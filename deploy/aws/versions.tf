terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Terraform state holds the generated admin token and database password: keep it in an encrypted, private
  # backend, e.g.
  # backend "s3" {
  #   bucket       = "my-terraform-state"
  #   key          = "osiss/terraform.tfstate"
  #   region       = "ap-south-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = merge({ Project = "osiss", Deployment = var.name }, var.tags)
  }
}
