// Package ratelimit implements a distributed token bucket in Redis.
//
// The bucket state lives in Redis so every gateway replica draws from the same budget. The
// refill-and-take step runs as one Lua script, which Redis executes atomically, so concurrent
// requests can't both spend the last token. The clock is Redis's own TIME, so gateway replicas
// with skewed clocks still agree on how many tokens have refilled.
//
// The same bucket serves two budgets: requests (cost 1 each) and LLM tokens (cost = an estimate
// of the call's tokens, settled with Adjust once the real usage is known).
package ratelimit

import (
	"context"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

const script = `
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])      -- tokens per second
local cost = tonumber(ARGV[3])
local force = ARGV[4] == '1'        -- settle: subtract (or refund) unconditionally, debt allowed
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)

local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end

tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate / 1000)
local allowed = 0
local retry_ms = 0
if force then
  tokens = math.min(capacity, tokens - cost)
  allowed = 1
elseif tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry_ms = math.ceil((cost - tokens) * 1000 / rate)
end

redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity * 1000 / rate) + 1000)
return {allowed, retry_ms}
`

type Decision struct {
	Allowed    bool
	RetryAfter time.Duration
}

type TokenBucket struct {
	rdb      redis.UniversalClient
	script   *redis.Script
	capacity float64
	rate     float64
	prefix   string
}

// New creates a limiter allowing bursts of up to capacity requests, refilled at ratePerSecond.
func New(rdb redis.UniversalClient, capacity, ratePerSecond float64) *TokenBucket {
	return &TokenBucket{
		rdb:      rdb,
		script:   redis.NewScript(script),
		capacity: capacity,
		rate:     ratePerSecond,
		prefix:   "chartwise:ratelimit:",
	}
}

// WithPrefix returns a limiter over separate buckets, so one Redis can hold several budgets.
func (b *TokenBucket) WithPrefix(prefix string) *TokenBucket {
	c := *b
	c.prefix = prefix
	return &c
}

// Capacity is the most a single take can ever cost.
func (b *TokenBucket) Capacity() float64 { return b.capacity }

func (b *TokenBucket) Allow(ctx context.Context, key string) (Decision, error) {
	return b.AllowN(ctx, key, 1)
}

// AllowN takes cost tokens if the bucket holds them.
func (b *TokenBucket) AllowN(ctx context.Context, key string, cost float64) (Decision, error) {
	res, err := b.script.Run(ctx, b.rdb, []string{b.prefix + key}, b.capacity, b.rate, cost, 0).Int64Slice()
	if err != nil {
		return Decision{}, fmt.Errorf("token bucket: %w", err)
	}
	return Decision{Allowed: res[0] == 1, RetryAfter: time.Duration(res[1]) * time.Millisecond}, nil
}

// Adjust settles a reservation: a positive delta takes more (the bucket may go into debt, which
// later callers wait out), a negative one refunds, up to capacity.
func (b *TokenBucket) Adjust(ctx context.Context, key string, delta float64) error {
	if err := b.script.Run(ctx, b.rdb, []string{b.prefix + key}, b.capacity, b.rate, delta, 1).Err(); err != nil {
		return fmt.Errorf("token bucket: %w", err)
	}
	return nil
}
