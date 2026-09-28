# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What Chartwise is

An AI clinical documentation pipeline. It takes a recorded patient visit (as a transcript), pulls the patient's history from HAPI FHIR, has an LLM draft a SOAP note, safety-checks the draft, queues it for clinician review, and writes the approved note back as a FHIR `DocumentReference`, with full version and audit history. It is a portfolio project: the distributed-systems guarantees and the measured failure results in `docs/results/` matter as much as the features. See README.md for the design rationale and results.

## Hard rules

- **Synthetic data only.** Patients come from Synthea (`tools/synthea`), and transcripts from `tools/transcripts/generate.py`. Never add real patient information to code, fixtures, prompts, logs or docs, including anything remembered from real clinical work.
- **No PHI in logs.** Log encounter IDs, counts and codes; never transcripts, notes, prompts, model output, patient names or medication names. Canary tests enforce this (`EncounterFlowIntegrationTest`, `tests/test_pipeline.py::test_no_phi_in_logs`, `server_test.go`). Exception messages that could reach logs or dead-letter headers must be written by us (see `WorkerError.safe_reason` and `FhirUnavailableException`), never forwarded from libraries.
- **All LLM calls go through the Go gateway.** The worker never calls the Claude API directly.
- **Every Kafka consumer is idempotent**, keyed on encounter ID. Record the key in `processed_message` in the same transaction as the effect, and only when the effect is applied (see `EncounterService.applyDraft` for the ordering and why).
- **Events are published only through the transactional outbox** (`OutboxWriter`, `Propagation.MANDATORY`). Outbox payloads carry IDs only; PHI is fetched through the internal API.
- **The audit log is append-only**, enforced by a Postgres trigger. Retention purges encounters, never audit rows.
- **AWS bills hourly.** Deploy only to verify and record, then `terraform destroy`. Say exactly what was deployed and for how long.
- **Report only what was actually run and measured.** Load, chaos and eval results use the gateway's fake LLM provider, and only `docs/results/live-*.json` used real Claude. Say which wherever a number is quoted.

## Architecture

```
review-app ──REST/JWT──▶ encounter-service ──▶ PostgreSQL (Flyway)          HAPI FHIR ◀── encounter-service
                          │ outbox relay ▲ consumes note.drafted, note.approved, chartwise.dead-letter
                          ▼              │
                 Kafka: encounter.created ──▶ note-worker ──gRPC──▶ llm-gateway ──▶ Claude (or fake provider)
                                                 │  ▲                   │
                  GET /internal/encounters/{id}/draft-context          Redis token bucket
```

- **Claim check:** `encounter.created` carries only IDs. The worker fetches transcript and patient context from `GET /internal/encounters/{id}/draft-context` (role `SERVICE`, user `note-worker`, audited). The context excludes name and identifiers.
- **Status machine** (`Encounter`): `DRAFTING → IN_REVIEW → APPROVED → FILED`, plus `DRAFTING → DRAFT_FAILED`. An admin retry re-emits the event through the outbox.
- **Note versions are immutable.** v1 is always the AI draft. Edits require `baseVersion` to equal the latest (409 otherwise). Approval names the exact version.
- **Dead letters** use Spring's header names (`kafka_dlt-original-topic`, `kafka_dlt-exception-fqcn`, `kafka_dlt-exception-message`) from both Java (`DeadLetterPublishingRecoverer`) and Python (`consumer.py`). `EventListeners.onDeadLetter` turns them into visible state.
- **FHIR filing** is a conditional create on identifier `urn:chartwise:encounter-note|{encounterId}` first, then `markFiled`, so a crash in between is safe.
- **Worker delivery** (`consumer.py`): up to `CONCURRENCY` drafts in flight. Per partition, commit up to the first unfinished offset after flushing the producer. `pause()` at capacity, `resume()` when a slot frees. Revoke waits for in-flight work and commits it. Never go back to batch-then-commit: with real latency variance it blocks on the slowest call. The worker retries the gateway for up to `GATEWAY_RETRY_BUDGET_SECONDS` (600), so an outage becomes backlog rather than failures.
- **Gateway order:** rate limit (Redis Lua, uses Redis `TIME`) → circuit breaker (per replica, checked per attempt) → provider call with retries. Returns `RESOURCE_EXHAUSTED` or `UNAVAILABLE` with a `retry-after-ms` trailer. `CHAOS_ENABLED=true` exposes `POST :8081/admin/chaos`.
- **Worker ↔ LLM prompt contract** is `soap-v1` in `note_worker/prompt.py`. The fake provider (`internal/provider/fake.go`) parses those prompt tags, so change both together.
- **Tracing:** the outbox stores the request's `traceparent` and the relay sends it as a Kafka header. The worker extracts it, and otelgrpc and Spring Kafka observation carry it on.
- **SLO:** `chartwise_note_draft_latency_seconds` (created → draft ready). Rules live in `deploy/compose/prometheus/rules.yml`. The 30 s target (`DRAFT_SLO`) is always one of the histogram buckets.

## Commands

```bash
make help                       # all targets
make up && make venv && make seed && make demo-data   # full local stack with data
make test                       # all suites (Docker required for Java and Python integration tests)

# encounter-service (Java 21, Spring Boot 4, Maven)
cd services/encounter-service && mvn -B verify
mvn -B test -Dtest=EncounterFlowIntegrationTest#fullFlowFromTranscriptToFiledNote_withoutLeakingPhiToLogs

# llm-gateway (Go)
cd services/llm-gateway && go test -race ./...
go test ./internal/breaker -run TestHalfOpenProbeClosesOnSuccess

# note-worker (Python; venv at services/note-worker/.venv)
cd services/note-worker && .venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_safety.py::test_brand_name_is_resolved_to_generic
.venv/bin/python -m pytest -m "not integration"      # skip the Testcontainers Kafka test

# review-app (React 19, TypeScript, Vite)
cd apps/review-app && npm run dev    # proxies /api to localhost:8080
npm test && npm run lint && npm run build
npx vitest run src/lib/diff.test.ts

scripts/gen-proto.sh            # after editing proto/; CI fails if stubs are stale
make eval | chaos-kill-worker | chaos-llm-outage | load WORKERS=3   # experiments -> docs/results
LLM_PROVIDER=anthropic docker compose up -d llm-gateway && python3 chaos/live_claude.py --count 20 --offset N --label X   # real Claude, costs money
make kind-up                    # kind cluster (needs `go install sigs.k8s.io/kind@latest`)
python3 scripts/smoke.py --api URL --fhir URL                        # end-to-end check anywhere
```

## Gotchas

- Spring Boot 4 uses **Jackson 3** (`tools.jackson.*`, unchecked exceptions) and modular starters (for example `spring-boot-starter-flyway` and `spring-boot-starter-webmvc-test`; `AutoConfigureMockMvc` is under `org.springframework.boot.webmvc.test.autoconfigure`). Testcontainers 2.x module artifacts are `testcontainers-postgresql` / `testcontainers-kafka`.
- Micrometer drops a `_created` suffix from Prometheus counter names (OpenMetrics reserves it), so don't name counters `*.created`.
- Postgres re-serialises `jsonb` (`"attempt": 2`, with a space). Parse outbox payloads; don't string-match them.
- Worker Prometheus counters reset on restart, so use Kafka offsets or DB state for exact experiment accounting (see `chaos/lib.py`).
- HAPI FHIR's image is distroless (no shell). Configure it with `SPRING_*` / `HAPI_FHIR_*` env vars.
- `compose.yaml` must not bind worker ports on the host, or `--scale note-worker=N` fails.
