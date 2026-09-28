// Package breaker implements a consecutive-failure circuit breaker.
//
//	closed ──(N consecutive failures)──▶ open ──(cooldown elapsed)──▶ half-open
//	  ▲                                    ▲                              │
//	  └───────────(probe succeeds)─────────┼──────────────────────────────┤
//	                                       └───────(probe fails)──────────┘
//
// While open, calls fail immediately instead of queueing behind a provider that is down. That
// protects the provider while it recovers and gives callers a fast, explicit signal to back off.
package breaker

import (
	"errors"
	"sync"
	"time"
)

type State int

const (
	Closed State = iota
	HalfOpen
	Open
)

func (s State) String() string {
	switch s {
	case Closed:
		return "closed"
	case HalfOpen:
		return "half_open"
	default:
		return "open"
	}
}

// OpenError is returned by Allow while the breaker is rejecting calls.
type OpenError struct {
	RetryAfter time.Duration
}

func (e *OpenError) Error() string { return "circuit breaker open" }

// IsOpen reports whether err came from an open breaker.
func IsOpen(err error) bool {
	var oe *OpenError
	return errors.As(err, &oe)
}

type Config struct {
	// FailureThreshold is the number of consecutive failures that opens the circuit.
	FailureThreshold int
	// OpenDuration is how long the circuit stays open before letting a probe through.
	OpenDuration time.Duration
	// HalfOpenProbes is how many concurrent trial calls are allowed while half-open.
	HalfOpenProbes int
}

type Breaker struct {
	cfg      Config
	now      func() time.Time
	onChange func(from, to State)

	mu         sync.Mutex
	state      State
	failures   int
	openedAt   time.Time
	probes     int
	generation uint64
}

func New(cfg Config, now func() time.Time, onChange func(from, to State)) *Breaker {
	if cfg.FailureThreshold < 1 {
		cfg.FailureThreshold = 1
	}
	if cfg.HalfOpenProbes < 1 {
		cfg.HalfOpenProbes = 1
	}
	if now == nil {
		now = time.Now
	}
	if onChange == nil {
		onChange = func(State, State) {}
	}
	return &Breaker{cfg: cfg, now: now, onChange: onChange}
}

// Allow asks permission to make one call. On success it returns a done func that must be called
// exactly once with the call's outcome.
func (b *Breaker) Allow() (done func(success bool), err error) {
	b.mu.Lock()
	defer b.mu.Unlock()

	if b.state == Open {
		remaining := b.cfg.OpenDuration - b.now().Sub(b.openedAt)
		if remaining > 0 {
			return nil, &OpenError{RetryAfter: remaining}
		}
		b.transition(HalfOpen)
	}
	if b.state == HalfOpen {
		if b.probes >= b.cfg.HalfOpenProbes {
			return nil, &OpenError{RetryAfter: time.Second}
		}
		b.probes++
	}
	gen := b.generation
	var once sync.Once
	return func(success bool) {
		once.Do(func() { b.record(gen, success) })
	}, nil
}

func (b *Breaker) record(gen uint64, success bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if gen != b.generation {
		// The call started under a previous state (e.g. before the circuit opened). Its outcome
		// says nothing about the provider's health now, so ignore it.
		return
	}
	switch b.state {
	case Closed:
		if success {
			b.failures = 0
			return
		}
		b.failures++
		if b.failures >= b.cfg.FailureThreshold {
			b.trip()
		}
	case HalfOpen:
		b.probes--
		if success {
			b.transition(Closed)
		} else {
			b.trip()
		}
	}
}

func (b *Breaker) trip() {
	b.openedAt = b.now()
	b.transition(Open)
}

func (b *Breaker) transition(to State) {
	from := b.state
	if from == to {
		return
	}
	b.state = to
	b.failures = 0
	b.probes = 0
	b.generation++
	b.onChange(from, to)
}

// State returns the current state, advancing open -> half-open if the cooldown has elapsed.
func (b *Breaker) State() State {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.state == Open && b.now().Sub(b.openedAt) >= b.cfg.OpenDuration {
		return HalfOpen
	}
	return b.state
}
