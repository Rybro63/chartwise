#!/usr/bin/env bash
# Fills rds.env and the ECR image names from Terraform outputs.
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
cd "$HERE"
for svc in encounter-service llm-gateway note-worker review-app; do
  kustomize edit set image "chartwise/$svc=$REGISTRY/chartwise/$svc:$TAG"
done
echo "rendered for $HOST, images $REGISTRY/chartwise/*:$TAG"
