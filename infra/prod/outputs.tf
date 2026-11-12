output "name_servers" {
  description = "Add these as NS records for the domain at the parent's DNS host"
  value       = aws_route53_zone.app.name_servers
}

output "zone_id" {
  value = aws_route53_zone.app.zone_id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "data_bucket" {
  value = aws_s3_bucket.data.bucket
}

output "gha_deploy_role_arn" {
  description = "For the deploy workflow's aws-actions/configure-aws-credentials role-to-assume"
  value       = aws_iam_role.gha_deploy.arn
}

output "instance_profile" {
  value = aws_iam_instance_profile.instance.name
}

output "subnet_id" {
  value = aws_subnet.public.id
}

output "security_group_id" {
  value = aws_security_group.web.id
}

output "instance_id" {
  value = aws_instance.app.id
}

output "public_ip" {
  value = aws_eip.app.public_ip
}
