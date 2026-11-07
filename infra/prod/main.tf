# Production for the Filings RAG Lab serving app (P4). Everything here is cheap and always-on:
# network, data bucket, image registry, IAM (instance + GitHub deploy role), SSM secret
# placeholders and the DNS zone. The instance itself comes with the deploy (P4 PR 8).

terraform {
  required_version = ">= 1.11" # write-only attributes (SSM placeholders)
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  backend "s3" {
    bucket       = "frl-271200701471-tfstate"
    key          = "prod/terraform.tfstate"
    region       = "us-east-2"
    profile      = "frl-admin"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile
  default_tags {
    tags = { project = "frl", managed-by = "terraform", stack = "prod" }
  }
}

data "aws_caller_identity" "current" {}

check "account" {
  assert {
    condition     = data.aws_caller_identity.current.account_id == local.account_id
    error_message = "This configuration is for AWS account 271200701471."
  }
}

locals {
  account_id  = "271200701471"
  name        = "frl"
  data_bucket = "frl-${local.account_id}-data"
}
