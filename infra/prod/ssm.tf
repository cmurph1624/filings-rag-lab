# Secret placeholders. Terraform creates each parameter once with a placeholder written through
# a write-only attribute, so no value (placeholder or real) is ever kept in state. The real
# values are set by hand, never through Terraform:
#   aws ssm put-parameter --profile frl-admin --name /frl/prod/<name> --type SecureString \
#     --overwrite --value '...'
# The deploy renders them into /run/frl/env (tmpfs) on the instance.

locals {
  secrets = toset([
    "anthropic_api_key",
    "voyage_api_key",
    "langfuse_public_key",
    "langfuse_secret_key",
    "ip_hash_salt",
    "postgres_password",
    "reader_password",
    "smoke_token", # sent as X-Smoke-Token, it keeps the smoke test out of the stats
  ])
}

resource "aws_ssm_parameter" "secret" {
  for_each         = local.secrets
  name             = "/frl/prod/${each.key}"
  type             = "SecureString"
  value_wo         = "placeholder"
  value_wo_version = 1
  description      = "Set by hand with put-parameter --overwrite; see infra/prod/ssm.tf"
  # SSM can't change a description without rewriting the value, and the value Terraform has
  # is the placeholder: never "fix" a description edited outside Terraform.
  lifecycle {
    ignore_changes = [description]
  }
}

# Not secret: the email Let's Encrypt writes to about expiring certificates.
resource "aws_ssm_parameter" "acme_email" {
  name  = "/frl/prod/acme_email"
  type  = "String"
  value = "placeholder@example.com"
  lifecycle {
    ignore_changes = [value]
  }
}
