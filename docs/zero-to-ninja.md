# From zero to ninja: WordPress with MCP

A step-by-step walkthrough for a person, from an empty AWS account to a WordPress site
that ChatGPT and Claude can work on, with its mail sent through Amazon SES. Every command
is meant to be typed by you; nothing here needs an AI agent.

The reference documents behind each step are [`infra/README.md`](../infra/README.md),
[`operations.md`](operations.md), [`clients.md`](clients.md) and
[`security.md`](security.md). This page only puts them in order.

Throughout, `example.org` is your site, `mcp.example.org` the service, `you@example.net`
your own mailbox and `~/my-site/` a private folder **outside** this repository that holds
everything about your deployment.

| Stage | What you get | Time |
|---|---|---|
| 1–3 | Tools, AWS access, the repository | 30 min |
| 4–7 | A server, its DNS names, TLS | 20 min |
| 8–9 | WordPress ready (fresh or migrated) | 15 min |
| 10–12 | wpmcp running, ChatGPT and Claude connected | 20 min |
| 13 | Outgoing mail through SES | 30 min, plus up to a day for SES production access |
| 14 | Day-to-day habits | — |

## 1. What you need

- **An AWS account** where the server will live, and the DNS zone of your domain. The zone
  can be in Route 53 (in the same or another account) or at any other DNS provider.
- **A machine with a terminal** (Linux or macOS), with:
  - `git`, `ssh`, `python3`, `curl`, `dig`;
  - [uv](https://docs.astral.sh/uv/) (Python environment);
  - [Terraform](https://developer.hashicorp.com/terraform/install) ≥ 1.5 (or OpenTofu);
  - [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html);
  - optionally podman or docker, only for the local WordPress test stack.
- **An authenticator app** on your phone (any TOTP app: Aegis, 1Password, Google
  Authenticator…). It protects every approval of an assistant.
- **ChatGPT** on a plan with developer mode for custom connectors, and/or **Claude** on a
  plan with custom connectors.

## 2. AWS access from your terminal

Create a named profile for the account that will hold the server. With IAM Identity
Center (recommended):

```bash
aws configure sso --profile my-site          # or: aws login, or an IAM user's keys
aws sts get-caller-identity --profile my-site
```

If the DNS zone is in another account, give it its own profile (`dns-account` below).
Pick a region close to your readers and use it everywhere (`eu-west-1` in the examples).

## 3. The repository and your private folder

```bash
git clone <this repository> lightsail-wp-mcp && cd lightsail-wp-mcp
make setup          # .venv from uv.lock, a local .env for development only
make test           # ruff, pytest, shellcheck: must be green before any deploy

mkdir -p ~/my-site/keys ~/my-site/state && chmod 700 ~/my-site
ssh-keygen -t ed25519 -f ~/my-site/keys/my-site -C my-site    # the server's SSH key
```

Nothing in `~/my-site/` goes into git. That folder is your deployment: variables,
Terraform state, keys, notes. Back it up.

## 4. The server (Terraform, `infra/instance`)

```bash
cp infra/instance/terraform.tfvars.example ~/my-site/instance.tfvars
$EDITOR ~/my-site/instance.tfvars
```

| Variable | Value |
|---|---|
| `name` | a short name, e.g. `my-site` |
| `region`, `aws_profile` | from step 2 |
| `ssh_public_key_path` | `~/my-site/keys/my-site.pub` |
| `ssh_cidrs` | your public IP as `/32` (`curl -s https://checkip.amazonaws.com`) |
| `auto_snapshots` | `true` (daily snapshots, billed by size) |

The default image is Lightsail's own WordPress image (`wordpress_ls_1_0`, Debian 12,
`PLATFORM=debian`), which this guide assumes. Bitnami works too, with more manual steps
([`operations.md`](operations.md)).

```bash
terraform -chdir=infra/instance init
terraform -chdir=infra/instance apply -var-file=$HOME/my-site/instance.tfvars \
  -state=$HOME/my-site/state/instance.tfstate
terraform -chdir=infra/instance output -state=$HOME/my-site/state/instance.tfstate
```

Note `ssh_target`, `ipv4`, `ipv6` and `platform`. WordPress finishes its first boot about a
minute after the apply.

SSH is open to `ssh_cidrs` and to Lightsail's browser console. If your IP changes, use the
console (Lightsail → instance → **Connect using SSH**) or update `ssh_cidrs` and apply.

## 5. DNS names

The site (`example.org`, and `www.example.org` if you use it) and the service
(`mcp.example.org`) need A and AAAA records pointing at the instance.

**With Route 53**, use the `infra/dns` stack:

```bash
cp infra/dns/terraform.tfvars.example ~/my-site/dns.tfvars
$EDITOR ~/my-site/dns.tfvars
#   aws_profile = "dns-account"            # the account of the zone
#   zone_name   = "example.org"
#   hosts       = ["example.org", "www.example.org", "mcp.example.org"]
#   instance_state_path = "/home/me/my-site/state/instance.tfstate"   # absolute: no ~ in tfvars
terraform -chdir=infra/dns init
terraform -chdir=infra/dns apply -var-file=$HOME/my-site/dns.tfvars \
  -state=$HOME/my-site/state/dns.tfstate
```

**Elsewhere**, create the records by hand at your provider: an `A` with `ipv4` and an `AAAA`
with `ipv6` for each name, TTL 300.

Either way, wait until they answer before going on, because the certificates are requested
over HTTP:

```bash
for h in example.org www.example.org mcp.example.org; do echo "$h $(dig +short A $h) $(dig +short AAAA $h)"; done
```

## 6. Trust the server's host key

```bash
ssh-keyscan -t ed25519 <ipv4> > ~/my-site/keys/known_hosts
ssh-keygen -lf ~/my-site/keys/known_hosts
```

Compare that fingerprint with the server's own, from the browser console:
`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. If they match, every later connection
is pinned to that key.

## 7. The site file

```bash
cp deploy/site.env.example ~/my-site/site.env
$EDITOR ~/my-site/site.env
export SITE_ENV=~/my-site/site.env          # every bin/ script reads it
```

The minimum:

```bash
PLATFORM=debian
DEPLOY_SSH=admin@<ipv4>
DEPLOY_SSH_KEY=~/my-site/keys/my-site
DEPLOY_KNOWN_HOSTS=~/my-site/keys/known_hosts
WP_HOST=example.org
WP_ALIASES=www.example.org
MCP_HOST=mcp.example.org
SITE_NAME="Example Site"
ACME_EMAIL=you@example.net                  # Let's Encrypt expiry notices
SITE_LANGUAGES=en_GB,es_ES                  # fresh sites only; the first is the default
```

The file holds no secrets, but it describes your server: keep it in `~/my-site/`.

## 8. Prepare the server

```bash
bin/setup-server
```

On Lightsail's image it:

- brings every package up to date and turns on unattended security updates;
- refuses a PHP without the October 2026 fixes (PHP2Shell), and adds a hardening ini;
- fixes the site address to `https://example.org`;
- installs the site's vhosts, permalink rules and hardening (no XML-RPC, no PHP in
  uploads), and removes world-writable permissions;
