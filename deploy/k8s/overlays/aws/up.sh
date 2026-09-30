#!/usr/bin/env bash
# Deploys Chartwise to the EKS cluster created by deploy/terraform/aws.
#
# Images: copies the x86 images CI pushed to GHCR (IMAGE_TAG, default origin/main, the last
# commit CI built) into
# ECR. Images built on an Apple Silicon Mac are arm64 and won't run on the t3 nodes.
# Secrets: generated fresh here (never the kind dev values) and kept only in the cluster, except
# the demo login password, which is written to the git-ignored .demo-password for the smoke test.
# The LLM stays on the fake provider unless LLM_PROVIDER=anthropic and ANTHROPIC_API_KEY are set.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
TF="$ROOT/deploy/terraform/aws"
OWNER="${GHCR_OWNER:-rybro63}"
export IMAGE_TAG="${IMAGE_TAG:-$(git -C "$ROOT" rev-parse origin/main)}"

$(terraform -chdir="$TF" output -raw kubeconfig_command)
REGISTRY=$(terraform -chdir="$TF" output -raw ecr_registry)
REGION=$(echo "$REGISTRY" | cut -d. -f4)

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
for s in encounter-service llm-gateway note-worker review-app; do
  src="ghcr.io/$OWNER/chartwise-$s:$IMAGE_TAG"
  docker pull --platform linux/amd64 "$src"
  docker tag "$src" "$REGISTRY/chartwise/$s:$IMAGE_TAG"
  docker push "$REGISTRY/chartwise/$s:$IMAGE_TAG"
done

"$HERE/render.sh"

kubectl apply -f "$ROOT/deploy/k8s/base/namespace.yaml"
if ! kubectl -n chartwise get secret chartwise-secrets >/dev/null 2>&1; then
  umask 077
  DEMO_PASSWORD=$(openssl rand -hex 12)
  echo "$DEMO_PASSWORD" > "$HERE/.demo-password"
  kubectl -n chartwise create secret generic chartwise-secrets \
    --from-literal=DB_USER=chartwise \
    --from-literal=DB_PASSWORD="$(terraform -chdir="$TF" output -raw db_password)" \
    --from-literal=JWT_SECRET="$(openssl rand -base64 48)" \
    --from-literal=ENCRYPTION_KEY="$(openssl rand -base64 32)" \
    --from-literal=SEED_PASSWORD="$DEMO_PASSWORD" \
    --from-literal=WORKER_PASSWORD="$(openssl rand -hex 16)" \
    ${ANTHROPIC_API_KEY:+--from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"}
fi

kubectl apply -k "$HERE/../aws-rendered"
if [[ "${LLM_PROVIDER:-fake}" != "fake" ]]; then
  kubectl -n chartwise set env deploy/llm-gateway LLM_PROVIDER="$LLM_PROVIDER"
fi
kubectl -n chartwise wait --for=condition=complete job/db-init --timeout=300s
kubectl -n chartwise wait --for=condition=complete job/kafka-topics --timeout=600s
for d in hapi encounter-service llm-gateway note-worker review-app; do
  kubectl -n chartwise rollout status deploy/$d --timeout=900s
done
kubectl -n chartwise get pods -o wide
echo "Chartwise is up on EKS. Smoke test:"
echo "  kubectl -n chartwise port-forward svc/encounter-service 8080:8080 &"
echo "  kubectl -n chartwise port-forward svc/hapi 8090:8080 &"
echo "  python3 scripts/smoke.py --password \"\$(cat $HERE/.demo-password)\""
