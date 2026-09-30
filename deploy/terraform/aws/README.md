# AWS deployment (Terraform)

Creates a VPC (2 AZs, one NAT gateway), an EKS cluster with a 2-node managed node group, an
encrypted RDS PostgreSQL instance reachable only from the nodes, and ECR repositories.

> **This costs money every hour it exists.** The EKS control plane, the NAT gateway, two
> `m7i-flex.large` nodes and a small RDS instance come to roughly $0.35/hour (about $8/day) in
> us-east-1 at on-demand prices. Check current pricing before you apply. Deploy, verify, record
> the demo, then run `terraform destroy`.

```bash
# 1. Infrastructure (about 15-20 minutes)
cd deploy/terraform/aws
terraform init
terraform apply -var admin_cidr="$(curl -s https://checkip.amazonaws.com)/32"
cd -

# 2. Apps: copies CI's x86 images from GHCR to ECR, generates fresh secrets, deploys, waits
deploy/k8s/overlays/aws/up.sh            # IMAGE_TAG=<commit sha> to pin; defaults to origin/main

# 3. Verify
kubectl -n chartwise port-forward svc/encounter-service 8080:8080 &
kubectl -n chartwise port-forward svc/hapi 8090:8080 &
python3 scripts/smoke.py --password "$(cat deploy/k8s/overlays/aws/.demo-password)"

# 4. Tear down (do not skip this): deletes the apps and Kafka's EBS volume, runs
#    terraform destroy, then lists anything billable that is left (should print nothing)
deploy/k8s/overlays/aws/down.sh
```

Things this handles that are easy to get wrong:

- **Architecture.** Images built on an Apple Silicon Mac are arm64; the `t3` nodes are x86. `up.sh`
  copies CI's amd64 images instead of pushing local builds.
- **Storage.** EKS has no default StorageClass, so the overlay adds an encrypted `gp3` one.
  Otherwise Kafka's volume claim stays Pending.
- **Orphaned volumes.** StatefulSet volume claims outlive the StatefulSet, and `terraform destroy`
  doesn't know about volumes Kubernetes created. `down.sh` deletes the claims first.
- **Secrets.** Generated per deployment and stored only in the cluster Secret. Nothing from the
  kind dev values is reused. The LLM stays on the fake provider unless `LLM_PROVIDER=anthropic`
  and `ANTHROPIC_API_KEY` are set when running `up.sh`.
- **Account details stay out of git.** `render.sh` writes the RDS host and ECR registry into
  git-ignored files (`rds.env`, `overlays/aws-rendered/`).

Nothing is exposed publicly: the Kubernetes API accepts only `admin_cidr`, RDS accepts only the
nodes, and the apps are reached through `kubectl port-forward`.

[`deploy-eks.yml`](../../../.github/workflows/deploy-eks.yml) can deploy from GitHub Actions
instead, but it needs a GitHub OIDC role (`AWS_DEPLOY_ROLE_ARN`) that this Terraform does not
create.

Kafka, Redis and HAPI FHIR run inside the cluster. Amazon MSK would be the production choice for
Kafka, but it costs more per hour than everything else here combined.