- gets a Let's Encrypt certificate for `WP_HOST` and `WP_ALIASES`.

It is idempotent: run it again whenever you like. If it says "a reboot is pending", reboot
when convenient (`ssh … sudo reboot`).

**Then change the WordPress admin password.** The image's initial password is in
`/opt/aws/wordpress/credentials.log` and is also the database's:

```bash
ssh -t -i ~/my-site/keys/my-site admin@<ipv4> sudo cat /opt/aws/wordpress/credentials.log
```

Log in at `https://example.org/wp-admin/` with the user and password in that file, set a
long new password in
**Users → Profile**, and add a second factor for wp-admin with a 2FA plugin of your choice.

## 9. Content: a fresh site, or a site you already have

**A fresh site:**

```bash
bin/bootstrap-site      # Polylang with SITE_LANGUAGES, All in One SEO, post-name permalinks
```

**A site moving from another server:** follow "Moving a site to a new server" in
[`operations.md`](operations.md) (`SOURCE_*` and `MIGRATE_*` in the site file, then
`bin/migrate-site`). Only the database and media are copied; code is installed again from
wordpress.org.

## 10. Install wpmcp

```bash
bin/deploy
```

It runs the tests, builds a release, installs the service with its own certificate for
`mcp.example.org`, the bridge mu-plugin and the Apache vhosts, and checks its health. A
failed Apache change or health check is undone. Check from outside:

```bash
curl -s https://mcp.example.org/healthz; echo
curl -s -o /dev/null -w '%{http_code}\n' https://mcp.example.org/mcp     # 401: login required
```

## 11. You, the owner, and the two clients

Secrets are shown once, in your terminal only. Do this yourself, not through an assistant:

```bash
ssh -t -i ~/my-site/keys/my-site admin@<ipv4>
sudo wpmcp-admin owner set
```

- Choose the **consent password**: long, used only here.
- Add the **TOTP secret** to your authenticator app: "enter a setup key", time-based,
  6 digits. The `otpauth://` link shows the same thing.
- Type a code to confirm.

Then register the clients you will use. Each prints a `client_id` and a `client_secret`
once: paste them straight into the connector (steps 12a and 12b), not into a file.

```bash
sudo wpmcp-admin client add --preset chatgpt
sudo wpmcp-admin client add --preset claude
```

## 12a. Connect ChatGPT

OpenAI moves this menu now and then. At the time of writing:

