variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  type    = string
  default = "chartwise"
}

variable "kubernetes_version" {
  type    = string
  default = "1.34"
}

variable "node_instance_type" {
  description = "Worker node size: 2 vCPU / 8 GiB fits the whole demo on two nodes. m7i-flex.large is allowed on AWS free-plan accounts (t3.large is not) and costs about the same."
  type        = string
  default     = "m7i-flex.large"
}

variable "node_count" {
  type    = number
  default = 2
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "admin_cidr" {
  description = "CIDR allowed to reach the EKS public API endpoint (your IP /32)."
  type        = string
}
