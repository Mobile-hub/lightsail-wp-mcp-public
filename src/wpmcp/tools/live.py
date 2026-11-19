"""Live tools (scope wp:write): what changes the site visitors see.

Publish, schedule, unpublish, edit published items, insert an image into a body (drafts
too), restore a revision, trash and untrash, link translations, and change or restore a
menu. Each change names the version it was based on, is refused for protected items
(WPMCP_PROTECTED_IDS here; the front, posts
and privacy pages and their translations in the bridge), counts against a separate, lower
hourly budget, and can be undone: revisions, the trash, saved menu versions. The bridge
mails the site owner a digest of these changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from ..config import SCOPE_WRITE, Settings
from ..wp.client import WordPressClient, WordPressError
from . import Registry, ToolSpec
from .write import (
    AllowShrink,
    Body,
    Categories,
    ContentId,
    Excerpt,
    LangCode,
    Seo,
    Slug,
    Title,
    _audit,
    content_fields,
)

Version = Annotated[
    str,
    Field(
        pattern=r"^[0-9a-f]{16}$",
        description="`version` from your last get_content (or write) of this item. The change "
        "is refused if the item changed since.",
    ),
]
MenuVersion = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{16}$", description="`version` of the menu from get_menus."),
]


class MenuItem(BaseModel):
    """One item of a menu, in order. `parent` is the position (from 0) of an earlier item."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["post", "page", "category", "link", "keep"] = Field(
        description="post/page/category: by id, in the menu's language, published. link: url "
        "and title. keep: an existing item of this menu by its id (get_menus), unchanged "
        "except for an optional new title; use it for the language switcher and any item of "
        "another kind."
    )
    id: int | None = Field(None, ge=1)
    url: str | None = Field(
        None, max_length=500, description="For links: https://... or a site path /..."
    )
    title: str | None = Field(
        None, max_length=200, description="Label. Default: the item's own title."
    )
    parent: int | None = Field(None, ge=0)


