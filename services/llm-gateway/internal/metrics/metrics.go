// Package metrics defines the gateway's Prometheus metrics.
package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
)

type Metrics struct {
	Registry        *prometheus.Registry
	Requests        *prometheus.CounterVec
	Attempts        *prometheus.CounterVec
	Duration        *prometheus.HistogramVec
	BreakerState    prometheus.Gauge
	BreakerChanges  *prometheus.CounterVec
	Tokens          *prometheus.CounterVec
	RateLimitErrors prometheus.Counter
	InFlight        prometheus.Gauge
}

func New() *Metrics {
	reg := prometheus.NewRegistry()
	reg.MustRegister(collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	m := &Metrics{
		Registry: reg,
		Requests: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "llm_gateway_requests_total",
			Help: "Complete requests by outcome (ok, rate_limited, circuit_open, provider_error, invalid).",
		}, []string{"client", "outcome"}),
		Attempts: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "llm_gateway_provider_attempts_total",
			Help: "Individual provider calls, including retries.",
		}, []string{"provider", "result"}),
		Duration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "llm_gateway_request_duration_seconds",
			Help:    "End-to-end Complete latency including retries.",
			Buckets: []float64{0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120},
		}, []string{"outcome"}),
		BreakerState: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "llm_gateway_circuit_state",
			Help: "Circuit breaker state: 0 closed, 1 half-open, 2 open.",
		}),
		BreakerChanges: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "llm_gateway_circuit_transitions_total",
			Help: "Circuit breaker state transitions.",
		}, []string{"from", "to"}),
		Tokens: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "llm_gateway_tokens_total",
			Help: "LLM tokens consumed.",
		}, []string{"type"}),
		RateLimitErrors: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "llm_gateway_ratelimit_backend_errors_total",
			Help: "Redis errors while checking the rate limit (requests are admitted when this happens).",
		}),
		InFlight: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "llm_gateway_inflight_requests",
			Help: "Complete requests currently being processed.",
		}),
	}
	reg.MustRegister(m.Requests, m.Attempts, m.Duration, m.BreakerState, m.BreakerChanges, m.Tokens, m.RateLimitErrors, m.InFlight)
	return m
}
