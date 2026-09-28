// Command gateway runs the Chartwise LLM gateway: a gRPC front door to the LLM provider that
// enforces rate limits, retries and circuit breaking.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	llmv1 "github.com/chartwise/llm-gateway/gen/chartwise/llm/v1"
	"github.com/chartwise/llm-gateway/internal/breaker"
	"github.com/chartwise/llm-gateway/internal/metrics"
	"github.com/chartwise/llm-gateway/internal/provider"
	"github.com/chartwise/llm-gateway/internal/ratelimit"
	"github.com/chartwise/llm-gateway/internal/retry"
	"github.com/chartwise/llm-gateway/internal/server"
	"github.com/chartwise/llm-gateway/internal/telemetry"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil)).With("service", "llm-gateway")
	if err := run(log); err != nil {
		log.Error("gateway exited", "error", err.Error())
		os.Exit(1)
	}
}

func run(log *slog.Logger) error {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	shutdownTracing, err := telemetry.Init(ctx, "llm-gateway")
	if err != nil {
		return err
	}
	defer func() { _ = shutdownTracing(context.Background()) }()

	m := metrics.New()

	var base provider.Provider
	switch env("LLM_PROVIDER", "fake") {
	case "anthropic":
		if os.Getenv("ANTHROPIC_API_KEY") == "" {
			log.Warn("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty; the SDK will look for other credentials")
		}
		base = provider.NewAnthropic(os.Getenv("ANTHROPIC_API_KEY"), env("LLM_MODEL", "claude-opus-5"), os.Getenv("LLM_EFFORT"))
	case "fake":
		base = &provider.Fake{
			Latency:           duration("FAKE_LATENCY", 800*time.Millisecond),
			Jitter:            duration("FAKE_JITTER", 400*time.Millisecond),
			HallucinationRate: float("FAKE_HALLUCINATION_RATE", 0),
		}
	default:
		return errors.New("LLM_PROVIDER must be anthropic or fake")
	}
	chaos := provider.NewChaos(base)

	rdb := redis.NewClient(&redis.Options{Addr: env("REDIS_ADDR", "localhost:6379")})
	defer rdb.Close()

	br := breaker.New(breaker.Config{
		FailureThreshold: integer("BREAKER_FAILURE_THRESHOLD", 5),
		OpenDuration:     duration("BREAKER_OPEN_DURATION", 15*time.Second),
		HalfOpenProbes:   integer("BREAKER_HALF_OPEN_PROBES", 1),
	}, nil, func(from, to breaker.State) {
		log.Warn("circuit breaker transition", "from", from.String(), "to", to.String())
		m.BreakerChanges.WithLabelValues(from.String(), to.String()).Inc()
		m.BreakerState.Set(float64(to))
	})

	srv := &server.Server{
		Provider: chaos,
		Limiter:  ratelimit.New(rdb, float("RATE_LIMIT_BURST", 20), float("RATE_LIMIT_PER_SECOND", 10)),
		Breaker:  br,
		Retry: retry.Policy{
			MaxAttempts: integer("RETRY_MAX_ATTEMPTS", 4),
			Base:        duration("RETRY_BASE_DELAY", 250*time.Millisecond),
			Max:         duration("RETRY_MAX_DELAY", 5*time.Second),
		},
		AttemptTimeout: duration("PROVIDER_TIMEOUT", 120*time.Second),
		Metrics:        m,
		Log:            log,
		Sleep:          server.SleepContext,
	}

	gs := grpc.NewServer(grpc.StatsHandler(otelgrpc.NewServerHandler()))
	llmv1.RegisterLlmGatewayServer(gs, srv)
	hs := health.NewServer()
	healthpb.RegisterHealthServer(gs, hs)

	lis, err := net.Listen("tcp", env("GRPC_ADDR", ":50051"))
	if err != nil {
		return err
	}

	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.HandlerFor(m.Registry, promhttp.HandlerOpts{}))
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok", "circuit": br.State().String()})
	})
	if env("CHAOS_ENABLED", "false") == "true" {
		mux.HandleFunc("/admin/chaos", chaosHandler(chaos, log))
		log.Warn("chaos admin endpoint enabled")
	}
	httpSrv := &http.Server{Addr: env("HTTP_ADDR", ":8081"), Handler: mux, ReadHeaderTimeout: 5 * time.Second}

	errs := make(chan error, 2)
	go func() { errs <- gs.Serve(lis) }()
	go func() {
		if err := httpSrv.ListenAndServe(); !errors.Is(err, http.ErrServerClosed) {
			errs <- err
		}
	}()
	// Refresh the breaker gauge so it reflects open -> half-open transitions that happen lazily.
	go func() {
		t := time.NewTicker(time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				m.BreakerState.Set(float64(br.State()))
			}
		}
	}()
	log.Info("llm gateway listening", "grpc", lis.Addr().String(), "http", httpSrv.Addr, "provider", base.Name())

	select {
	case <-ctx.Done():
	case err := <-errs:
		return err
	}
	log.Info("shutting down")
	hs.Shutdown()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	_ = httpSrv.Shutdown(shutdownCtx)
	stopped := make(chan struct{})
	go func() { gs.GracefulStop(); close(stopped) }()
	select {
	case <-stopped:
	case <-shutdownCtx.Done():
		gs.Stop()
	}
	return nil
}

// chaosHandler lets chaos experiments switch fault injection on and off:
//
//	curl -XPOST localhost:8081/admin/chaos -d '{"mode":"outage","durationSeconds":60}'
func chaosHandler(c *provider.Chaos, log *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if token := os.Getenv("ADMIN_TOKEN"); token != "" && r.Header.Get("Authorization") != "Bearer "+token {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		if r.Method == http.MethodPost {
			var body struct {
				Mode            string  `json:"mode"`
				ErrorRate       float64 `json:"errorRate"`
				ExtraLatencyMs  int     `json:"extraLatencyMs"`
				DurationSeconds int     `json:"durationSeconds"`
			}
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				http.Error(w, "invalid JSON", http.StatusBadRequest)
				return
			}
			switch body.Mode {
			case "none", "outage", "error_rate", "latency":
			default:
				http.Error(w, "mode must be none, outage, error_rate or latency", http.StatusBadRequest)
				return
			}
			s := provider.ChaosSettings{Mode: body.Mode, ErrorRate: body.ErrorRate, ExtraLatency: time.Duration(body.ExtraLatencyMs) * time.Millisecond}
			if body.DurationSeconds > 0 {
				s.Until = time.Now().Add(time.Duration(body.DurationSeconds) * time.Second)
			}
			c.Set(s)
			log.Warn("chaos settings changed", "mode", s.Mode, "error_rate", s.ErrorRate, "until", s.Until)
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(c.Get())
	}
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func duration(key string, def time.Duration) time.Duration {
	if d, err := time.ParseDuration(os.Getenv(key)); err == nil {
		return d
	}
	return def
}

func integer(key string, def int) int {
	if n, err := strconv.Atoi(os.Getenv(key)); err == nil {
		return n
	}
	return def
}

func float(key string, def float64) float64 {
	if f, err := strconv.ParseFloat(os.Getenv(key), 64); err == nil {
		return f
	}
	return def
}
