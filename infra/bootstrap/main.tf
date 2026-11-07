# The S3 bucket that holds Terraform state for infra/prod (and, after the first apply, this
# configuration's own state). See infra/README.md for the two-step first run.

terraform {
  required_version = ">= 1.11"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "aws_profile" {
  description = "Local AWS profile with admin rights (the default credentials can't create these)"
  type        = string
  default     = "frl-admin"
}

provider "aws" {
  region  = "us-east-2"
  profile = var.aws_profile
  default_tags {
    tags = { project = "frl", managed-by = "terraform", stack = "bootstrap" }
  }
}

data "aws_caller_identity" "current" {}

check "account" {
  assert {
    condition     = data.aws_caller_identity.current.account_id == "271200701471"
    error_message = "This configuration is for AWS account 271200701471."
  }
}

resource "aws_s3_bucket" "state" {
  bucket = "frl-271200701471-tfstate"
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "old-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days           = 90
      newer_noncurrent_versions = 20
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  # S3 can refuse the two at once on a new bucket (OperationAborted).
  depends_on = [aws_s3_bucket_public_access_block.state]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "TLSOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}
