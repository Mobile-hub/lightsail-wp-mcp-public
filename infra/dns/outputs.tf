output "records" {
  value = { for h in local.hosts : h => compact([local.ipv4, local.aaaa]) }
}
