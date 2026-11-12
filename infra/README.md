# Infrastructure (Terraform)

AWS account 271200701471, us-east-2. Two configurations:

- **`bootstrap/`**: the S3 bucket holding Terraform state (versioned, encrypted, TLS-only).
- **`prod/`**: everything the serving app needs:
  - **network:** VPC, public subnet, internet gateway, and a security group allowing 80/443 only (no SSH);
  - **data bucket:** `frl-271200701471-data`, with `serving/`, `deploy/` and `backups/`;
  - **ECR:** repository `frl`, with immutable tags, keeping the last 15 images;
  - **IAM:** the instance role, plus a GitHub OIDC deploy role usable only from this repo's `production` environment (matched by the repo's immutable subject, `owner@id/name@id`);
  - **SSM:** `/frl/prod/*` secret placeholders;
  - **Route 53:** the zone for `filings.stigmergylabs.net`;
  - **the server:** a t4g.medium (ARM, 4 GB) on Ubuntu 24.04 with a 30 GB encrypted disk,
    IMDSv2 only and no SSH, plus an Elastic IP and the A record. Cloud-init installs Docker,
    Compose and the AWS CLI and adds 2 GB of swap. Postgres lives on the root disk, so the
    instance ignores later AMI and user-data changes and has termination protection.

Approximate cost: **about $31/month** (instance $24.50, public IPv4 $3.65, disk $2.40, zone
$0.50; S3 and ECR are pennies). Without the instance, EIP and record it's about $0.50.

**The account must be on the AWS Paid plan.** The Free plan allows only micro and small
instance types, and closes the account when its credits or six months run out.

Nothing here is applied by CI. CI runs only `fmt -check` and `validate`, and every `apply` is
run by hand.

## Prerequisites

1. **An admin profile `frl-admin`** (`aws configure --profile frl-admin`). The Nucbox's default
   credentials are a least-privilege backup user and can't create any of this.
2. **Terraform 1.11 or later** (the lock files are made with 1.14.9). Or run it in Docker:
   `docker run --rm -it -v ~/.aws:/root/.aws:ro -v $PWD:/infra -w /infra/prod hashicorp/terraform:1.14 <cmd>`.
3. **The AWS budget raised** from `monthly-5usd-guard` to about $40. The instance (PR 8) costs
   about $31/month.

## First run

```sh
# 1. State bucket. Its backend can't exist before the bucket does: apply with local state,
#    then move the state into the bucket.
cd infra/bootstrap
mv backend.tf backend.tf.off
terraform init && terraform apply
mv backend.tf.off backend.tf
terraform init -migrate-state        # answer yes; then rm terraform.tfstate*

# 2. Production
cd ../prod
terraform init
terraform plan -out tfplan           # review it
terraform apply tfplan
terraform output name_servers
```

If the account already has GitHub's OIDC provider, the plan fails creating it. In that case, use
`-var create_github_oidc_provider=false`.

## After the first apply

- **Delegate the subdomain.** At the DNS host for `stigmergylabs.net`, add an NS record for
  `filings` with each of the four `name_servers`. Then check:
  `dig +short NS filings.stigmergylabs.net`.
- **Set the secrets.** Run this for each name in `ssm.tf` (`anthropic_api_key`, `voyage_api_key`,
  `langfuse_public_key`, `langfuse_secret_key`, `ip_hash_salt`, `postgres_password` and
  `reader_password`), plus `acme_email` with `--type String`:
  ```sh
  aws ssm put-parameter --profile frl-admin --name /frl/prod/<name> --type SecureString \
    --overwrite --value '...'
  ```
  Terraform wrote the placeholders through write-only attributes, so no value, placeholder or
  real, is ever in Terraform state. A later `apply` never overwrites what you set.
- **Set the smoke token** (random; `scripts/smoke_prod.sh` reads it back from SSM):
  ```sh
  aws ssm put-parameter --profile frl-admin --name /frl/prod/smoke_token --type SecureString \
    --overwrite --value "$(openssl rand -hex 32)"
  ```
- **Create the GitHub environment.** Add a `production` environment in the repo settings and
  limit its deployment branches to `main`. The deploy role trusts only that environment.
