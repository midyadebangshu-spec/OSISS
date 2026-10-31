resource "random_password" "admin_token" {
  length  = 48
  special = false
}

resource "random_password" "db" {
  length  = 32
  special = false
}

# Read at boot by every instance (never written into user data). The admin app needs ADMIN_TOKEN:
#   aws secretsmanager get-secret-value --secret-id <output admin_secret_arn> --query SecretString --output text
resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name}/app"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    ADMIN_TOKEN           = random_password.admin_token.result
    POSTGRES_PASSWORD     = random_password.db.result
    ELASTICSEARCH_API_KEY = var.elasticsearch_mode == "elastic_cloud" ? var.elastic_cloud_api_key : ""
  })
}
