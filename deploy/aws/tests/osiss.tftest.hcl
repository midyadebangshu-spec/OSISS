# terraform test: runs the configuration against a mocked AWS provider (no account, nothing created) and checks
# what each option set produces.   cd deploy/aws && terraform init -backend=false && terraform test

mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = { names = ["ap-south-1a", "ap-south-1b", "ap-south-1c"] }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_ssm_parameter" {
    defaults = { value = "ami-0123456789abcdef0" }
  }
  mock_data "aws_ami" {
    defaults = { id = "ami-0123456789abcdef0", root_device_name = "/dev/sda1" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { bucket = "osiss-data-123456789012-ap-south-1", arn = "arn:aws:s3:::osiss-data-123456789012-ap-south-1" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = { repository_url = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/osiss-api", arn = "arn:aws:ecr:ap-south-1:123456789012:repository/osiss-api" }
  }
  mock_resource "aws_db_instance" {
    defaults = { address = "osiss.abc.ap-south-1.rds.amazonaws.com", port = 5432 }
  }
  mock_resource "aws_instance" {
    defaults = { private_ip = "10.40.10.5" }
  }
  mock_resource "aws_elasticache_cluster" {
    defaults = { cache_nodes = [{ address = "osiss.cache.amazonaws.com", port = 6379, id = "0001", availability_zone = "ap-south-1a", outpost_arn = "" }] }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:ap-south-1:123456789012:secret:osiss/app-AbCdEf" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:ap-south-1:123456789012:log-group:/osiss/osiss" }
  }
  mock_resource "aws_acm_certificate" {
    defaults = {
      arn = "arn:aws:acm:ap-south-1:123456789012:certificate/abc"
      domain_validation_options = [{
        domain_name           = "ossis.example.com"
        resource_record_name  = "_x.ossis.example.com."
        resource_record_type  = "CNAME"
        resource_record_value = "_y.acm-validations.aws."
      }]
    }
  }
  mock_resource "aws_iam_instance_profile" {
    defaults = { arn = "arn:aws:iam::123456789012:instance-profile/osiss-app" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/osiss-app" }
  }
  mock_resource "aws_wafv2_web_acl" {
    defaults = { arn = "arn:aws:wafv2:ap-south-1:123456789012:regional/webacl/osiss/1" }
  }
  mock_resource "aws_acm_certificate_validation" {
    defaults = { certificate_arn = "arn:aws:acm:ap-south-1:123456789012:certificate/abc" }
  }
  mock_resource "aws_lb_listener" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:listener/app/osiss/1/2" }
  }
  mock_resource "aws_launch_template" {
    defaults = { id = "lt-0123456789abcdef0", latest_version = 1 }
  }
  mock_resource "aws_lb" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:loadbalancer/app/osiss/1", arn_suffix = "app/osiss/1", dns_name = "osiss-1.ap-south-1.elb.amazonaws.com", zone_id = "Z1" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:targetgroup/osiss-api/1", arn_suffix = "targetgroup/osiss-api/1" }
  }
}

variables {
  region      = "ap-south-1"
  domain_name = "ossis.example.com"
}

