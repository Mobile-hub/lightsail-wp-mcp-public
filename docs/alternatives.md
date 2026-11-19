# Related projects

This project lets ChatGPT and Claude operate one self-hosted WordPress site through a
remote MCP server, with no automation platform or relay in between. It does so with a
particular focus: **AWS Lightsail and the AWS services around it** (Terraform for the
instance, optional Route 53 records, mail through SES), and a separate, sandboxed service
next to WordPress.

Other projects address the same need in more general environments: any WordPress host,
often as a single plugin. They are interesting and inspiring alternatives to explore, both
for sites where this project's AWS focus does not fit and as a source of ideas. This page
lists the ones found in a desk review on 2026-11-19.

**How to read it.** Descriptions come from each project's own public listing or
documentation; none of them was installed, tested or audited for this page, and the order
is not a ranking. Features and versions change quickly: check the current listing before
relying on any detail.

## Two ways to do it

| | MCP inside WordPress (plugin) | Separate MCP service next to WordPress (this project) |
|---|---|---|
| Where the MCP endpoint runs | In the WordPress PHP runtime | In its own process, behind the web server |
| Installation | A plugin and its settings | A service, a reverse proxy and a small bridge plugin |
| Hosting | Any WordPress host | A server you control (here: Lightsail) |
| Authorisation | WordPress users, roles and capabilities, plus the plugin's own rules | The service's own policy on top of a least-privilege WordPress user |
| Updates | With the WordPress site | On their own cycle |
| Isolation | Shares the site's runtime and database access | Can be sandboxed apart from PHP and the database |

The plugin model needs fewer moving parts and works on shared or managed hosting. The
separate service needs more to operate in exchange for a process boundary of its own. Both
are reasonable; the right choice depends on the hosting environment and on how much of the
runtime one wants to control.

## Building blocks in WordPress core

- **Abilities API** (in core since WordPress 6.9): a registry where core and plugins declare
  what they can do, with input schemas and permission checks, so other components can
  discover and run those abilities.
  [Announcement](https://make.wordpress.org/ai/2025/07/17/abilities-api/),
  [repository](https://github.com/WordPress/abilities-api).
- **MCP Adapter** (WordPress project): exposes registered abilities as MCP tools, over HTTP
  or STDIO. It is a foundation rather than a packaged editorial tool, and several plugins
  below build on it. [Repository](https://github.com/WordPress/mcp-adapter),
  [announcement](https://make.wordpress.org/ai/2025/07/17/mcp-adapter/).

This project does not use them yet: its tools talk to WordPress through its own bridge
routes. Exposing those tools as abilities is a possible direction.

## Plugins that run the MCP server on the site itself

According to their documentation, these connect hosted assistants directly to the site,
with no relay run by the vendor.

| Project | Approach and authentication, as described | Ideas worth a look |
|---|---|---|
| [Easy MCP AI](https://wordpress.org/plugins/easy-mcp-ai/) ([docs](https://docs.themeisle.com/easy-mcp-ai/getting-started)) | MCP server in a plugin with its own OAuth 2.1 authorisation server (PKCE, refresh-token rotation, dynamic client registration); ChatGPT as a custom connector in developer mode, and Claude | Discovers abilities registered by other plugins and offers them as tools; SEO data integrations |
| [Agent Abilities for MCP](https://wordpress.org/plugins/agent-abilities-for-mcp/) | Built on the Abilities API and the MCP Adapter; every ability off by default | Binds the agent to one chosen WordPress user and re-checks that user's capability on every call; logs every call, denials included |
| [Cowboy MCP](https://wordpress.org/plugins/cowboy-mcp/) | Streamable HTTP endpoint on the site, API key | Undo for changes, an audit log of every tool call, a filter to block individual tools |
| [StifLi Flex MCP](https://mcpservers.org/servers/estebanstifli/stifli-flex-mcp) | OAuth 2.1, a large tool set | A receipt for each change, which the assistant can use to roll most changes back |
| [NIBWP](https://wordpress.org/plugins/nibwp/) | Built on the Abilities API and the MCP Adapter; WordPress Application Passwords for each call | Relies on core mechanisms only for authentication |
| [Royal MCP](https://wordpress.org/plugins/royal-mcp/) | MCP server in a plugin with many core and integration tools | Detects compatible plugins and adds tools for them |
| [AlphaBridge MCP](https://wordpress.org/plugins/?p=337059) | MCP implemented directly in PHP, with no Node middleware; a commercial add-on for more tools | A PHP-only implementation |

## Hosted or relayed

These solve the same need with a service in the path, which suits sites that prefer not to
run anything themselves.

- [WPVibe](https://wordpress.org/plugins/vibe-ai/): a plugin on the site plus a hosted MCP
  endpoint run by its vendor, which forwards calls to WordPress; it needs an account with
  that service and a publicly reachable site.
- WordPress.com's MCP integration: for sites hosted on WordPress.com rather than
  self-hosted ones, with OAuth consent on WordPress.com and a listing in Claude's
  connector directory, according to secondary sources. See WordPress.com's own support
  documentation for current details.

## Ideas these projects explore

Some approaches above go in directions this project has not taken, or has chosen
differently:

- **Abilities as the tool source.** Discovering abilities from core and other plugins
  instead of a fixed tool list (this project keeps a fixed, typed list on purpose; see
  [security.md](security.md)).
- **Undo for every change.** Receipts, checkpoints or per-operation undo, compared with this
  project's revisions, trash and saved menu versions.
- **Per-user binding.** Tying a connection to a chosen WordPress user and its capabilities.
- **Client registration.** Dynamic client registration makes connecting easier; this
  project accepts only pre-registered clients, a deliberate trade-off described in
  [security.md](security.md).

## What this project adds in its own niche

- Reproducible AWS deployment: Terraform for a Lightsail instance, optional Route 53
  records, and SES for the site's mail ([infra/](../infra/), [operations.md](operations.md)).
- A separate service with its own system user and systemd sandbox, a loopback-only
  WordPress credential, and images decoded in a separate, network-restricted worker
  process ([architecture.md](architecture.md)).
- Owner consent with a password and an authenticator code, pre-registered clients, and
  tokens bound to this server ([security.md](security.md)).

## Comparing options

The same short test works for any of them, on a test site:

1. Connect a hosted ChatGPT or Claude account with OAuth, without local processes.
2. Read a post, then create a draft with text and an image.
3. Change SEO fields and a translation.
4. Try an operation the connection should not allow, and check that it is refused.
5. Undo a change, revoke the connection, and review the audit trail.

The last step tells the most: a large tool set is easy to offer, while the permission
model and the way back from a mistake matter more on a public site.
