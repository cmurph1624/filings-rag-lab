# One data bucket, by prefix:
#   serving/  index dumps + manifests from the Nucbox (scripts/serving_dump.sh)
#   deploy/   deploy bundles (compose file, Caddyfile, scripts) per commit
#   backups/  daily frl_app dumps from the instance (put-only for the instance)

resource "aws_s3_bucket" "data" {
  bucket = local.data_bucket
  lifecycle {
    prevent_destroy = true # backups/ holds the only data that can't be rebuilt
  }
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "data" {
  bucket = aws_s3_bucket.data.id
  # S3 can refuse the two at once on a new bucket (OperationAborted).
  depends_on = [aws_s3_bucket_public_access_block.data]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "TLSOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "backups-90-days"
    status = "Enabled"
    filter {
      prefix = "backups/"
    }
    expiration {
      days = 90
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  rule {
    id     = "deploy-bundles-30-days"
    status = "Enabled"
    filter {
      prefix = "deploy/"
    }
    expiration {
      days = 30
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }

  rule {
    # Dumps are replaced by newer ones; keep replaced versions a while for a rollback.
    id     = "serving-old-versions"
    status = "Enabled"
    filter {
      prefix = "serving/"
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 2
    }
  }
}
