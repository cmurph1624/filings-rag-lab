# The server: one ARM instance running the Compose stack (deploy/compose.prod.yaml), with a
# fixed address and the A record for var.domain. No SSH: it's reached through SSM only.
#
# Postgres lives on the root volume, so the instance must not be replaced by accident: the AMI
# and user data are ignored after creation, and API termination is disabled (a deliberate
# teardown turns that off first).

data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id"
}

resource "aws_instance" "app" {
  ami                     = data.aws_ssm_parameter.ubuntu.insecure_value
  instance_type           = var.instance_type
  subnet_id               = aws_subnet.public.id
  vpc_security_group_ids  = [aws_security_group.web.id]
  iam_instance_profile    = aws_iam_instance_profile.instance.name
  disable_api_termination = true

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    region = var.region
    ecr    = aws_ecr_repository.api.repository_url
    bucket = aws_s3_bucket.data.bucket
    domain = var.domain
  })

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 1          # the host's AWS CLI uses it; containers don't
  }

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
    encrypted   = true
  }

  # Standard credits: throttled under sustained load instead of billed for it.
  credit_specification {
    cpu_credits = "standard"
  }

  tags = { Name = local.name, app = local.name } # app=frl: the deploy role's SendCommand scope

  lifecycle {
    ignore_changes = [ami, user_data]
  }
}

resource "aws_eip" "app" {
  domain   = "vpc"
  instance = aws_instance.app.id
  tags     = { Name = local.name }
}

resource "aws_route53_record" "app" {
  zone_id = aws_route53_zone.app.zone_id
  name    = var.domain
  type    = "A"
  ttl     = 300
  records = [aws_eip.app.public_ip]
}
