# Product context

The target is a multilingual WordPress site (Polylang, one post per language linked as
translations, All in One SEO) whose owner writes with AI assistants and wants the
assistant to carry the work into WordPress: drafts, translations, SEO, images,
publication, menu upkeep.

The alternative this replaces is scripts writing straight into the database, which is
fragile and does not match Polylang's data model. Everything here goes through
WordPress's own APIs, as a least-privilege user.

What "good" looks like: the owner connects Claude or ChatGPT once, approves it with
password and authenticator code, and can then say "publish this review in the three
languages" and get linked posts, with a revision trail and a way back.

Site-specific data (custom plugin fields) is added by each site through the
`wpmcp_bridge_extra_fields` filter, not built into the bridge.