run "defaults_self_managed_elasticsearch" {
  command = apply

  assert {
    condition     = length(aws_instance.es) == 1 && length(aws_ebs_volume.es) == 1
    error_message = "self_managed mode must create the Elasticsearch instance and its data volume"
  }
  assert {
    condition     = local.es_url == "http://10.40.10.5:9200"
    error_message = "copies must use the private Elasticsearch address"
  }
  assert {
    condition     = local.app_env.STATE_BACKEND == "postgres" && local.app_env.STORAGE_BACKEND == "s3" && local.app_env.POSTGRES_SSLMODE == "require"
    error_message = "copies must share state through PostgreSQL (TLS) and keep PDFs in S3"
  }
  assert {
    condition     = local.app_env.MODELS_S3_URI == "s3://osiss-data-123456789012-ap-south-1/models/" && local.app_env.PUBLIC_BASE_URL == "https://ossis.example.com"
    error_message = "models and public URL"
  }
  assert {
    condition     = !contains(keys(local.app_env), "REDIS_URL") && length(aws_elasticache_cluster.main) == 0
    error_message = "no Redis unless enabled"
  }
  assert {
    condition     = length(aws_wafv2_web_acl.main) == 0 && length(aws_lb_listener_rule.admin_denied) == 0
    error_message = "WAF and the admin IP rule are opt-in"
  }
  assert {
    condition     = aws_lb_target_group.api.health_check[0].path == "/ready"
    error_message = "the load balancer must route only to ready copies"
  }
  assert {
    condition     = aws_autoscaling_group.worker.max_size == 1 && aws_autoscaling_group.search.max_size == 4
    error_message = "one worker; search copies scale"
  }
  assert {
    condition     = strcontains(base64decode(local.user_data["search"]), "INGEST_WORKER=false") && strcontains(base64decode(local.user_data["worker"]), "INGEST_WORKER=true")
    error_message = "only the worker runs ingestion"
  }
  assert {
    condition     = strcontains(base64decode(local.user_data["worker"]), "RUN_DB_INIT=true") && !strcontains(base64decode(local.user_data["search"]), "RUN_DB_INIT")
    error_message = "only the worker initialises the schema"
  }
  assert {
    condition     = !strcontains(base64decode(local.user_data["search"]), "ADMIN_TOKEN=") && !strcontains(base64decode(local.user_data["search"]), random_password.db.result)
    error_message = "secrets must come from Secrets Manager, not user data"
  }
  assert {
    condition     = strcontains(base64decode(local.user_data["search"]), "--gpus all")
    error_message = "search copies use the GPU"
  }
  assert {
    condition     = length(aws_route53_record.site) == 0
    error_message = "no Route 53 records without a zone"
  }
}

run "elastic_cloud_redis_waf_admin_ips_route53_cpu_worker" {
  command = apply

  variables {
    elasticsearch_mode        = "elastic_cloud"
    elastic_cloud_url         = "https://osiss.es.ap-south-1.aws.elastic-cloud.com:443"
    elastic_cloud_api_key     = "secret-key"
    enable_redis              = true
    waf_search_limit_per_5min = 500
    admin_cidrs               = ["203.0.113.10/32"]
    route53_zone_id           = "Z123"
    worker_use_gpu            = false
    worker_instance_type      = "c6i.2xlarge"
    app_environment           = { MODEL_PRECISION = "fp16" }
  }

  assert {
    condition     = length(aws_instance.es) == 0 && local.es_url == "https://osiss.es.ap-south-1.aws.elastic-cloud.com:443"
    error_message = "elastic_cloud mode uses the given endpoint and creates no instance"
  }
  assert {
    condition     = local.app_env.REDIS_URL == "redis://osiss.cache.amazonaws.com:6379/0"
    error_message = "Redis URL passed to the copies"
  }
  assert {
    condition     = length(aws_wafv2_web_acl.main) == 1 && one([for rule in aws_wafv2_web_acl.main[0].rule : one(one(rule.statement).rate_based_statement).limit]) == 500
    error_message = "WAF rate rule"
  }
  assert {
    condition     = length(aws_lb_listener_rule.admin_allowed) == 1 && length(aws_lb_listener_rule.admin_denied) == 1
    error_message = "admin API limited to admin_cidrs"
  }
  assert {
    condition     = length(aws_route53_record.site) == 1 && length(aws_route53_record.validation) == 1
    error_message = "Route 53 site and validation records"
  }
  assert {
    condition     = !strcontains(base64decode(local.user_data["worker"]), "--gpus all") && strcontains(base64decode(local.user_data["search"]), "MODEL_PRECISION=fp16")
    error_message = "CPU worker without --gpus; app_environment reaches the copies"
  }
  assert {
    condition     = jsondecode(aws_secretsmanager_secret_version.app.secret_string).ELASTICSEARCH_API_KEY == "secret-key"
    error_message = "the Elastic Cloud API key travels through Secrets Manager"
  }
}

run "rejects_bad_inputs" {
  command = plan

  variables {
    admin_cidrs               = ["not-a-cidr"]
    waf_search_limit_per_5min = 5
    elasticsearch_mode        = "opensearch"
  }

  expect_failures = [var.admin_cidrs, var.waf_search_limit_per_5min, var.elasticsearch_mode]
}
