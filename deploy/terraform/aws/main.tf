data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

# ---- Network -------------------------------------------------------------------------------
# Two AZs, private subnets for nodes and the database, public subnets for load balancers.
# One NAT gateway (not one per AZ) to keep the demo's hourly cost down.
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.0"

  name            = var.name
  cidr            = "10.40.0.0/16"
  azs             = local.azs
  private_subnets = ["10.40.1.0/24", "10.40.2.0/24"]
  public_subnets  = ["10.40.101.0/24", "10.40.102.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true

  public_subnet_tags  = { "kubernetes.io/role/elb" = 1 }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = 1 }
}

# ---- Kubernetes ----------------------------------------------------------------------------
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.0"

  name               = var.name
  kubernetes_version = var.kubernetes_version
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = module.vpc.private_subnets

  endpoint_public_access       = true
  endpoint_public_access_cidrs = [var.admin_cidr]

  enable_cluster_creator_admin_permissions = true

  addons = {
    coredns                = {}
    kube-proxy             = {}
    vpc-cni                = { before_compute = true }
    eks-pod-identity-agent = { before_compute = true }
    aws-ebs-csi-driver     = { pod_identity_association = [{ role_arn = aws_iam_role.ebs_csi.arn, service_account = "ebs-csi-controller-sa" }] }
  }

  eks_managed_node_groups = {
    default = {
      instance_types = [var.node_instance_type]
      min_size       = var.node_count
      max_size       = var.node_count + 1
      desired_size   = var.node_count
    }
  }
}

# The EBS CSI driver provisions volumes for the in-cluster Kafka StatefulSet.
resource "aws_iam_role" "ebs_csi" {
  name = "${var.name}-ebs-csi"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

# ---- PostgreSQL (RDS) ----------------------------------------------------------------------
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "this" {
  name       = var.name
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "db" {
  name        = "${var.name}-db"
  description = "Postgres reachable only from EKS nodes"
  vpc_id      = module.vpc.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_nodes" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = module.eks.node_security_group_id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_db_instance" "this" {
  identifier     = var.name
  engine         = "postgres"
  engine_version = "17"
  instance_class = var.db_instance_class

  db_name  = "chartwise"
  username = "chartwise"
  password = random_password.db.result

  allocated_storage      = 20
  storage_encrypted      = true # PHI at rest, even though ours is synthetic
  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false

  backup_retention_period = 1
  deletion_protection     = false # demo: must be destroyable in one command
  skip_final_snapshot     = true
  apply_immediately       = true
}

# ---- Container registry --------------------------------------------------------------------
resource "aws_ecr_repository" "service" {
  for_each             = toset(["encounter-service", "llm-gateway", "note-worker", "review-app"])
  name                 = "${var.name}/${each.key}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration {
    scan_on_push = true
  }
}

data "aws_caller_identity" "current" {}
