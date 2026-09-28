package breaker

import (
	"testing"
	"time"
)

type clock struct{ t time.Time }

func (c *clock) now() time.Time          { return c.t }
func (c *clock) advance(d time.Duration) { c.t = c.t.Add(d) }

func newTestBreaker(c *clock, transitions *[]string) *Breaker {
	return New(Config{FailureThreshold: 3, OpenDuration: 10 * time.Second, HalfOpenProbes: 1}, c.now,
		func(from, to State) { *transitions = append(*transitions, from.String()+"->"+to.String()) })
}

func call(t *testing.T, b *Breaker, success bool) {
	t.Helper()
	done, err := b.Allow()
	if err != nil {
		t.Fatalf("expected call to be allowed, got %v", err)
	}
	done(success)
}

func TestOpensAfterConsecutiveFailures(t *testing.T) {
	c := &clock{t: time.Unix(0, 0)}
	var tr []string
	b := newTestBreaker(c, &tr)

	call(t, b, false)
	call(t, b, false)
	call(t, b, true) // success resets the streak
	call(t, b, false)
	call(t, b, false)
	if b.State() != Closed {
		t.Fatalf("breaker opened on non-consecutive failures")
	}
	call(t, b, false)
	if b.State() != Open {
		t.Fatalf("expected open after 3 consecutive failures, got %v", b.State())
	}

	_, err := b.Allow()
	var oe *OpenError
	if !IsOpen(err) {
		t.Fatalf("expected OpenError, got %v", err)
	}
	if oe = err.(*OpenError); oe.RetryAfter != 10*time.Second {
		t.Fatalf("expected retry-after 10s, got %v", oe.RetryAfter)
	}
}

func TestHalfOpenProbeClosesOnSuccess(t *testing.T) {
	c := &clock{t: time.Unix(0, 0)}
	var tr []string
	b := newTestBreaker(c, &tr)
	for i := 0; i < 3; i++ {
		call(t, b, false)
	}
	c.advance(10 * time.Second)

	done, err := b.Allow()
	if err != nil {
		t.Fatalf("probe should be allowed after cooldown: %v", err)
	}
	if _, err := b.Allow(); !IsOpen(err) {
		t.Fatalf("only one probe may run while half-open")
	}
	done(true)
	if b.State() != Closed {
		t.Fatalf("expected closed after successful probe, got %v", b.State())
	}
	want := []string{"closed->open", "open->half_open", "half_open->closed"}
	if len(tr) != len(want) {
		t.Fatalf("transitions = %v, want %v", tr, want)
	}
	for i := range want {
		if tr[i] != want[i] {
			t.Fatalf("transitions = %v, want %v", tr, want)
		}
	}
}

func TestHalfOpenProbeFailureReopens(t *testing.T) {
	c := &clock{t: time.Unix(0, 0)}
	var tr []string
	b := newTestBreaker(c, &tr)
	for i := 0; i < 3; i++ {
		call(t, b, false)
	}
	c.advance(10 * time.Second)
	call(t, b, false)
	if b.State() != Open {
		t.Fatalf("expected open after failed probe, got %v", b.State())
	}
	c.advance(9 * time.Second)
	if _, err := b.Allow(); !IsOpen(err) {
		t.Fatalf("cooldown should restart after a failed probe")
	}
}

func TestStaleOutcomesAreIgnored(t *testing.T) {
	c := &clock{t: time.Unix(0, 0)}
	var tr []string
	b := newTestBreaker(c, &tr)

	slow, _ := b.Allow() // started while closed
	for i := 0; i < 3; i++ {
		call(t, b, false)
	}
	c.advance(10 * time.Second)
	probe, _ := b.Allow()

	slow(true) // finishes late; must not close the circuit on the probe's behalf
	if b.State() != HalfOpen {
		t.Fatalf("stale success changed state to %v", b.State())
	}
	probe(false)
	if b.State() != Open {
		t.Fatalf("expected open, got %v", b.State())
	}
}
