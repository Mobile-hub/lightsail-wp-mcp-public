variable "region" {
  description = "SES region; WordPress sends through its SMTP endpoint (email-smtp.<region>.amazonaws.com)."
  type        = string
  default     = "eu-west-1"
}

variable "aws_profile" {
  description = "AWS CLI profile of the site's account (null: default chain)."
  type        = string
  default     = null
}

variable "domain" {
  description = "The site's domain, verified in SES with Easy DKIM, e.g. example.org."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.domain))
    error_message = "A lower-case domain name."
  }
}

variable "mail_from_subdomain" {
  description = "Custom MAIL FROM (bounce) domain under the site's, so SPF aligns: <this>.<domain>."
  type        = string
  default     = "mail"
}

variable "from_addresses" {
  description = "The only From addresses the sender may use (local parts are not enough: full addresses)."
  type        = list(string)

  validation {
    condition     = length(var.from_addresses) > 0 && alltrue([for a in var.from_addresses : can(regex("^[^@\\s]+@[a-z0-9.-]+$", a))])
    error_message = "List at least one e-mail address."
  }
}

variable "sender_name" {
  description = "IAM user whose access key, turned into SMTP credentials by bin/mail-credentials, WordPress uses."
  type        = string
  default     = "wp-mail-sender"
}

variable "dmarc_policy" {
  description = "DMARC policy published for the domain (none, quarantine or reject). No report address: nobody reads them."
  type        = string
  default     = "quarantine"

  validation {
    condition     = contains(["none", "quarantine", "reject"], var.dmarc_policy)
    error_message = "none, quarantine or reject."
  }
}

variable "tags" {
  type    = map(string)
  default = { project = "lightsail-wp-mcp" }
}
