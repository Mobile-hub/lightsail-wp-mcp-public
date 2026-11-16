# Optional stack: A/AAAA records for the site and the service, in a Route 53 zone that may
# belong to another account. Existing records are never overwritten (allow_overwrite =
# false): a name already in use makes the apply fail instead.

data "aws_route53_zone" "this" {
  name         = var.zone_name
  private_zone = false
}

data "terraform_remote_state" "instance" {
  count   = var.ipv4 == null || var.ipv6 == null ? 1 : 0
  backend = "local"
  config  = { path = var.instance_state_path }
}

locals {
  zone = trimsuffix(data.aws_route53_zone.this.name, ".")
  ipv4 = coalesce(var.ipv4, try(data.terraform_remote_state.instance[0].outputs.ipv4, null))
  ipv6 = var.ipv6 != null ? var.ipv6 : try(data.terraform_remote_state.instance[0].outputs.ipv6, "")
  # null from the instance stack (no IPv6) behaves like "".
  aaaa  = local.ipv6 == null ? "" : local.ipv6
  hosts = toset(var.hosts)
}

resource "terraform_data" "checks" {
  lifecycle {
    precondition {
      condition     = alltrue([for h in var.hosts : h == local.zone || endswith(h, ".${local.zone}")])
      error_message = "Every host must be inside ${local.zone}."
    }
  }
}

resource "aws_route53_record" "a" {
  for_each        = local.hosts
  zone_id         = data.aws_route53_zone.this.zone_id
  name            = each.value
  type            = "A"
  ttl             = var.ttl
  records         = [local.ipv4]
  allow_overwrite = false
  depends_on      = [terraform_data.checks]
}

resource "aws_route53_record" "aaaa" {
  for_each        = local.aaaa == "" ? toset([]) : local.hosts
  zone_id         = data.aws_route53_zone.this.zone_id
  name            = each.value
  type            = "AAAA"
  ttl             = var.ttl
  records         = [local.aaaa]
  allow_overwrite = false
  depends_on      = [terraform_data.checks]
}
