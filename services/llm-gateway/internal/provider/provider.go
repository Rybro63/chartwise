// Package provider abstracts the LLM backend behind the gateway.
package provider

import (
	"context"
	"errors"
	"fmt"
	"time"
)

type Request struct {
	System     string
	User       string
	JSONSchema string // optional; when set the response text is JSON matching it
	MaxTokens  int
}

type Response struct {
	Text         string
	Model        string
	StopReason   string
	InputTokens  int64
	OutputTokens int64
}

// Error describes a provider failure. Retryable errors (rate limits, overload, 5xx, timeouts)
// count against the circuit breaker; permanent ones (bad request, refusal) do not, because they
// say nothing about whether the provider is healthy.
type Error struct {
	Retryable  bool
	Status     int
	Reason     string
	RetryAfter time.Duration
}

func (e *Error) Error() string {
	return fmt.Sprintf("provider error (status %d, retryable=%t): %s", e.Status, e.Retryable, e.Reason)
}

func AsError(err error) (*Error, bool) {
	var pe *Error
	ok := errors.As(err, &pe)
	return pe, ok
}

type Provider interface {
	Complete(ctx context.Context, req Request) (Response, error)
	Name() string
}
