# AWS deployment (Terraform)

Creates a VPC (2 AZs, one NAT gateway), an EKS cluster with a 2-node managed node group, an
encrypted RDS PostgreSQL instance reachable only from the nodes, and ECR repositories.

> **This costs money every hour it exists.** The EKS control plane, the NAT gateway, two
> `t3.large` nodes and a small RDS instance come to roughly $0.35/hour (about $8/day) in
> us-east-1 at on-demand prices. Check current pricing before you apply. Deploy, verify, record
> the demo, then run `terraform destroy`.

```bash
cd deploy/terraform/aws
terraform init
terraform apply -var admin_cidr="$(curl -s https://checkip.amazonaws.com)/32"
$(terraform output -raw kubeconfig_command)

# Push images (from the repo root)
REG=$(terraform -chdir=deploy/terraform/aws output -raw ecr_registry)
aws ecr get-login-password | docker login --username AWS --password-stdin "$REG"
for s in encounter-service llm-gateway note-worker review-app; do
  docker tag chartwise-$s:latest "$REG/chartwise/$s:latest" && docker push "$REG/chartwise/$s:latest"
done

# Deploy
cd deploy/k8s/overlays/aws && ./render.sh
kubectl create namespace chartwise
kubectl -n chartwise create secret generic chartwise-secrets --from-env-file=secrets.env
kubectl apply -k .

# Tear down (do not skip this)
kubectl delete -k deploy/k8s/overlays/aws
terraform -chdir=deploy/terraform/aws destroy
```

Kafka, Redis and HAPI FHIR run inside the cluster. Amazon MSK would be the production choice for
Kafka, but it costs more per hour than everything else here combined.
