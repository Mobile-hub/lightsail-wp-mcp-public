terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Local state by default (git-ignored). For a shared or long-lived deployment use a
  # remote backend, for example:
  #   backend "s3" { bucket = "my-tf-state" key = "wpmcp/instance.tfstate" region = "eu-west-1" use_lockfile = true }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = var.tags
  }
}