1. ChatGPT → **Settings → Apps** (formerly "Connectors") → **Advanced settings** → turn
   on **Developer mode**. On Business or Enterprise, an admin may have to allow custom
   connectors first.
2. **Create** (new app / connector):
   - **Name:** your site's name; **Description:** what it is for;
   - **MCP server URL:** `https://mcp.example.org/mcp`;
   - **Authentication:** OAuth. Open the advanced OAuth settings and enter the
     **client ID** and **client secret** from step 11. This server has no dynamic
     registration, so they are required. Token endpoint authentication:
     `client_secret_post` (or `client_secret_basic`). OIDC: off.
   - Accept the warning about custom connectors and create it.
3. ChatGPT opens the **consent screen** of your server. Choose **Read and write** (or
   **Read only**), enter the consent password and a code from the authenticator.
4. In a new chat, enable the connector (the **+** menu, then developer mode tools) and ask:
   *"Use get_connection_info and get_site_overview."*

The redirect URI registered by the preset is
`https://chatgpt.com/connector_platform_oauth_redirect`. If the consent screen says the
redirect URI does not match, see [`clients.md`](clients.md).

## 12b. Connect Claude

1. claude.ai → **Settings → Connectors → Add custom connector**.
2. URL: `https://mcp.example.org/mcp`. Under **Advanced settings**, enter the client ID and
   client secret from step 11.
3. **Connect**, then approve on the consent screen as above.
4. In a chat, enable the connector from the tools menu and ask for the site overview.

The connector is then available on claude.ai, the desktop app and mobile.

**How the tools behave:** drafts never change what visitors see. Publishing, live edits,
trash, translation links and menus do: they are budgeted (30 an hour), protected pages
are refused, every one can be undone, and the site's admin e-mail gets an hourly digest
(which needs step 13). Images: ChatGPT passes them directly; Claude gives you a single-use
upload link.

## 13. Outgoing mail through Amazon SES

WordPress needs to send mail: password resets, form notifications, wpmcp's digest. This
step sends it from `noreply@example.org` through SES, with DKIM, SPF and DMARC aligned.
Replies go to your own mailbox. Nothing receives mail for the domain.

### 13.1 The SES identity and the sender user

```bash
cp infra/mail/terraform.tfvars.example ~/my-site/mail.tfvars
$EDITOR ~/my-site/mail.tfvars
#   aws_profile    = "my-site"
#   region         = "eu-west-1"
#   domain         = "example.org"
#   from_addresses = ["noreply@example.org"]
terraform -chdir=infra/mail init
terraform -chdir=infra/mail apply -var-file=$HOME/my-site/mail.tfvars \
  -state=$HOME/my-site/state/mail.tfstate
```

This creates the domain identity (Easy DKIM, MAIL FROM `mail.example.org`) and an IAM user
that may only send as those From addresses. Terraform creates no access key.

### 13.2 Its DNS records

**Route 53:** add the mail stack's records to the DNS stack and apply it again:

```bash
# in ~/my-site/dns.tfvars:
#   mail_state_path = "/home/me/my-site/state/mail.tfstate"
terraform -chdir=infra/dns apply -var-file=$HOME/my-site/dns.tfvars \
  -state=$HOME/my-site/state/dns.tfstate
```

**Elsewhere:** create by hand what
`terraform -chdir=infra/mail output -state=$HOME/my-site/state/mail.tfstate dns_records`
lists. That is three DKIM CNAMEs, an MX and an SPF TXT for `mail.`, and the `_dmarc` TXT.
Do not add an MX record for the domain itself.

Wait for verification (usually minutes, at most 72 hours):

```bash
aws sesv2 get-email-identity --profile my-site --region eu-west-1 \
  --email-identity example.org \
  --query '{identity:VerificationStatus,dkim:DkimAttributes.Status,mailfrom:MailFromAttributes.MailFromDomainStatus}'
# all three: SUCCESS
```

### 13.3 The sandbox, and production access

A new SES account is in the **sandbox**: it only delivers to addresses you have
verified, at most 200 messages a day. For testing, verify your own mailbox:

```bash
aws sesv2 create-email-identity --profile my-site --region eu-west-1 --email-identity you@example.net
```

Amazon then sends a message titled *"Amazon Web Services – Email Address Verification
Request in region …"* to `you@example.net`. **Open it and click the link within 24
hours**, then check that it worked:

```bash
aws sesv2 get-email-identity --profile my-site --region eu-west-1 \
  --email-identity you@example.net --query VerificationStatus     # SUCCESS
```

To send to anyone (your readers resetting a password, form senders), request
**production access**. Describe honestly what the site sends:

