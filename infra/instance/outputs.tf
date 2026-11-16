output "instance_name" {
  value = aws_lightsail_instance.this.name
}

output "ipv4" {
  description = "Static IPv4 address (survives stop/start)."
  value       = aws_lightsail_static_ip.this.ip_address
}

output "ipv6" {
  description = "IPv6 address of the instance (null if none)."
  value       = try(aws_lightsail_instance.this.ipv6_addresses[0], null)
}

output "ssh_user" {
  description = "Login user of the image."
  value       = aws_lightsail_instance.this.username
}

output "ssh_target" {
  description = "Value for DEPLOY_SSH in the site file."
  value       = "${aws_lightsail_instance.this.username}@${aws_lightsail_static_ip.this.ip_address}"
}
