#!/usr/bin/env bash
# Regenerates gRPC stubs for the gateway (Go) and the note worker (Python) from proto/.
# Requires: protoc-gen-go and protoc-gen-go-grpc on PATH, and grpcio-tools in the worker venv.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$ROOT/services/note-worker/.venv/bin/python}"
export PATH="$PATH:$(go env GOPATH)/bin"

GO_OUT="$ROOT/services/llm-gateway/gen"
PY_OUT="$ROOT/services/note-worker/src"
mkdir -p "$GO_OUT" "$PY_OUT"

"$PY" -m grpc_tools.protoc -I "$ROOT/proto" \
  --go_out="$GO_OUT" --go_opt=paths=source_relative \
  --go-grpc_out="$GO_OUT" --go-grpc_opt=paths=source_relative \
  --python_out="$PY_OUT" --pyi_out="$PY_OUT" --grpc_python_out="$PY_OUT" \
  "$ROOT/proto/chartwise/llm/v1/gateway.proto"

echo "generated: $GO_OUT/chartwise/llm/v1, $PY_OUT/chartwise/llm/v1"
