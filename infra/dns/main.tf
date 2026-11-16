# Optional stack: A/AAAA records for the site and the service, plus other records (the mail
# stack's, or any given in `records`), in a Route 53 zone that may belong to another
# account. Existing records are not overwritten unless allow_overwrite = true: a name
# already in use makes the apply fail instead.
#
# allow_overwrite = true takes over records created by hand (a migration's DNS switch).
# Records this stack holds are deleted by `terraform destroy`: for a live site, take them
# out of the state (`terraform state rm`) instead of destroying.

data "aws_route53_zone" "this" {
  name         = var.zone_name
  private_zone = false
}

data "terraform_remote_state" "mail" {
  count   = var.mail_state_path == null ? 0 : 1
  backend = "local"
  config  = { path = var.mail_state_path }
}

data "terraform_remote_state" "instance" {
  count   = length(var.hosts) > 0 && (var.ipv4 == null || var.ipv6 == null) ? 1 : 0
  backend = "local"
  config  = { path = var.instance_state_path }
}

locals {
  zone = trimsuffix(data.aws_route53_zone.this.name, ".")
  ipv4 = length(var.hosts) == 0 ? null : coalesce(var.ipv4, try(data.terraform_remote_state.instance[0].outputs.ipv4, null))
  ipv6 = var.ipv6 != null ? var.ipv6 : try(data.terraform_remote_state.instance[0].outputs.ipv6, "")
  # null from the instance stack (no IPv6) behaves like "".
  aaaa  = local.ipv6 == null ? "" : local.ipv6
  hosts = toset(var.hosts)
  # "name/type" -> record, from the mail stack and then var.records (which wins).
  extra = merge(
    { for r in try(data.terraform_remote_state.mail[0].outputs.dns_records, []) :
    "${r.name}/${r.type}" => { name = r.name, type = r.type, values = r.values, ttl = try(r.ttl, null) } },
    { for r in var.records : "${r.name}/${r.type}" => r },
  )
}

resource "terraform_data" "checks" {
  lifecycle {
    precondition {
      condition     = alltrue([for h in var.hosts : h == local.zone || endswith(h, ".${local.zone}")])
      error_message = "Every host must be inside ${local.zone}."
    }
    precondition {
      condition     = alltrue([for k, r in local.extra : r.name == local.zone || endswith(r.name, ".${local.zone}")])
      error_message = "Every record must be inside ${local.zone}."
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
  allow_overwrite = var.allow_overwrite
  depends_on      = [terraform_data.checks]
}

resource "aws_route53_record" "aaaa" {
  for_each        = local.aaaa == "" ? toset([]) : local.hosts
  zone_id         = data.aws_route53_zone.this.zone_id
  name            = each.value
  type            = "AAAA"
  ttl             = var.ttl
  records         = [local.aaaa]
  allow_overwrite = var.allow_overwrite
  depends_on      = [terraform_data.checks]
}

resource "aws_route53_record" "extra" {
  for_each        = local.extra
  zone_id         = data.aws_route53_zone.this.zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = coalesce(each.value.ttl, var.ttl)
  records         = each.value.values
  allow_overwrite = var.allow_overwrite
  depends_on      = [terraform_data.checks]
}
