# Connecting clients

**ChatGPT: verified** on 2026-11-15 (pre-registered client with ID and secret, no
registration endpoint, read-and-write approval, tools called from a conversation, every
request from inside OpenAI's published ranges). **Claude: not yet connected**; its section
still comes from Anthropic's documentation.

Both clients need a pre-registered OAuth client: there is no dynamic registration.

## Register the client on the server

```bash
sudo wpmcp-admin client add --preset claude      # on the server; or: --preset chatgpt
```

It prints `client_id` and `client_secret` once. Paste both into the connector's settings.

| Preset | Redirect URI registered |
|---|---|
| `claude` | `https://claude.ai/api/mcp/auth_callback` |
| `chatgpt` | `https://chatgpt.com/connector_platform_oauth_redirect` |

If a product uses a different callback, the consent screen answers "redirect_uri does not
match the registration". Register that exact URI with
`sudo wpmcp-admin client add --name <name> --redirect-uri <uri>` and disable the old client.

## Claude (claude.ai, desktop, mobile)

Settings → Connectors → Add custom connector. URL: `https://<MCP_HOST>/mcp`. Under the
advanced settings, enter the OAuth client ID and client secret.

## ChatGPT

Add a custom MCP server. URL: `https://<MCP_HOST>/mcp`, authentication OAuth, with the
static client ID and secret. Token endpoint authentication: `client_secret_post` or
`client_secret_basic`. OIDC: off (this server is not an OpenID provider).

## Images

- **ChatGPT** passes an image from the conversation (uploaded or generated) straight to
  `import_image` (`openai/fileParams`).
- **Claude** cannot pass files to tools: it calls `request_image_upload` and gives you a
  single-use link (15 minutes) where you upload the image from a browser or phone. Then
  tell Claude it is uploaded.

Either way the image is checked and re-encoded on the server (camera data and location are
removed, at most 2560 px) before it reaches the media library.

## Approving a connection

The client opens the consent screen. Choose **Read only** or **Read and write**, then
enter the owner password and the authenticator code. A connection approved as read-only
never sees the write tools; to change that, remove the connector and add it again.

## Reviewing and revoking

```bash
sudo wpmcp-admin grant list        # approved connections, with the last token refresh
sudo wpmcp-admin grant revoke <id> # end one
sudo wpmcp-admin revoke-all        # end all
```

## Still to check

- Claude: the whole connection (pre-registered client, scopes, a read-only choice).
- Both: how each asks for confirmation before write tools, and that ChatGPT's file
  parameter reaches `import_image`.
- Claude's source addresses inside Anthropic's published ranges, before the CIDR allowlist
  is switched from `monitor` to `enforce`.
