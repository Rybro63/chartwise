package retry

import (
	"testing"
	"time"
)

func TestBackoffStaysWithinExponentialCeiling(t *testing.T) {
	p := Policy{MaxAttempts: 5, Base: 100 * time.Millisecond, Max: time.Second}
	ceilings := []time.Duration{100 * time.Millisecond, 200 * time.Millisecond, 400 * time.Millisecond, 800 * time.Millisecond, time.Second, time.Second}
	for i, ceiling := range ceilings {
		for j := 0; j < 200; j++ {
			if d := p.Backoff(i + 1); d < 0 || d > ceiling {
				t.Fatalf("retry %d: backoff %v outside [0, %v]", i+1, d, ceiling)
			}
		}
	}
}

func TestBackoffDoesNotOverflowOnLargeAttempts(t *testing.T) {
	p := Policy{Base: time.Second, Max: 5 * time.Second}
	if d := p.Backoff(200); d < 0 || d > 5*time.Second {
		t.Fatalf("backoff %v overflowed", d)
	}
}
