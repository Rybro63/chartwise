package provider

import (
	"context"
	"math/rand/v2"
	"sync"
	"time"
)

// ChaosSettings controls injected faults. Mode is one of:
//   - "none":       pass through
//   - "outage":     every call fails with a retryable 503 (provider down)
//   - "error_rate": a fraction of calls fail with 529 overloaded
//   - "latency":    calls are delayed by ExtraLatency
type ChaosSettings struct {
	Mode         string        `json:"mode"`
	ErrorRate    float64       `json:"errorRate"`
	ExtraLatency time.Duration `json:"extraLatency"`
	Until        time.Time     `json:"until"`
}

// Chaos wraps a provider with switchable fault injection, driven by the gateway's admin endpoint
// during chaos experiments.
type Chaos struct {
	inner Provider
	mu    sync.RWMutex
	s     ChaosSettings
}

func NewChaos(inner Provider) *Chaos {
	return &Chaos{inner: inner, s: ChaosSettings{Mode: "none"}}
}

func (c *Chaos) Name() string { return c.inner.Name() }

func (c *Chaos) Set(s ChaosSettings) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.s = s
}

func (c *Chaos) Get() ChaosSettings {
	c.mu.RLock()
	defer c.mu.RUnlock()
	s := c.s
	if s.Mode != "none" && !s.Until.IsZero() && time.Now().After(s.Until) {
		s = ChaosSettings{Mode: "none"}
	}
	return s
}

func (c *Chaos) Complete(ctx context.Context, req Request) (Response, error) {
	s := c.Get()
	switch s.Mode {
	case "outage":
		select {
		case <-time.After(50 * time.Millisecond):
		case <-ctx.Done():
		}
		return Response{}, &Error{Retryable: true, Status: 503, Reason: "chaos: provider outage"}
	case "error_rate":
		if rand.Float64() < s.ErrorRate {
			return Response{}, &Error{Retryable: true, Status: 529, Reason: "chaos: provider overloaded"}
		}
	case "latency":
		select {
		case <-time.After(s.ExtraLatency):
		case <-ctx.Done():
			return Response{}, &Error{Retryable: true, Status: 504, Reason: "provider call timed out"}
		}
	}
	return c.inner.Complete(ctx, req)
}
