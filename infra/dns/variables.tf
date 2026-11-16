variable "aws_profile" {
  description = "AWS CLI profile of the account that holds the hosted zone (null: default chain)."
  type        = string
  default     = null
}

variable "assume_role_arn" {
  description = "Optional role to assume in the zone's account."
  type        = string
  default     = null
}

variable "zone_name" {
  description = "Public hosted zone, e.g. example.org."
  type        = string
}

variable "hosts" {
  description = "Names to point at the instance: the WordPress site (WP_HOST) and the service (MCP_HOST), both inside zone_name."
  type        = list(string)

  validation {
    condition     = length(var.hosts) > 0 && alltrue([for h in var.hosts : can(regex("^[a-z0-9.-]+$", h))])
    error_message = "List at least one lower-case host name."
  }
}

variable "ipv4" {
  description = "IPv4 address for the A records (null: read from the instance stack's state)."
  type        = string
  default     = null
}

variable "ipv6" {
  description = "IPv6 address for the AAAA records (null: from the instance stack's state; empty string: no AAAA)."
  type        = string
  default     = null
}

variable "instance_state_path" {
  description = "Local state file of the instance stack, used when ipv4/ipv6 are not given."
  type        = string
  default     = "../instance/terraform.tfstate"
}

variable "ttl" {
  type    = number
  default = 300
}

variable "tags" {
  type    = map(string)
  default = { project = "lightsail-wp-mcp" }
}
