// Package retry holds the gateway's backoff policy.
package retry

import (
	"math/rand/v2"
	"time"
)

type Policy struct {
	MaxAttempts int
	Base        time.Duration
	Max         time.Duration
}

// Backoff returns the wait before retry number n (1-based) using "full jitter": a uniform random
// duration in [0, min(Max, Base*2^(n-1))]. Jitter spreads retries from many callers out so they
// don't hit a recovering provider in synchronized waves.
func (p Policy) Backoff(n int) time.Duration {
	if n < 1 {
		n = 1
	}
	ceiling := p.Base << (n - 1)
	if ceiling <= 0 || ceiling > p.Max {
		ceiling = p.Max
	}
	return time.Duration(rand.Int64N(int64(ceiling) + 1))
}
