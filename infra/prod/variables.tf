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
