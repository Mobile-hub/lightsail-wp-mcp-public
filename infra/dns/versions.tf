terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

# The zone may live in another account: pick its profile, or a role to assume there.
provider "aws" {
  region  = "us-east-1" # Route 53 is global; the region only selects the API endpoint.
  profile = var.aws_profile

  dynamic "assume_role" {
    for_each = var.assume_role_arn == null ? [] : [var.assume_role_arn]
    content {
      role_arn     = assume_role.value
      session_name = "wpmcp-dns"
    }
  }

  default_tags {
    tags = var.tags
  }
}
