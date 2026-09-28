package server

import (
	"bytes"
	"context"
	"log/slog"
	"net"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	llmv1 "github.com/chartwise/llm-gateway/gen/chartwise/llm/v1"
	"github.com/chartwise/llm-gateway/internal/breaker"
	"github.com/chartwise/llm-gateway/internal/metrics"
	"github.com/chartwise/llm-gateway/internal/provider"
	"github.com/chartwise/llm-gateway/internal/ratelimit"
	"github.com/chartwise/llm-gateway/internal/retry"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"
)

const phiCanary = "PHI-CANARY-PROMPT-93ab"

// scripted returns the queued results in order, then succeeds.
type scripted struct {
	calls   atomic.Int32
	results []error
}

func (p *scripted) Name() string { return "scripted" }
func (p *scripted) Complete(ctx context.Context, req provider.Request) (provider.Response, error) {
	n := int(p.calls.Add(1))
	if n <= len(p.results) && p.results[n-1] != nil {
		return provider.Response{}, p.results[n-1]
	}
	return provider.Response{Text: `{"ok":true}`, Model: "test-model", StopReason: "end_turn", InputTokens: 10, OutputTokens: 5}, nil
}

type limiter struct{ deny bool }

func (l limiter) Allow(context.Context, string) (ratelimit.Decision, error) {
	if l.deny {
		return ratelimit.Decision{Allowed: false, RetryAfter: 750 * time.Millisecond}, nil
	}
	return ratelimit.Decision{Allowed: true}, nil
}

var transient = &provider.Error{Retryable: true, Status: 529, Reason: "overloaded"}

func start(t *testing.T, p provider.Provider, l Limiter, threshold int) (llmv1.LlmGatewayClient, *bytes.Buffer) {
	t.Helper()
	logs := &bytes.Buffer{}
	srv := &Server{
		Provider:       p,
		Limiter:        l,
		Breaker:        breaker.New(breaker.Config{FailureThreshold: threshold, OpenDuration: time.Minute, HalfOpenProbes: 1}, nil, nil),
		Retry:          retry.Policy{MaxAttempts: 3, Base: time.Millisecond, Max: time.Millisecond},
		AttemptTimeout: time.Second,
		Metrics:        metrics.New(),
		Log:            slog.New(slog.NewJSONHandler(logs, nil)),
		Sleep:          SleepContext,
	}
	lis := bufconn.Listen(1 << 20)
	gs := grpc.NewServer()
	llmv1.RegisterLlmGatewayServer(gs, srv)
	go func() { _ = gs.Serve(lis) }()
	t.Cleanup(gs.Stop)

	conn, err := grpc.NewClient("passthrough:///bufnet",
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) { return lis.DialContext(ctx) }),
		grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	return llmv1.NewLlmGatewayClient(conn), logs
}

func request() *llmv1.CompleteRequest {
	return &llmv1.CompleteRequest{ClientId: "note-worker", RequestId: "enc-1", SystemPrompt: "sys", UserPrompt: "transcript " + phiCanary}
}

func TestRetriesTransientErrorsThenSucceeds(t *testing.T) {
	p := &scripted{results: []error{transient, transient}}
	client, logs := start(t, p, limiter{}, 10)

	resp, err := client.Complete(context.Background(), request())
	if err != nil {
		t.Fatalf("expected success after retries: %v", err)
	}
	if resp.GetAttempts() != 3 || p.calls.Load() != 3 {
		t.Fatalf("attempts = %d, calls = %d, want 3", resp.GetAttempts(), p.calls.Load())
	}
	if strings.Contains(logs.String(), phiCanary) {
		t.Fatalf("prompt content leaked into logs: %s", logs.String())
	}
}

func TestPermanentErrorsAreNotRetried(t *testing.T) {
	p := &scripted{results: []error{&provider.Error{Retryable: false, Status: 400, Reason: "bad request"}}}
	client, _ := start(t, p, limiter{}, 10)

	_, err := client.Complete(context.Background(), request())
	if status.Code(err) != codes.FailedPrecondition {
		t.Fatalf("code = %v, want FailedPrecondition", status.Code(err))
	}
	if p.calls.Load() != 1 {
		t.Fatalf("permanent error was retried (%d calls)", p.calls.Load())
	}
}

func TestCircuitOpensAndFailsFast(t *testing.T) {
	p := &scripted{results: []error{transient, transient, transient, transient, transient, transient}}
	client, _ := start(t, p, limiter{}, 3)

	// First request: three retryable failures trip the breaker (threshold 3).
	if _, err := client.Complete(context.Background(), request()); status.Code(err) != codes.Unavailable {
		t.Fatalf("code = %v, want Unavailable", status.Code(err))
	}
	callsBefore := p.calls.Load()

	// Second request: rejected by the open circuit without touching the provider.
	var trailer metadata.MD
	_, err := client.Complete(context.Background(), request(), grpc.Trailer(&trailer))
	if status.Code(err) != codes.Unavailable || !strings.Contains(status.Convert(err).Message(), "circuit open") {
		t.Fatalf("expected fast circuit-open failure, got %v", err)
	}
	if p.calls.Load() != callsBefore {
		t.Fatalf("open circuit still called the provider")
	}
	if len(trailer.Get(RetryAfterKey)) != 1 {
		t.Fatalf("missing %s trailer", RetryAfterKey)
	}
}

func TestRateLimitedRequestsNeverReachTheProvider(t *testing.T) {
	p := &scripted{}
	client, _ := start(t, p, limiter{deny: true}, 3)

	var trailer metadata.MD
	_, err := client.Complete(context.Background(), request(), grpc.Trailer(&trailer))
	if status.Code(err) != codes.ResourceExhausted {
		t.Fatalf("code = %v, want ResourceExhausted", status.Code(err))
	}
	if got := trailer.Get(RetryAfterKey); len(got) != 1 || got[0] != "750" {
		t.Fatalf("retry-after trailer = %v, want [750]", got)
	}
	if p.calls.Load() != 0 {
		t.Fatalf("rate-limited request reached the provider")
	}
}

func TestFakeProviderProducesWorkerSchema(t *testing.T) {
	f := &provider.Fake{}
	resp, err := f.Complete(context.Background(), provider.Request{User: "<transcript>\nDoctor: BP is 150/90.\nPatient: I take my lisinopril daily.\n</transcript>\n<active_conditions>\n- Hypertension\n</active_conditions>\n<active_medications>\n- lisinopril 10 MG Oral Tablet\n</active_medications>"})
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{`"subjective"`, `"plan"`, `"medications_mentioned"`, "lisinopril"} {
		if !strings.Contains(resp.Text, want) {
			t.Fatalf("fake response missing %s: %s", want, resp.Text)
		}
	}
}
