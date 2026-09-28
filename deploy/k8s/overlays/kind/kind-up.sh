#!/usr/bin/env bash
# Creates a local kind cluster, loads the locally built images and deploys Chartwise.
#   deploy/k8s/overlays/kind/kind-up.sh          # then: kubectl -n chartwise port-forward svc/review-app 3000:80
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
KIND="${KIND:-kind}"

if ! "$KIND" get clusters | grep -qx chartwise; then
  "$KIND" create cluster --config "$HERE/kind-config.yaml"
fi

if [[ "${SKIP_BUILD:-}" != "1" ]]; then
  docker compose -f "$ROOT/compose.yaml" build encounter-service llm-gateway note-worker review-app
fi
for img in chartwise-encounter-service chartwise-llm-gateway chartwise-note-worker chartwise-review-app; do
  "$KIND" load docker-image "$img:latest" --name chartwise
done

kubectl apply -k "$HERE"
kubectl -n chartwise wait --for=condition=complete job/kafka-topics --timeout=300s
for d in hapi encounter-service llm-gateway note-worker review-app; do
  kubectl -n chartwise rollout status deploy/$d --timeout=600s
done
echo "Chartwise is up. Try: kubectl -n chartwise port-forward svc/review-app 3000:80"