def register(
    registry: Registry,
    settings: Settings,
    wp: WordPressClient,
) -> None:
    def guard(content_id: int) -> None:
        if content_id in settings.protected_ids:
            raise ToolError(
                f"Refused: item {content_id} is protected by the site owner. Tell the user to "
                "change it in wp-admin."
            )

    async def post(ctx: Context, tool: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await wp.post(path, body)
        except WordPressError as exc:
            raise ToolError(str(exc)) from exc
        _audit(ctx, tool, result)
        return result

    async def publish_content(
        ctx: Context, content_id: ContentId, expected_version: Version
    ) -> dict[str, Any]:
        """Publish a draft or pending post or page now: it becomes visible to everyone, and
        appears in the language switcher of its translations. Only with the user's explicit
        go-ahead for this item. Undo with unpublish_content."""
        return await post(
            ctx,
            "publish_content",
            f"/content/{content_id}/publish",
            {"expected_version": expected_version},
        )

    async def schedule_content(
        ctx: Context,
        content_id: ContentId,
        expected_version: Version,
        publish_at: Annotated[
            str,
            Field(
                max_length=40,
                description="When to publish, ISO 8601 with time zone, e.g. "
                "2026-12-01T09:00:00Z. Between 5 minutes and one year from now.",
            ),
        ],
    ) -> dict[str, Any]:
        """Schedule a draft, pending or already scheduled item to be published at a given
        time. Only with the user's explicit go-ahead. Undo with unpublish_content."""
        when = _utc(publish_at)
        return await post(
            ctx,
            "schedule_content",
            f"/content/{content_id}/schedule",
            {"expected_version": expected_version, "publish_at": when},
        )

    async def unpublish_content(
        ctx: Context, content_id: ContentId, expected_version: Version
    ) -> dict[str, Any]:
        """Take a published or scheduled item back to draft: visitors no longer see it and
        its translations stop linking to it. Only with the user's explicit go-ahead."""
        guard(content_id)
        return await post(
            ctx,
            "unpublish_content",
            f"/content/{content_id}/unpublish",
            {"expected_version": expected_version},
        )

    async def update_published_content(
        ctx: Context,
        content_id: ContentId,
        expected_version: Version,
        title: Title | None = None,
        content: Body | None = None,
        excerpt: Excerpt | None = None,
        category_ids: Categories | None = None,
        featured_image_id: Annotated[
            int | None, Field(ge=0, description="Image ID; 0 removes the featured image.")
        ] = None,
        parent_id: Annotated[int | None, Field(ge=0, description="0: no parent.")] = None,
        slug: Slug | None = None,
        seo: Seo | None = None,
        allow_shrink: AllowShrink = False,
    ) -> dict[str, Any]:
        """Change a published (or scheduled, or private) post or page: visitors see the
        change at once. Only the fields you give change; content replaces the whole body, so
        have all of it first (a long body comes from get_content in parts); to add an image,
        use insert_image instead. Changing the slug changes the URL. The state before is
        saved as a revision first and returned in `before` (with the old categories, image,
        slug and SEO fields). Only with the user's explicit go-ahead; for drafts use
        update_draft."""
        guard(content_id)
        body = content_fields(
            expected_version, title, content, excerpt, category_ids, featured_image_id,
            parent_id, slug, seo, allow_shrink,
        )  # fmt: skip
        return await post(ctx, "update_published_content", f"/content/{content_id}/live", body)

    async def insert_image(
        ctx: Context,
        content_id: ContentId,
        expected_version: Version,
        media_id: Annotated[int, Field(ge=1, description="Image ID (list_media, import_image).")],
        position: Annotated[
            Literal["start", "end", "after_text"],
            Field(
                description="Where the image goes: start or end of the body, or after_text: "
                "after the paragraph, heading or other block that contains after_text."
            ),
        ] = "end",
        after_text: Annotated[
            str | None,
            Field(
                min_length=3,
                max_length=200,
                description="With position after_text: a few words copied from the one block "
                "the image goes after (case and spacing do not matter).",
            ),
        ] = None,
        alt_text: Annotated[
            str | None,
            Field(
                max_length=500,
                description="Alt text for this use only, in the item's language. Default: "
                "the image's own alt text. The media library is not changed.",
            ),
        ] = None,
        caption: Annotated[str | None, Field(max_length=500)] = None,
        size: Literal["medium", "large", "full"] = "large",
    ) -> dict[str, Any]:
        """Add an image from the media library to the body of a post or page, without
        rewriting the body: the rest stays exactly as it was. Use it instead of update_draft
        or update_published_content to place an image, one call per item (each translation
        has its own body; give alt_text in its language). On a draft nothing becomes visible.
        On a published item visitors see it at once, so only with the user's explicit
        go-ahead; the state before is saved as a revision (`before.revision_id`)."""
        if (position == "after_text") != (after_text is not None):
            raise ToolError("after_text goes with position after_text, and only with it.")
        guard(content_id)
        body = {
            "expected_version": expected_version,
            "media_id": media_id,
            "position": position,
            "anchor_text": after_text,
            "alt": alt_text,
            "caption": caption,
            "size": size,
        }
        return await post(ctx, "insert_image", f"/content/{content_id}/insert-image", body)

    async def restore_revision(
        ctx: Context,
        content_id: ContentId,
        expected_version: Version,
        revision_id: Annotated[int, Field(ge=1, description="Revision ID (list_revisions).")],
    ) -> dict[str, Any]:
        """Put a post or page's title, content and excerpt back to a saved revision (on a
        published item, visitors see it at once). The current state is saved as a revision
        first. Only with the user's explicit go-ahead."""
        guard(content_id)
        return await post(
            ctx,
            "restore_revision",
            f"/content/{content_id}/restore-revision",
            {"expected_version": expected_version, "revision_id": revision_id},
        )

    async def trash_content(
        ctx: Context, content_id: ContentId, expected_version: Version
    ) -> dict[str, Any]:
        """Move a post or page to the trash (never deleted permanently: the owner empties the
        trash in wp-admin, or WordPress after 30 days). Its translations stay. Only with the
        user's explicit go-ahead. Undo with restore_from_trash."""
        guard(content_id)
        return await post(
            ctx,
            "trash_content",
            f"/content/{content_id}/trash",
            {"expected_version": expected_version},
        )

    async def restore_from_trash(ctx: Context, content_id: ContentId) -> dict[str, Any]:
        """Bring a trashed post or page back, as a draft (publish it again to show it)."""
        return await post(ctx, "restore_from_trash", f"/content/{content_id}/untrash", {})

    async def link_translations(
        ctx: Context,
        translations: Annotated[
            dict[LangCode, Annotated[int, Field(ge=1)]],
            Field(
                min_length=2,
                max_length=10,
                description="Language -> post or page ID, e.g. {'en': 12, 'es': 34}. Each item "
                "must already be in that language. Existing links are kept.",
            ),
        ],
    ) -> dict[str, Any]:
        """Link existing posts (or pages) as translations of each other, so the language
        switcher and hreflang connect them. Refused if two items would be the same
        language's translation. Only with the user's explicit go-ahead."""
        for content_id in translations.values():
            guard(content_id)
        return await post(ctx, "link_translations", "/translations", {"items": translations})

    async def apply_menu(
        ctx: Context,
        menu_id: Annotated[int, Field(ge=1, description="Menu ID (get_menus).")],
        expected_version: MenuVersion,
        items: Annotated[list[MenuItem], Field(max_length=60)],
    ) -> dict[str, Any]:
        """Replace the items of a menu shown by a theme location (see get_menus) with this
        list, in order. Items not listed are removed (the language switcher is kept). The
        previous version is saved: restore_menu puts it back. Never changes which menu a
        location shows. Only with the user's explicit go-ahead."""
        body = {
            "expected_version": expected_version,
            "items": [item.model_dump(exclude_none=True) for item in items],
        }
        return await post(ctx, "apply_menu", f"/menus/{menu_id}", body)

    async def restore_menu(
        ctx: Context, menu_id: Annotated[int, Field(ge=1, description="Menu ID (get_menus).")]
    ) -> dict[str, Any]:
        """Put a menu back to the version saved before its last change by apply_menu."""
        return await post(ctx, "restore_menu", f"/menus/{menu_id}/restore", {})

    # (tool, title, destructive, idempotent): a repeat of a version-guarded change is
    # refused as stale, and linking the same translations again changes nothing.
    for fn, title, destructive, idempotent in (
        (publish_content, "Publish", False, True),
        (schedule_content, "Schedule publication", False, True),
        (unpublish_content, "Unpublish", True, True),
        (update_published_content, "Edit published content", True, True),
        # A draft or a published item: counted as live either way, refused when protected.
        (insert_image, "Insert an image", True, True),
        (restore_revision, "Restore a revision", True, True),
        (trash_content, "Move to trash", True, True),
        (link_translations, "Link translations", True, True),
        (apply_menu, "Change a menu", True, True),
        # Each call puts back the version saved before the last apply_menu.
        (restore_menu, "Restore a menu", True, False),
    ):
        registry.add(
            ToolSpec(
                fn,
                title=title,
                scope=SCOPE_WRITE,
                destructive=destructive,
                live=True,
                idempotent=idempotent,
            )
        )
    # Back to draft: nothing becomes visible, so it is not a live change.
    registry.add(ToolSpec(restore_from_trash, title="Restore from trash", scope=SCOPE_WRITE))


def _utc(value: str) -> str:
    """ISO 8601 with a time zone, as UTC 'Z'. The bridge checks the range against the
    server's clock (between 5 minutes and a year ahead)."""
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError("publish_at must be an ISO 8601 date, e.g. 2026-12-01T09:00:00Z.") from exc
    if when.tzinfo is None:
        raise ToolError("publish_at needs a time zone, e.g. 2026-12-01T09:00:00Z or +01:00.")
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
