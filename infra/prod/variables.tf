variable "region" {
  type    = string
  default = "us-east-2"
}

variable "aws_profile" {
  description = "Local AWS profile with admin rights (the default credentials can't create these)"
  type        = string
  default     = "frl-admin"
}

variable "domain" {
  description = "Public hostname; its own Route 53 zone, delegated from the parent's DNS host"
  type        = string
  default     = "filings.stigmergylabs.net"
}

variable "github_repo" {
  description = "owner/name allowed to assume the deploy role"
  type        = string
  default     = "cmurph1624/filings-rag-lab"
}

variable "github_environment" {
  description = "GitHub environment the deploy jobs run in (limit it to main in the repo settings)"
  type        = string
  default     = "production"
}

variable "create_github_oidc_provider" {
  description = "false if the account already has the token.actions.githubusercontent.com provider"
  type        = bool
  default     = true
}

variable "instance_type" {
  description = "The server (4 GB RAM is the Compose stack's budget: Postgres 1 GB buffers, api 1 GB)"
  type        = string
  default     = "t4g.medium"
}
