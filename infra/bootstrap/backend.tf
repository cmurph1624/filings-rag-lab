# Rename this file away for the very first apply (the bucket doesn't exist yet), then back, and
# run `terraform init -migrate-state`. infra/README.md has the steps.
terraform {
  backend "s3" {
    bucket       = "frl-271200701471-tfstate"
    key          = "bootstrap/terraform.tfstate"
    region       = "us-east-2"
    profile      = "frl-admin"
    encrypt      = true
    use_lockfile = true
  }
}
