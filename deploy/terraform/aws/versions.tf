terraform {
  required_version = ">= 1.9"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # Local state by default. For anything shared, configure an S3 backend with locking.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "chartwise"
      ManagedBy = "terraform"
      # Everything here bills hourly. Tear it down: terraform destroy
      Ephemeral = "true"
    }
  }
}
