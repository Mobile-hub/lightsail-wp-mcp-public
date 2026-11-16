# Project brief

Let the owner of one self-hosted WordPress site operate it from ChatGPT and Claude
through one remote MCP server, with strict security.

## Goals
- Create, edit, translate (Polylang), publish and trash posts and pages; SEO fields
  (All in One SEO), categories, media, navigation menus.
- Direct publication is in scope (phase 5): with a write connection the assistant may
  publish without a human review step, so the safety nets are revisions, the trash,
  snapshots, audit lines and per-connection revocation.
- One owner. No collaborators, no multi-user identity model.

## Non-goals
- Users, plugins, themes, options, code, permanent deletes.
- Maintenance actions (updates, snapshots): a later, optional tier of fixed actions.
- Many sites or many owners per installation: one service per site.
