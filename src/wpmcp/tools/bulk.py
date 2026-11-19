"""Bulk tools (scope wp:write): one change to up to 50 posts and pages per call, without
reading or sending their bodies.

They "ensure" rather than overwrite: an item that already links to the target, or whose
SEO fields already have the values given, is answered as unchanged and not written, so a
repeated call changes nothing more. Each item names the version it was based on (from
search_content) and is checked on its own; the result lists only what changed (with the
new versions), what was unchanged (IDs) and what was refused (with the reason). Each item
counts against the hourly budgets as one change, on published and draft items alike; a
dry run counts nothing. A run that changed something is a batch that undo_bulk_change
puts back.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from ..audit import audit
from ..config import SCOPE_WRITE, Settings
from ..wp.client import WordPressClient, WordPressError
from . import Registry, ToolSpec, principal_of
from .write import Seo, _seo

MAX_ITEMS = 50
DryRun = Annotated[
    bool,
    Field(description="Only answer what would change (nothing is written or counted)."),
]


class Item(BaseModel):
    """One post or page of a bulk change."""

    model_config = ConfigDict(extra="forbid")
    content_id: int = Field(ge=1, description="ID of a post or page.")
    expected_version: str = Field(
        pattern=r"^[0-9a-f]{16}$",
        description="Its `version` from search_content (or get_content, or a previous change).",
    )


class SeoItem(Item):
    seo: Seo = Field(description="Only the fields given are set; null clears one.")


Items = Annotated[list[Item], Field(min_length=1, max_length=MAX_ITEMS)]
SeoItems = Annotated[list[SeoItem], Field(min_length=1, max_length=MAX_ITEMS)]


def changes(arguments: dict[str, Any]) -> int:
    """Budget units of a bulk call: one per item, none for a dry run."""
    if arguments.get("dry_run") is True:
        return 0
    items = arguments.get("items")
    return len(items) if isinstance(items, list) and items else 1


def register(registry: Registry, settings: Settings, wp: WordPressClient) -> None:
    def guard(items: list[Item]) -> None:
        protected = sorted({i.content_id for i in items} & settings.protected_ids)
        if protected:
            raise ToolError(
                f"Refused: items {protected} are protected by the site owner. Leave them out "
                "and tell the user to change them in wp-admin."
            )

    async def post(ctx: Context, tool: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await wp.post(path, body)
        except WordPressError as exc:
            raise ToolError(str(exc)) from exc
        if isinstance(result, dict) and not result.get("dry_run"):
            # Who changed how many items, under which batch; never what was written.
            counts = result.get("counts") or {}
            principal = principal_of(ctx)
            audit(
                "wp_write",
                tool=tool,
                object=result.get("batch_id"),
                items=counts.get("changed", counts.get("undone")),
                client=principal.client_id,
                grant=principal.grant_id,
            )
        return result

    async def bulk_add_links(
        ctx: Context,
        target_id: Annotated[
            int, Field(ge=1, description="The published post or page to link to.")
        ],
        html: Annotated[
            str,
            Field(
                min_length=1,
                max_length=5000,
                description="The block to add, as HTML with at least one <a href> to the "
                "target's address (its `link` in search_content), in the items' language. "
                "Use the same markup as items that already have it, e.g. <section "
                'class="..."><h2>...</h2><p>... <a href="...">...</a>.</p></section>.',
            ),
        ],
        items: Items,
        position: Annotated[
            Literal["end", "before_closing_tags"],
            Field(
                description="end: after the last block of the body. before_closing_tags: "
                "inside a body wrapped in an element, before the closing </div>, </article> "
                "or </main> tags that end it (where items that already link have it)."
            ),
        ] = "end",
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        """Add the same block with a link to one post or page (a series guide, a related
        page) to up to 50 posts or pages at once, without reading or sending their bodies.
        Items whose body already links to the target are left unchanged; the rest of each
        body is kept exactly. All items must be in the target's language: one call per
        language. Take each item's version from search_content. Try dry_run first. On
        published items visitors see the link at once, so only with the user's explicit
        go-ahead; the result has a batch_id for undo_bulk_change."""
        guard(items)
        body = {
            "target_id": target_id,
            "html": html,
            "position": position,
            "dry_run": dry_run,
            "items": [{"id": i.content_id, "expected_version": i.expected_version} for i in items],
        }
        return await post(ctx, "bulk_add_links", "/bulk/links", body)

    async def bulk_update_seo(
        ctx: Context, items: SeoItems, dry_run: DryRun = False
    ) -> dict[str, Any]:
        """Set SEO fields (title, description, focus keyphrase, Open Graph title and
        description; All in One SEO or SEOPress, whichever the site uses) of up to 50 posts
        or pages at once. Only the fields given that
        differ are written; items already as asked are left unchanged. Take each item's
        version from search_content. Try dry_run first. On published items search engines
        and visitors get the new fields at once, so only with the user's explicit
        go-ahead; the result has a batch_id for undo_bulk_change."""
        guard(items)
        body = {
            "dry_run": dry_run,
            "items": [
                {
                    "id": i.content_id,
                    "expected_version": i.expected_version,
                    "seo": _seo(i.seo) or {},
                }
                for i in items
            ],
        }
        return await post(ctx, "bulk_update_seo", "/bulk/seo", body)

    async def undo_bulk_change(
        ctx: Context,
        batch_id: Annotated[
            str,
            Field(
                pattern=r"^[0-9a-f]{12}$",
                description="batch_id from bulk_add_links or bulk_update_seo.",
            ),
        ],
    ) -> dict[str, Any]:
        """Put back the items of a bulk change (one of the last 10) that nobody has changed
        since: the body as it was before the link, or the SEO fields as they were. Items
        changed since are listed as skipped and left alone. Only with the user's explicit
        go-ahead."""
        return await post(ctx, "undo_bulk_change", f"/bulk/{batch_id}/undo", {})

    for fn, title, cost in (
        (bulk_add_links, "Add a link to many items", changes),
        (bulk_update_seo, "Set SEO fields of many items", changes),
        (undo_bulk_change, "Undo a bulk change", None),
    ):
        registry.add(
            ToolSpec(
                fn,
                title=title,
                scope=SCOPE_WRITE,
                destructive=True,
                live=True,
                idempotent=True,
                cost=cost,
            )
        )
