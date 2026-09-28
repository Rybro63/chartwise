// Package ratelimit implements a distributed token bucket in Redis.
//
// The bucket state lives in Redis so every gateway replica draws from the same budget. The
// refill-and-take step runs as one Lua script, which Redis executes atomically, so concurrent
// requests can't both spend the last token. The clock is Redis's own TIME, so gateway replicas
// with skewed clocks still agree on how many tokens have refilled.
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
if tokens >= cost then
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

func (b *TokenBucket) Allow(ctx context.Context, key string) (Decision, error) {
	res, err := b.script.Run(ctx, b.rdb, []string{b.prefix + key}, b.capacity, b.rate, 1).Int64Slice()
	if err != nil {
		return Decision{}, fmt.Errorf("token bucket: %w", err)
	}
	return Decision{Allowed: res[0] == 1, RetryAfter: time.Duration(res[1]) * time.Millisecond}, nil
}
