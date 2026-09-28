// Package server implements the LlmGateway gRPC service.
//
// Every request passes three gates, in order:
//  1. Admission control: a per-client token bucket in Redis. Over budget -> RESOURCE_EXHAUSTED
//     with a retry-after hint, before any provider capacity is spent.
//  2. Circuit breaker: if the provider has been failing, fail fast with UNAVAILABLE instead of
//     piling more requests onto it.
//  3. Retries: transient provider errors are retried with jittered exponential backoff, within the
//     caller's deadline, each attempt reported to the breaker.
package server

import (
	"context"
	"errors"
	"log/slog"
	"strconv"
	"time"

	llmv1 "github.com/chartwise/llm-gateway/gen/chartwise/llm/v1"
	"github.com/chartwise/llm-gateway/internal/breaker"
	"github.com/chartwise/llm-gateway/internal/metrics"
	"github.com/chartwise/llm-gateway/internal/provider"
	"github.com/chartwise/llm-gateway/internal/ratelimit"
	"github.com/chartwise/llm-gateway/internal/retry"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// RetryAfterKey is the trailer carrying how long the caller should wait, in milliseconds.
const RetryAfterKey = "retry-after-ms"

type Limiter interface {
	Allow(ctx context.Context, key string) (ratelimit.Decision, error)
}

type Server struct {
	llmv1.UnimplementedLlmGatewayServer

	Provider       provider.Provider
	Limiter        Limiter
	Breaker        *breaker.Breaker
	Retry          retry.Policy
	AttemptTimeout time.Duration
	Metrics        *metrics.Metrics
	Log            *slog.Logger
	// Sleep waits between retries; replaceable in tests.
	Sleep func(ctx context.Context, d time.Duration) error
}

func (s *Server) Complete(ctx context.Context, req *llmv1.CompleteRequest) (*llmv1.CompleteResponse, error) {
	start := time.Now()
	s.Metrics.InFlight.Inc()
	defer s.Metrics.InFlight.Dec()

	resp, outcome, attempts, err := s.complete(ctx, req)

	s.Metrics.Requests.WithLabelValues(req.GetClientId(), outcome).Inc()
	s.Metrics.Duration.WithLabelValues(outcome).Observe(time.Since(start).Seconds())
	// Log identifiers and outcomes only. Prompts and completions contain PHI and are never logged.
	s.Log.Info("complete",
		"request_id", req.GetRequestId(),
		"client_id", req.GetClientId(),
		"outcome", outcome,
		"attempts", attempts,
		"duration_ms", time.Since(start).Milliseconds(),
	)
	return resp, err
}

func (s *Server) complete(ctx context.Context, req *llmv1.CompleteRequest) (*llmv1.CompleteResponse, string, int, error) {
	if req.GetClientId() == "" || req.GetUserPrompt() == "" {
		return nil, "invalid", 0, status.Error(codes.InvalidArgument, "client_id and user_prompt are required")
	}

	// 1. Admission control.
	decision, err := s.Limiter.Allow(ctx, req.GetClientId())
	if err != nil {
		// Fail open: an unavailable Redis should degrade rate limiting, not take down drafting.
		// The breaker still protects the provider.
		s.Metrics.RateLimitErrors.Inc()
		s.Log.Warn("rate limiter unavailable; admitting request", "request_id", req.GetRequestId(), "error", err.Error())
	} else if !decision.Allowed {
		setRetryAfter(ctx, decision.RetryAfter)
		return nil, "rate_limited", 0, status.Errorf(codes.ResourceExhausted, "rate limit exceeded for client %q", req.GetClientId())
	}

	preq := provider.Request{
		System:     req.GetSystemPrompt(),
		User:       req.GetUserPrompt(),
		JSONSchema: req.GetJsonSchema(),
		MaxTokens:  int(req.GetMaxTokens()),
	}

	var lastErr error
	for attempt := 1; attempt <= s.Retry.MaxAttempts; attempt++ {
		// 2. Circuit breaker, checked per attempt so a retry loop stops as soon as it opens.
		done, err := s.Breaker.Allow()
		if err != nil {
			var oe *breaker.OpenError
			errors.As(err, &oe)
			setRetryAfter(ctx, oe.RetryAfter)
			return nil, "circuit_open", attempt - 1, status.Error(codes.Unavailable, "LLM provider circuit open")
		}

		// 3. The provider call itself, bounded by a per-attempt timeout.
		attemptCtx, cancel := context.WithTimeout(ctx, s.AttemptTimeout)
		resp, err := s.Provider.Complete(attemptCtx, preq)
		cancel()

		if err == nil {
			done(true)
			s.Metrics.Attempts.WithLabelValues(s.Provider.Name(), "ok").Inc()
			s.Metrics.Tokens.WithLabelValues("input").Add(float64(resp.InputTokens))
			s.Metrics.Tokens.WithLabelValues("output").Add(float64(resp.OutputTokens))
			return &llmv1.CompleteResponse{
				Text:         resp.Text,
				Model:        resp.Model,
				StopReason:   resp.StopReason,
				InputTokens:  resp.InputTokens,
				OutputTokens: resp.OutputTokens,
				Attempts:     int32(attempt),
			}, "ok", attempt, nil
		}

		pe, ok := provider.AsError(err)
		if !ok {
			pe = &provider.Error{Retryable: true, Status: 500, Reason: err.Error()}
		}
		if !pe.Retryable {
			// A bad request or a refusal is not evidence the provider is unhealthy.
			done(true)
			s.Metrics.Attempts.WithLabelValues(s.Provider.Name(), "permanent_error").Inc()
			return nil, "provider_error", attempt, status.Errorf(codes.FailedPrecondition, "provider rejected request: %s", pe.Reason)
		}
		done(false)
		s.Metrics.Attempts.WithLabelValues(s.Provider.Name(), "retryable_error").Inc()
		lastErr = pe

		if attempt == s.Retry.MaxAttempts || ctx.Err() != nil {
			break
		}
		wait := s.Retry.Backoff(attempt)
		if pe.RetryAfter > wait {
			wait = pe.RetryAfter
		}
		if deadline, ok := ctx.Deadline(); ok && time.Until(deadline) < wait {
			break // no time left for another attempt
		}
		if err := s.Sleep(ctx, wait); err != nil {
			break
		}
	}

	if ctx.Err() != nil {
		return nil, "provider_error", s.Retry.MaxAttempts, status.Error(codes.DeadlineExceeded, "deadline exceeded while retrying provider")
	}
	return nil, "provider_error", s.Retry.MaxAttempts, status.Errorf(codes.Unavailable, "provider failed after retries: %v", lastErr)
}

func setRetryAfter(ctx context.Context, d time.Duration) {
	if d <= 0 {
		return
	}
	_ = grpc.SetTrailer(ctx, metadata.Pairs(RetryAfterKey, strconv.FormatInt(d.Milliseconds(), 10)))
}

// SleepContext waits for d or until ctx is cancelled.
func SleepContext(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
