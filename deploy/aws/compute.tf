data "aws_ssm_parameter" "gpu_ami" {
  name = var.gpu_ami_ssm_parameter
}

data "aws_ami" "gpu" {
  owners = ["amazon"]
  filter {
    name   = "image-id"
    values = [nonsensitive(data.aws_ssm_parameter.gpu_ami.value)]
  }
}

data "aws_ami" "al2023" {
  owners = ["amazon"]
  filter {
    name   = "image-id"
    values = [nonsensitive(data.aws_ssm_parameter.al2023.value)]
  }
}

locals {
  image    = var.container_image != "" ? var.container_image : "${aws_ecr_repository.api.repository_url}:latest"
  registry = split("/", local.image)[0]
  es_url   = var.elasticsearch_mode == "self_managed" ? "http://${aws_instance.es[0].private_ip}:9200" : var.elastic_cloud_url

  # Settings shared by every copy (secrets come from Secrets Manager at boot, never from here).
  app_env = merge(
    {
      STATE_BACKEND       = "postgres"
      POSTGRES_HOST       = aws_db_instance.main.address
      POSTGRES_PORT       = tostring(aws_db_instance.main.port)
      POSTGRES_DB         = "osiss"
      POSTGRES_USER       = "osiss"
      POSTGRES_SSLMODE    = "require"
      ELASTICSEARCH_URL   = local.es_url
      STORAGE_BACKEND     = "s3"
      S3_BUCKET           = aws_s3_bucket.data.bucket
      S3_REGION           = var.region
      MODELS_S3_URI       = "s3://${aws_s3_bucket.data.bucket}/models/"
      PUBLIC_BASE_URL     = "https://${var.domain_name}"
      SERVE_FRONTEND      = "true"
      WARMUP_ON_START     = "true"
      FORWARDED_ALLOW_IPS = "*" # only the load balancer can reach port 8000 (security group "app")
    },
    var.enable_redis ? { REDIS_URL = "redis://${aws_elasticache_cluster.main[0].cache_nodes[0].address}:6379/0" } : {},
    var.app_environment,
  )

  user_data = {
    for role in ["search", "worker"] : role => base64encode(templatefile("${path.module}/user-data.sh.tftpl", {
      role       = role
      gpu        = role == "search" || var.worker_use_gpu
      region     = var.region
      image      = local.image
      registry   = local.registry
      secret_arn = aws_secretsmanager_secret.app.arn
      log_group  = aws_cloudwatch_log_group.app.name
      env_lines = join("\n", [for key, value in merge(local.app_env, role == "worker" ? {
        INGEST_WORKER = "true"
        RUN_DB_INIT   = "true"
        } : {
        INGEST_WORKER = "false"
      }) : "${key}=${value}"])
    }))
  }
}

resource "aws_launch_template" "search" {
  name_prefix            = "${var.name}-search-"
  image_id               = data.aws_ami.gpu.id
  instance_type          = var.search_instance_type
  vpc_security_group_ids = [aws_security_group.app.id]
  user_data              = local.user_data["search"]
  update_default_version = true

  iam_instance_profile {
    arn = aws_iam_instance_profile.app.arn
  }
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  block_device_mappings {
    device_name = data.aws_ami.gpu.root_device_name
    ebs {
      volume_type = "gp3"
      volume_size = var.root_volume_gb
      encrypted   = true
    }
  }
  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.name}-search", Role = "search" }
  }
}

resource "aws_launch_template" "worker" {
  name_prefix            = "${var.name}-worker-"
  image_id               = var.worker_use_gpu ? data.aws_ami.gpu.id : data.aws_ami.al2023.id
  instance_type          = var.worker_instance_type
  vpc_security_group_ids = [aws_security_group.app.id]
  user_data              = local.user_data["worker"]
  update_default_version = true

  iam_instance_profile {
    arn = aws_iam_instance_profile.app.arn
  }
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  block_device_mappings {
    device_name = var.worker_use_gpu ? data.aws_ami.gpu.root_device_name : data.aws_ami.al2023.root_device_name
    ebs {
      volume_type = "gp3"
      volume_size = var.root_volume_gb
      encrypted   = true
    }
  }
  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.name}-worker", Role = "worker" }
  }
}

# Search copies behind the load balancer. A new copy downloads the models and loads them before /ready turns 200,
# hence the long grace period.
resource "aws_autoscaling_group" "search" {
  name                      = "${var.name}-search"
  vpc_zone_identifier       = aws_subnet.private[*].id
  min_size                  = var.search_min
  max_size                  = var.search_max
  desired_capacity          = var.search_desired
  target_group_arns         = [aws_lb_target_group.api.arn]
  health_check_type         = "ELB"
  health_check_grace_period = 900
  default_instance_warmup   = 600

  launch_template {
    id      = aws_launch_template.search.id
    version = aws_launch_template.search.latest_version
  }
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
    }
  }
  tag {
    key                 = "Name"
    value               = "${var.name}-search"
    propagate_at_launch = true
  }
  lifecycle {
    # The scaling policy moves it; do not fight it on the next apply.
    ignore_changes = [desired_capacity]
  }
}

resource "aws_autoscaling_policy" "search_requests" {
  name                   = "${var.name}-requests-per-copy"
  autoscaling_group_name = aws_autoscaling_group.search.name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.main.arn_suffix}/${aws_lb_target_group.api.arn_suffix}"
    }
    target_value = var.requests_per_copy_per_minute
  }
}

# Exactly one ingestion worker, replaced automatically if it fails. Not behind the load balancer.
resource "aws_autoscaling_group" "worker" {
  name                      = "${var.name}-worker"
  vpc_zone_identifier       = aws_subnet.private[*].id
  min_size                  = 1
  max_size                  = 1
  desired_capacity          = 1
  health_check_type         = "EC2"
  health_check_grace_period = 900

  launch_template {
    id      = aws_launch_template.worker.id
    version = aws_launch_template.worker.latest_version
  }
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 0
    }
  }
  tag {
    key                 = "Name"
    value               = "${var.name}-worker"
    propagate_at_launch = true
  }
}
