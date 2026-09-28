# Chartwise developer commands. Run `make help`.
PY ?= services/note-worker/.venv/bin/python

.PHONY: help
help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

# ---- local stack ----
up: ## Build and start the full local stack (docker compose)
	docker compose up -d --build
down: ## Stop the stack (keeps data volumes)
	docker compose down
reset: ## Stop the stack and delete all data
	docker compose down -v

seed: ## Generate Synthea patients, load them into HAPI FHIR, generate transcripts
	tools/synthea/generate.sh 25 42
	tools/synthea/load.sh
	python3 tools/transcripts/generate.py --per-patient 8

demo-data: ## Create 20 encounters from generated transcripts
	python3 tools/transcripts/seed_encounters.py --count 20

smoke: ## End-to-end smoke test against the running stack
	python3 scripts/smoke.py

# ---- tests ----
test: test-java test-go test-python test-web ## Run every service's tests
test-java: ## Encounter service (Testcontainers: needs Docker)
	cd services/encounter-service && mvn -B verify
test-go: ## LLM gateway
	cd services/llm-gateway && go vet ./... && go test -race ./...
test-python: ## Note worker (includes the Kafka crash test)
	cd services/note-worker && .venv/bin/python -m pytest -q
test-web: ## Review app
	cd apps/review-app && npm run lint && npm test && npm run build

venv: ## Create the note worker's virtualenv
	cd services/note-worker && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

proto: ## Regenerate gRPC stubs (Go + Python)
	scripts/gen-proto.sh

# ---- experiments (stack must be running and seeded) ----
eval: ## Safety-check mutation eval -> docs/results/safety-eval.*
	cd services/note-worker && .venv/bin/python eval/run_safety_eval.py
chaos-kill-worker: ## SIGKILL the worker mid-batch; verify nothing lost or duplicated
	cd chaos && ../$(PY) kill_worker.py --count 200
chaos-llm-outage: ## 60 s LLM outage under load; breaker opens, backlog drains
	cd chaos && ../$(PY) llm_outage.py
load: ## k6 ramp to PEAK_RATE encounters/s, then chart SLO metrics (WORKERS=n replicas)
	docker compose up -d --scale note-worker=$${WORKERS:-1} note-worker
	@start=$$(date +%s); \
	docker run --rm --network chartwise_default -v "$$PWD":/work -w /work grafana/k6:1.3.0 \
	  run -e API=http://encounter-service:8080 -e PEAK_RATE=$${PEAK_RATE:-8} -e STAGE=$${STAGE:-1m} loadtest/k6/drafting.js; \
	sleep 60; end=$$(date +%s); \
	$(PY) loadtest/report.py --start $$start --end $$end --name load-$${WORKERS:-1}-worker

# ---- kubernetes ----
kind-up: ## Create a kind cluster and deploy Chartwise into it
	deploy/k8s/overlays/kind/kind-up.sh
kind-down: ## Delete the kind cluster
	kind delete cluster --name chartwise
