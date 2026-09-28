package ratelimit

import (
	"context"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
)

func setup(t *testing.T) (*miniredis.Miniredis, *TokenBucket) {
	t.Helper()
	mr := miniredis.RunT(t)
	mr.SetTime(time.Unix(1_700_000_000, 0))
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	t.Cleanup(func() { _ = rdb.Close() })
	return mr, New(rdb, 3, 1) // burst of 3, one token per second
}

func TestBurstThenReject(t *testing.T) {
	_, tb := setup(t)
	ctx := context.Background()
	for i := 0; i < 3; i++ {
		d, err := tb.Allow(ctx, "worker")
		if err != nil || !d.Allowed {
			t.Fatalf("request %d: allowed=%v err=%v", i, d.Allowed, err)
		}
	}
	d, err := tb.Allow(ctx, "worker")
	if err != nil {
		t.Fatal(err)
	}
	if d.Allowed {
		t.Fatalf("4th request in the same instant should be rejected")
	}
	if d.RetryAfter <= 0 || d.RetryAfter > time.Second {
		t.Fatalf("retry-after = %v, want (0, 1s]", d.RetryAfter)
	}
}

func TestRefillsOverTime(t *testing.T) {
	mr, tb := setup(t)
	ctx := context.Background()
	for i := 0; i < 3; i++ {
		_, _ = tb.Allow(ctx, "worker")
	}
	mr.SetTime(time.Unix(1_700_000_002, 0)) // two seconds later: two tokens
	for i := 0; i < 2; i++ {
		if d, _ := tb.Allow(ctx, "worker"); !d.Allowed {
			t.Fatalf("refilled request %d rejected", i)
		}
	}
	if d, _ := tb.Allow(ctx, "worker"); d.Allowed {
		t.Fatalf("bucket should be empty again")
	}
}

func TestBucketsArePerClient(t *testing.T) {
	_, tb := setup(t)
	ctx := context.Background()
	for i := 0; i < 3; i++ {
		_, _ = tb.Allow(ctx, "worker")
	}
	if d, _ := tb.Allow(ctx, "other-client"); !d.Allowed {
		t.Fatalf("one client's usage must not drain another's bucket")
	}
}
