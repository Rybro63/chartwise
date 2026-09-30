#!/usr/bin/env bash
# Tears down everything up.sh and terraform created, then checks nothing billable is left.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
TF="$ROOT/deploy/terraform/aws"
REGION=$(terraform -chdir="$TF" output -raw ecr_registry | cut -d. -f4)

if kubectl -n chartwise get ns chartwise >/dev/null 2>&1; then
  kubectl delete -k "$HERE/../aws-rendered" --ignore-not-found --wait=true || true
  # StatefulSet volume claims survive their StatefulSet; delete them so the EBS volumes go too.
  kubectl -n chartwise delete pvc --all --wait=true || true
  kubectl delete namespace chartwise --ignore-not-found --wait=true || true
  for _ in $(seq 1 60); do
    [ -z "$(kubectl get pv -o name 2>/dev/null)" ] && break
    sleep 5
  done
fi

terraform -chdir="$TF" destroy -auto-approve -var admin_cidr=0.0.0.0/32

echo "Leftover check (all should be empty):"
aws ec2 describe-volumes --region "$REGION" --filters Name=tag:KubernetesCluster,Values=chartwise \
  --query 'Volumes[].VolumeId' --output text
aws ec2 describe-volumes --region "$REGION" --filters Name=tag-key,Values=kubernetes.io/cluster/chartwise \
  --query 'Volumes[].VolumeId' --output text
aws eks list-clusters --region "$REGION" --query 'clusters[?@==`chartwise`]' --output text
aws rds describe-db-instances --region "$REGION" --query 'DBInstances[?DBInstanceIdentifier==`chartwise`].DBInstanceIdentifier' --output text
aws ec2 describe-nat-gateways --region "$REGION" --filter Name=tag:Project,Values=chartwise Name=state,Values=available,pending \
  --query 'NatGateways[].NatGatewayId' --output text
aws ec2 describe-addresses --region "$REGION" --filters Name=tag:Project,Values=chartwise --query 'Addresses[].AllocationId' --output text
rm -f "$HERE/.demo-password"
