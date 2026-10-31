# --- basics -------------------------------------------------------------------------------------------------------

variable "region" {
  description = "AWS region, e.g. ap-south-1 (Mumbai)."
  type        = string
}

variable "name" {
  description = "Prefix for every resource name (lower case, digits, hyphens)."
  type        = string
  default     = "osiss"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.name))
    error_message = "name: 2-21 characters, lower case letters, digits and hyphens, starting with a letter."
  }
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

# --- domain and HTTPS ---------------------------------------------------------------------------------------------

variable "domain_name" {
  description = "Site address, e.g. ossis.com or search.college.edu. Must be a domain you control."
  type        = string
}

variable "route53_zone_id" {
  description = "Route 53 hosted zone of the domain. Empty: add the certificate-validation and site records at your DNS provider yourself (see outputs)."
  type        = string
  default     = ""
}

variable "admin_cidrs" {
  description = "Addresses allowed to reach /api/admin/* (the admin app), e.g. [\"203.0.113.10/32\"]. Empty: anyone (the admin token and its lockout still apply)."
  type        = list(string)
  default     = []

  validation {
    # A load balancer rule takes at most 5 condition values; the path pattern uses one.
    condition     = length(var.admin_cidrs) <= 4 && alltrue([for cidr in var.admin_cidrs : can(cidrhost(cidr, 0))])
    error_message = "admin_cidrs: at most 4 CIDR blocks such as 203.0.113.10/32."
  }
}

# --- search copies ------------------------------------------------------------------------------------------------

variable "search_instance_type" {
  description = "GPU instance for the search copies (e.g. g5.xlarge: 1x A10G 24 GB; g4dn.xlarge: 1x T4 16 GB). Check your G-instance vCPU quota first."
  type        = string
  default     = "g5.xlarge"
}

variable "search_min" {
  type    = number
  default = 1
}

variable "search_desired" {
  type    = number
  default = 2
}

variable "search_max" {
  type    = number
  default = 4
}

variable "requests_per_copy_per_minute" {
  description = "Autoscaling target: average searches (ALB requests) per copy per minute before another copy is started. Measure with tests/load_test.py."
  type        = number
  default     = 60
}

variable "gpu_ami_ssm_parameter" {
  description = "SSM parameter holding the GPU AMI (AWS Deep Learning Base AMI: NVIDIA driver, Docker and the NVIDIA Container Toolkit)."
  type        = string
  default     = "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
}

variable "root_volume_gb" {
  description = "Disk of each API instance: image + models (~12 GB) + temporary PDF copies."
  type        = number
  default     = 100
}

# --- ingestion worker ---------------------------------------------------------------------------------------------

variable "worker_instance_type" {
  description = "Instance for the single ingestion worker (OCR + embedding). A GPU type embeds much faster; a CPU type (e.g. c6i.2xlarge with worker_use_gpu = false) is cheaper."
  type        = string
  default     = "g4dn.xlarge"
}

variable "worker_use_gpu" {
  type    = bool
  default = true
}

# --- application --------------------------------------------------------------------------------------------------

variable "container_image" {
  description = "API image. Empty: <the ECR repository created here>:latest (build and push it first, see README)."
  type        = string
  default     = ""
}

variable "app_environment" {
  description = "Extra OSISS settings for every copy, e.g. { MODEL_PRECISION = \"fp16\", SEARCH_CONCURRENCY = \"3\" }."
  type        = map(string)
  default     = {}
}

# --- database -----------------------------------------------------------------------------------------------------

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage_gb" {
  type    = number
  default = 50
}

variable "db_multi_az" {
  description = "Standby database in a second zone (roughly doubles the database cost)."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Protect the database from deletion; set false before `terraform destroy`."
  type        = bool
  default     = true
}

# --- Elasticsearch ------------------------------------------------------------------------------------------------

variable "elasticsearch_mode" {
  description = "\"self_managed\": one Elasticsearch 8 instance on EC2 created here. \"elastic_cloud\": an Elastic Cloud deployment you created (same API, managed, paid separately)."
  type        = string
  default     = "self_managed"

  validation {
    condition     = contains(["self_managed", "elastic_cloud"], var.elasticsearch_mode)
    error_message = "elasticsearch_mode must be self_managed or elastic_cloud."
  }
}

variable "elastic_cloud_url" {
  description = "Elastic Cloud: the deployment's Elasticsearch endpoint (https://...:443)."
  type        = string
  default     = ""
}

variable "elastic_cloud_api_key" {
  description = "Elastic Cloud: an API key with access to the osiss_chunks index."
  type        = string
  default     = ""
  sensitive   = true
}

variable "es_instance_type" {
  type    = string
  default = "r6i.large"
}

variable "es_volume_gb" {
  type    = number
  default = 100
}

variable "es_heap" {
  description = "Elasticsearch JVM heap (about half the instance memory, at most 31g)."
  type        = string
  default     = "8g"
}

# --- optional extras ----------------------------------------------------------------------------------------------

variable "enable_redis" {
  description = "ElastiCache Redis for an answer cache and exact rate limits shared by all copies."
  type        = bool
  default     = false
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.small"
}

variable "waf_search_limit_per_5min" {
  description = "AWS WAF: block an IP that sends more than this many /api/search requests in 5 minutes (0 = no WAF)."
  type        = number
  default     = 0

  validation {
    condition     = var.waf_search_limit_per_5min == 0 || var.waf_search_limit_per_5min >= 10
    error_message = "waf_search_limit_per_5min must be 0 (off) or at least 10."
  }
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "force_destroy_bucket" {
  description = "Let `terraform destroy` delete the bucket with all PDFs and models in it."
  type        = bool
  default     = false
}
