output "records" {
  value = { for h in local.hosts : h => compact([local.ipv4, local.aaaa]) }
}

output "other_records" {
  value = { for k, r in aws_route53_record.extra : k => r.records }
}
