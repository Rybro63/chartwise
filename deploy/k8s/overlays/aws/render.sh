#!/usr/bin/env bash
# Fills rds.env and writes ../aws-rendered/kustomization.yaml (ECR image names) from Terraform outputs.
# Both are git-ignored, so the account ID and database host stay out of the repo.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TF="$HERE/../../../terraform/aws"
TAG="${IMAGE_TAG:-latest}"
HOST=$(terraform -chdir="$TF" output -raw db_address)
REGISTRY=$(terraform -chdir="$TF" output -raw ecr_registry)
cat > "$HERE/rds.env" <<ENV
DB_HOST=$HOST
DB_URL=jdbc:postgresql://$HOST:5432/chartwise?sslmode=require
HAPI_DB_URL=jdbc:postgresql://$HOST:5432/hapi?sslmode=require
ENV
OUT="$HERE/../aws-rendered"
mkdir -p "$OUT"
{
  echo "apiVersion: kustomize.config.k8s.io/v1beta1"
  echo "kind: Kustomization"
  echo "resources: [../aws]"
  echo "images:"
  for svc in encounter-service llm-gateway note-worker review-app; do
    echo "  - { name: ECR_REGISTRY/chartwise/$svc, newName: $REGISTRY/chartwise/$svc, newTag: \"$TAG\" }"
  done
} > "$OUT/kustomization.yaml"
echo "rendered images $REGISTRY/chartwise/*:$TAG"
