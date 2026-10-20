// Package server implements the LlmGateway gRPC service.
//
// Every request passes three gates, in order:
//  1. Admission control: per-client token buckets in Redis, one counting requests and one counting
//     LLM tokens (providers limit both). Over budget -> RESOURCE_EXHAUSTED with a retry-after
//     hint, before any provider capacity is spent.
//  2. Circuit breaker: if the provider has been failing, fail fast with UNAVAILABLE instead of
//     piling more requests onto it.
//  3. Retries: transient provider errors are retried with jittered exponential backoff, within the
//     caller's deadline, each attempt reported to the breaker.
package server

import (
	"context"
	"errors"
	"log/slog"
	"math"
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

// TokenLimiter budgets LLM tokens: a call reserves an estimate up front and settles the
// difference once the provider reports what it really used.
type TokenLimiter interface {
	AllowN(ctx context.Context, key string, cost float64) (ratelimit.Decision, error)
	Adjust(ctx context.Context, key string, delta float64) error
	Capacity() float64
}

// charsPerToken is a deliberately rough estimate for English clinical text; the reservation is
// corrected with the real count after the call, so it only has to be in the right range.
const charsPerToken = 4

type Server struct {
	llmv1.UnimplementedLlmGatewayServer

	Provider provider.Provider
	// AllowedModels are the models a caller may request by name. A request naming any other
	// model is rejected; one naming none gets the provider's default.
	AllowedModels map[string]bool
	Limiter       Limiter
	// TokenLimiter is optional; nil means only requests are counted.
	TokenLimiter TokenLimiter
	// ExpectedOutputTokens is reserved for the response before its size is known.
	ExpectedOutputTokens int
	Breaker              *breaker.Breaker
	Retry                retry.Policy
	AttemptTimeout       time.Duration
	Metrics              *metrics.Metrics
	Log                  *slog.Logger
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
	if m := req.GetModel(); m != "" && !s.AllowedModels[m] {
		return nil, "invalid", 0, status.Errorf(codes.InvalidArgument, "model %q is not allowed by this gateway", m)
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
	reserved, err := s.reserveTokens(ctx, req)
	if err != nil {
		return nil, "token_limited", 0, err
	}
	used := 0.0
	defer func() { s.settleTokens(ctx, req, used-reserved) }()

	preq := provider.Request{
		Model:      req.GetModel(),
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
			used = float64(resp.InputTokens + resp.OutputTokens)
			done(true)
			s.Metrics.Attempts.WithLabelValues(s.Provider.Name(), "ok").Inc()
			s.Metrics.Tokens.WithLabelValues("input", resp.Model, req.GetClientId()).Add(float64(resp.InputTokens))
			s.Metrics.Tokens.WithLabelValues("output", resp.Model, req.GetClientId()).Add(float64(resp.OutputTokens))
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

// reserveTokens takes an estimate of the call's tokens from the client's token budget. Like the
// request limiter it fails open if Redis is unavailable.
func (s *Server) reserveTokens(ctx context.Context, req *llmv1.CompleteRequest) (float64, error) {
	if s.TokenLimiter == nil {
		return 0, nil
	}
	chars := len(req.GetSystemPrompt()) + len(req.GetUserPrompt()) + len(req.GetJsonSchema())
	// Never more than the bucket holds, or a large prompt could never be admitted.
	estimate := math.Min(float64(chars/charsPerToken+s.ExpectedOutputTokens), s.TokenLimiter.Capacity())
	decision, err := s.TokenLimiter.AllowN(ctx, req.GetClientId(), estimate)
	if err != nil {
		s.Metrics.RateLimitErrors.Inc()
		s.Log.Warn("token limiter unavailable; admitting request", "request_id", req.GetRequestId(), "error", err.Error())
		return 0, nil
	}
	if !decision.Allowed {
		setRetryAfter(ctx, decision.RetryAfter)
		return 0, status.Errorf(codes.ResourceExhausted, "token budget exceeded for client %q", req.GetClientId())
	}
	return estimate, nil
}

// settleTokens corrects the reservation with what the provider reported: a call that used more
// puts the client in debt, a failed call (nothing used) refunds the whole reservation.
func (s *Server) settleTokens(ctx context.Context, req *llmv1.CompleteRequest, delta float64) {
	if s.TokenLimiter == nil || delta == 0 {
		return
	}
	// The caller's deadline may already have passed; settling must still happen.
	settleCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Second)
	defer cancel()
	if err := s.TokenLimiter.Adjust(settleCtx, req.GetClientId(), delta); err != nil {
		s.Metrics.RateLimitErrors.Inc()
		s.Log.Warn("could not settle token budget", "request_id", req.GetRequestId(), "error", err.Error())
	}
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
