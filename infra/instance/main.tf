# One Lightsail WordPress instance for wpmcp: static IPv4, IPv6, firewall with SSH limited
# to the given ranges, optional daily snapshots. The software is installed afterwards by
# bin/bootstrap-site (a fresh site) and bin/deploy (wpmcp itself), not by user data, so
# that both stay re-runnable and reviewable.

locals {
  az = coalesce(var.availability_zone, "${var.region}a")
}

resource "aws_lightsail_key_pair" "this" {
  name       = "${var.name}-key"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "aws_lightsail_instance" "this" {
  name              = var.name
  availability_zone = local.az
  blueprint_id      = var.blueprint_id
  bundle_id         = var.bundle_id
  key_pair_name     = aws_lightsail_key_pair.this.name
  ip_address_type   = "dualstack"

  dynamic "add_on" {
    for_each = var.auto_snapshots ? [1] : []
    content {
      type          = "AutoSnapshot"
      snapshot_time = var.snapshot_time
      status        = "Enabled"
    }
  }

  lifecycle {
    # A new image version must not replace a running site; move to it deliberately.
    ignore_changes = [blueprint_id]
  }
}

resource "aws_lightsail_static_ip" "this" {
  name = "${var.name}-ip"
}

resource "aws_lightsail_static_ip_attachment" "this" {
  static_ip_name = aws_lightsail_static_ip.this.name
  instance_name  = aws_lightsail_instance.this.name
}

# Replaces the instance's whole firewall: anything not listed here is closed.
resource "aws_lightsail_instance_public_ports" "this" {
  instance_name = aws_lightsail_instance.this.name

  port_info {
    protocol   = "tcp"
    from_port  = 22
    to_port    = 22
    cidrs      = var.ssh_cidrs
    ipv6_cidrs = var.ssh_ipv6_cidrs
  }

  port_info {
    protocol   = "tcp"
    from_port  = 80
    to_port    = 80
    cidrs      = ["0.0.0.0/0"]
    ipv6_cidrs = ["::/0"]
  }

  port_info {
    protocol   = "tcp"
    from_port  = 443
    to_port    = 443
    cidrs      = ["0.0.0.0/0"]
    ipv6_cidrs = ["::/0"]
  }
}
