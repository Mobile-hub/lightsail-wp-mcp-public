# Infrastructure

Terraform for a new WordPress server on AWS Lightsail, ready for `bin/setup-server`
and `bin/deploy`. Two independent stacks, so that the DNS zone can live in another AWS
account (or elsewhere):

| Stack | Creates | Needed |
|---|---|---|
| `instance/` | Lightsail instance (Lightsail's own WordPress image by default), static IPv4, IPv6, firewall (SSH only from the ranges you give; 80 and 443 open), optional daily snapshots, the SSH key pair (from your public key) | yes |
| `dns/` | A and AAAA records for the site and the service in a Route 53 public zone, in any account (profile or role) | optional: skip it if your DNS is managed elsewhere and create the records by hand |

Terraform ≥ 1.5 or OpenTofu. State is local (`terraform.tfstate`, git-ignored) unless
you configure a backend (see `instance/versions.tf`). Variable files (`*.tfvars`) are
git-ignored; keep yours outside the repository and pass them with `-var-file`.

## From nothing to a working server

```bash
# 1. The instance (about 2 minutes; WordPress finishes its first boot a minute later)
cd infra/instance
cp terraform.tfvars.example ~/my-site/instance.tfvars     # name, profile, key, your IP for SSH
terraform init
terraform apply -var-file=$HOME/my-site/instance.tfvars
terraform output ssh_target                               # -> DEPLOY_SSH
terraform output platform                                 # -> PLATFORM

# 2. DNS for WP_HOST and MCP_HOST (optional stack; reads the addresses from step 1)
cd ../dns
cp terraform.tfvars.example ~/my-site/dns.tfvars          # zone, hosts, profile of the zone's account
terraform init
terraform apply -var-file=$HOME/my-site/dns.tfvars

# 3. The site file (see deploy/site.env.example), then the software
cd ../..
ssh-keyscan "$(terraform -chdir=infra/instance output -raw ipv4)" > ~/my-site/known_hosts
SITE_ENV=~/my-site/site.env bin/setup-server     # packages, patched PHP, address, certificate, vhosts
SITE_ENV=~/my-site/site.env bin/bootstrap-site   # a fresh site: Polylang, languages
SITE_ENV=~/my-site/site.env bin/deploy           # wpmcp, its certificate, the bridge
```

Check the host key that `ssh-keyscan` printed against the instance's console (or accept
that first contact is trust on first use), and set `DEPLOY_KNOWN_HOSTS` in the site file
to that file. Then follow `docs/operations.md` (owner password, clients).

On Lightsail's own image `bin/setup-server` updates the packages and turns on unattended
security updates, refuses a PHP without the October 2026 fixes, fixes the site address to
`https://WP_HOST`, installs the site's vhosts and gets its certificate, and puts the
permalink rules in Apache's configuration (the image reads no `.htaccess`).
`bin/bootstrap-site` is only for a fresh site: Polylang with the languages of
`SITE_LANGUAGES`. Change the WordPress admin password afterwards: on this image the initial
one is also the database password.

SSH is open to `ssh_cidrs` and, unless `ssh_from_console = false`, to Lightsail's browser
console, so that a changed home address does not lock you out.

## Images

| `blueprint_id` | `platform` | What | Notes |
|---|---|---|---|
| `wordpress_ls_1_0` (default) | `debian` | Lightsail's own image: Debian 12, Apache 2 + mod_php (Debian's PHP, updated by apt), MariaDB, WP-CLI, IMDSv2 | everything in `bin/` |
| `wordpress` | `bitnami` | The Bitnami image | Bitnami's bundled PHP is not updated by apt (see AWS's PHP update guide); set up the site's certificate with `bncert-tool` |

The instance ignores later changes of `blueprint_id`: moving a site to another image is a
migration, not an apply.

## Tearing down

```bash
terraform -chdir=infra/dns destroy -var-file=$HOME/my-site/dns.tfvars
terraform -chdir=infra/instance destroy -var-file=$HOME/my-site/instance.tfvars
```

Destroying the instance deletes the site and its disk. Automatic snapshots go with it;
take a manual snapshot first if you want to keep anything.

## Cost

A `micro_3_0` bundle (1 GB, dual-stack) is about 7 USD a month, the static IP is free
while attached, and automatic snapshots are billed by size. Route 53 records cost nothing
beyond the zone.
