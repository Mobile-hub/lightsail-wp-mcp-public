variable "region" {
  description = "AWS region of the Lightsail instance."
  type        = string
  default     = "eu-west-1"
}

variable "aws_profile" {
  description = "AWS CLI profile for this stack (null: the default credential chain)."
  type        = string
  default     = null
}

variable "name" {
  description = "Name of the instance; also prefixes the static IP and key pair."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9][a-zA-Z0-9-]{1,40}$", var.name))
    error_message = "Use letters, digits and dashes (2-41 characters)."
  }
}

variable "availability_zone" {
  description = "Availability zone (null: the region's first zone, \"a\")."
  type        = string
  default     = null
}

variable "blueprint_id" {
  description = "Lightsail blueprint. wordpress_ls_1_0 is Lightsail's own WordPress image; wordpress is the Bitnami one. deploy/remote-install.sh supports both."
  type        = string
  default     = "wordpress_ls_1_0"

  validation {
    condition     = contains(["wordpress_ls_1_0", "wordpress"], var.blueprint_id)
    error_message = "Only wordpress_ls_1_0 and wordpress (Bitnami) are supported by the installer."
  }
}

variable "bundle_id" {
  description = "Instance size. 1 GB (micro) is enough for WordPress and wpmcp together; dual-stack bundles have no _ipv6 suffix."
  type        = string
  default     = "micro_3_0"
}

variable "ssh_public_key_path" {
  description = "Public key installed for SSH (imported as a Lightsail key pair). The private key never reaches Terraform."
  type        = string
}

variable "ssh_cidrs" {
  description = "IPv4 ranges allowed to reach port 22. Use your own address (x.x.x.x/32); 0.0.0.0/0 opens SSH to everyone."
  type        = list(string)
}

variable "ssh_ipv6_cidrs" {
  description = "IPv6 ranges allowed to reach port 22."
  type        = list(string)
  default     = []
}

variable "auto_snapshots" {
  description = "Daily automatic snapshots (kept for 7 days by Lightsail, billed by size)."
  type        = bool
  default     = true
}

variable "snapshot_time" {
  description = "Daily snapshot time, UTC, HH:00."
  type        = string
  default     = "03:00"
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = { project = "lightsail-wp-mcp" }
}