```bash
aws sesv2 put-account-details --profile my-site --region eu-west-1 \
  --production-access-enabled \
  --mail-type TRANSACTIONAL \
  --website-url https://example.org \
  --contact-language EN \
  --additional-contact-email-addresses you@example.net \
  --use-case-description "Transactional mail of a small WordPress site (example.org): \
password resets for its few editors, contact-form notifications to the site owner, and an \
hourly digest of changes to the owner. No marketing, no lists, no purchased addresses; \
volume well under 100 messages a day. Sent only from noreply@example.org, with DKIM, SPF \
and DMARC; bounces and complaints go to SES's MAIL FROM domain and are reviewed by the owner."
```

AWS answers by e-mail to the account's contacts, usually within a day. It may ask
questions in a support case: answer them there. Check the result:

```bash
aws sesv2 get-account --profile my-site --region eu-west-1 --query '{prod:ProductionAccessEnabled,quota:SendQuota}'
```

You can go on with 13.4 while you wait. In the sandbox, the test goes to your verified
address.

### 13.4 WordPress sends through SES

In the site file:

```bash
MAIL_AWS_PROFILE=my-site
MAIL_REGION=eu-west-1
MAIL_FROM=noreply@example.org
MAIL_REPLY_TO=you@example.net
MAIL_TEST_TO=you@example.net
```

```bash
bin/mail-credentials
```

The script:

- creates an access key for the sender user and turns it into an SES SMTP password on
  your machine;
- sends it straight to the server, without printing it;
- installs and configures WP Mail SMTP (free "Other SMTP" mailer, set by constants
  outside the database), forces the From address and adds the Reply-To;
- sends a test message, and only then deletes the user's older keys.

Run it again at any time to rotate the credentials.

**Check the test message.** In Gmail: open it → ⋮ → **Show original**. You want
`SPF: PASS`, `DKIM: PASS` (signed by `example.org`) and `DMARC: PASS`. Elsewhere, look for
`dkim=pass` and `spf=pass` in the `Authentication-Results` header.

Optional: set WordPress's **Settings → General → Administration Email Address** to the
mailbox where you want the digest and notices. WordPress sends a confirmation link to it:
click it.

## 14. Ninja habits

- **Updates.**
  - *Packages:* Debian packages, PHP included, update themselves every night (security
    updates only). Kernel updates need a reboot: `ls /var/run/reboot-required` tells you.
  - *WordPress and plugins:* WordPress core installs minor releases by itself. Update
    plugins and themes in wp-admin, or turn on their auto-updates there.
  - *wpmcp:* `git pull`, `make test`, `bin/deploy`.
  - None of these updates fights the file permissions set by `bin/setup-server`: the web
    server keeps group write access to the web root. The exceptions are `wp-config.php`
    and the mu-plugins, which stay root's.
- **Snapshots.** Automatic daily snapshots are on. Before a risky change, take a manual
  one (Lightsail console → Snapshots).
- **Connections.** Run `sudo wpmcp-admin grant list` now and then. If a device is lost:
  `sudo wpmcp-admin revoke-all`, then `owner set` again.
- **Audit.** `journalctl -u wpmcp | grep '"wp_write"'` shows every change the assistants
  made, as tool, object and connection.
- **Address allowlist.** After a week of normal use without `deny_ip` lines, set
  `WPMCP_CIDR_MODE=enforce` in the site file and run `bin/deploy` ([`operations.md`](operations.md)).
- **Budgets and protected pages.** Use `WPMCP_LIVE_BUDGET` and `WPMCP_PROTECTED_IDS` in the
  site file, then `bin/deploy`.
- **Full stop.** `sudo systemctl disable --now wpmcp`.

## When something goes wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| `bin/setup-server`: "PHP … lacks the fixes" | the image's PHP is older than the October 2026 releases | `sudo apt update && sudo apt full-upgrade` on the server (Debian); AWS's PHP guide (Bitnami) |
| "HTTPS for the site waits" | the names do not point at the server yet | step 5, then `bin/setup-server` again |
| `Host key verification failed` | known_hosts does not match (new instance, same IP) | step 6 again, checking the fingerprint |
| Consent screen: "redirect_uri does not match" | the product changed its callback | [`clients.md`](clients.md): register that exact URI |
| ChatGPT/Claude: "authorization failed" after consent | wrong client secret pasted | `client add` again, disable the old client |
| Test mail: "Could not authenticate" | the new IAM key has not propagated yet | wait a minute, run `bin/mail-credentials` again |
| Test mail never arrives | SES sandbox and recipient not verified | 13.3 |
| `/wp-json/` or pages 404 after a migration | rewrite rules flushed without plugins | `bin/migrate-site` finalize, or `wp rewrite flush` with plugins loaded |

## Tearing down

Destroy the stacks in this order: DNS, then mail, then the instance. Take a manual
snapshot first if you want to keep anything. Destroying the instance deletes its disk and
its automatic snapshots. See [`infra/README.md`](../infra/README.md).
