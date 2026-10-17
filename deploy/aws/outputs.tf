output "site_url" {
  value = "https://${var.domain_name}"
}

output "load_balancer_dns_name" {
  description = "Without Route 53: point your domain here (CNAME, or ALIAS/ANAME at the apex)."
  value       = aws_lb.main.dns_name
}

output "certificate_validation" {
  description = "Without Route 53: add this CNAME at your DNS provider so the HTTPS certificate is issued."
  value = [for option in aws_acm_certificate.site.domain_validation_options : {
    name  = option.resource_record_name
    type  = option.resource_record_type
    value = option.resource_record_value
  }]
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "bucket" {
  value = aws_s3_bucket.data.bucket
}

output "models_s3_uri" {
  value = "s3://${aws_s3_bucket.data.bucket}/models/"
}

output "pdfs_s3_uri" {
  value = "s3://${aws_s3_bucket.data.bucket}/pdfs/"
}

output "admin_secret_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "admin_token_command" {
  description = "Prints ADMIN_TOKEN for the admin app."
  value       = "aws secretsmanager get-secret-value --region ${var.region} --secret-id ${aws_secretsmanager_secret.app.arn} --query SecretString --output text | jq -r .ADMIN_TOKEN"
}

output "push_image_commands" {
  value = join("\n", [
    "aws ecr get-login-password --region ${var.region} | docker login --username AWS --password-stdin ${split("/", aws_ecr_repository.api.repository_url)[0]}",
    "docker build -t ${aws_ecr_repository.api.repository_url}:latest .",
    "docker push ${aws_ecr_repository.api.repository_url}:latest",
  ])
}

output "elasticsearch_url" {
  value = local.es_url
}

output "log_group" {
  value = aws_cloudwatch_log_group.app.name
}
