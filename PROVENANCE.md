# Provenance

Parts of this repository come from
[editorial.workflow](https://github.com/Machine-Learning-Labs/chatgpt-local-mcp-editorial.workflow)
(`mcp-oauth-gateway`, `editorial_assets`), at commit `dd7cd874e98c02023ed7ac07d89c72a6d45dde74`. This is a
deliberate fork, not a shared package: that project is stateless and local by rule, while
this one needs a database, per-connection revocation and a second factor.

| Here | From | How |
|---|---|---|
| `src/wpmcp/auth/tokens.py` | `src/mcp_oauth_gateway/tokens.py` | verbatim copy |
| `tests/unit/test_tokens.py` | `tests/test_tokens.py` | verbatim, import path changed |
| `src/wpmcp/net.py` | `src/mcp_oauth_gateway/network.py` | adapted: Unix-socket peer, no `X-Forwarded-Proto` |
| `src/wpmcp/auth/pages.py` | `src/mcp_oauth_gateway/pages.py` | adapted: TOTP field, access choice |
| `src/wpmcp/auth/oauth.py` | `src/mcp_oauth_gateway/oauth.py` | rewritten around a store (see below) |
| `src/wpmcp/auth/bearer.py` | bearer check in `src/mcp_oauth_gateway/proxy.py` | rewritten as an ASGI gate |
| `src/wpmcp/app.py` | `src/mcp_oauth_gateway/app.py` | adapted: `GuardMiddleware`, body caps, no CORS |
| `bin/lib.sh`, `.shellcheckrc` | same paths | trimmed |
| `src/wpmcp/media/fetch.py` | `src/editorial_assets/fetch.py` | verbatim, error class renamed; host list moved in |
| `src/wpmcp/media/images.py` | `src/editorial_assets/images.py` | adapted: same sniffing and pixel cap, then re-encoding (metadata removed, scaled to 2560 px) instead of keeping the original bytes; lower caps (10 MB, 16 MP); WebP kept |
| `src/wpmcp/tools/write.py` `FileRef`, `import_image` | `src/editorial_assets/server.py` | adapted: the file goes to WordPress through the image worker |

A security fix in the original `tokens.py` must be ported here by hand.

## What changed in the OAuth server, and why

| Original | Here | Reason |
|---|---|---|
| Dynamic client registration, client id is a signed token | Clients created with `wpmcp client add`, secrets stored hashed | An open registration endpoint lets anyone start a flow and send the owner a consent link |
| Redirect URI prefixes, loopback allowed | Exact match against the registration, https only | No local clients on a public server |
| One shared password | Password (scrypt) + TOTP, both always evaluated | A permanent public endpoint that can publish |
| In-memory failure counter | Failures persisted, per address and overall | A restart must not reset a lockout |
| One scope `mcp`; no scope requested means all | `wp:read` / `wp:write`; granted = requested ∩ the owner's choice | Read-only connections; the original default granted everything |
| Signed, stateless codes and refresh tokens | Random values stored hashed; rotation with reuse detection; grants revocable | Per-connection revocation, replay detection |
| Issuer from the `Host` header | Fixed `WPMCP_PUBLIC_URL` | A fixed domain needs no Host-derived origin |
| Proxy to an upstream MCP with header rewriting | MCP server in the same process | Both halves are ours; scopes cannot be forged between processes |

## Licence of the original

editorial.workflow is distributed under the MIT License; its notice is reproduced here as
that licence requires for the copied and adapted files listed above.

```
MIT License

Copyright (c) 2026 editorial.workflow contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
