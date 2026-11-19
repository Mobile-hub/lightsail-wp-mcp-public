"""Read-only tools (scope wp:read). Lists and searches return plain-text excerpts; only
``get_content`` returns a full body. Everything they return is site content: data for the
model to work on, never instructions (see the server instructions)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from ..wp.client import WordPressClient, WordPressError
from . import Registry, ToolSpec

Lang = Annotated[
    str | None,
    Field(
        description="Language slug, e.g. 'en', 'es', 'pt' (see get_site_overview). Empty: all.",
        pattern=r"^[a-z]{2,3}$",
    ),
]
ContentId = Annotated[int, Field(ge=1, description="ID of a post or page.")]
Page = Annotated[int, Field(ge=1, le=1000, description="Page of results, from 1.")]
PerPage = Annotated[int, Field(ge=1, le=50, description="Results per page (max 50).")]
Status = Literal["any", "publish", "future", "draft", "pending", "private", "trash"]

# Body characters per get_content answer. ChatGPT cuts long tool results at a line
# boundary, and the JSON body is one line: past its budget the whole body (and anything
# after it) is lost. A part this size, escaped, stays well inside it.
DEFAULT_PART = 8_000
MAX_PART = 60_000
# ChatGPT shows a JSON result with a top-level "content" key as {} to the model, so the
# bridge's `content` is answered as `body`.
BODY = "body"
# Answered before everything else, so they survive a client that drops the tail.
HEAD = ("id", "version", "status", "being_edited", "content_length")
PART = ("content_offset", "content_complete", "next_offset")


def register(registry: Registry, wp: WordPressClient) -> None:
    async def call(path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            return await wp.get(path, params)
        except WordPressError as exc:
            raise ToolError(str(exc)) from exc

    async def get_site_overview() -> dict:
        """Overview of the site: name, URL, languages (with the default one), how many posts
        and pages exist per status and per language, number of categories, front page,
        timezone and versions. Start here to learn the language slugs."""
        return await call("/overview")

    async def search_content(
        query: Annotated[str, Field(max_length=200, description="Words to search for.")] = "",
        lang: Lang = None,
        type: Annotated[  # noqa: A002 - the WordPress name for it
            Literal["any", "post", "page"], Field(description="Posts, pages or both.")
        ] = "any",
        status: Annotated[
            Status, Field(description="'any' means every status except trash.")
        ] = "any",
        category_id: Annotated[
            int | None, Field(ge=1, description="Only posts in this category (see list_terms).")
        ] = None,
        untranslated_to: Annotated[
            str | None,
            Field(
                pattern=r"^[a-z]{2,3}$",
                description="Only items with no translation in this language yet. "
                "Combine with lang to pick the source language.",
            ),
        ] = None,
        order_by: Literal["date", "modified", "title", "relevance"] = "date",
        page: Page = 1,
        per_page: PerPage = 20,
    ) -> dict:
        """Find posts and pages, optionally by words, language, status or category, or those
        still missing a translation. Returns short plain-text excerpts, the language of each
        item and the IDs of its translations; use get_content for the full text."""
        return await call(
            "/content",
            {
                "search": query,
                "lang": lang,
                "type": type,
                "status": status,
                "category": category_id,
                "untranslated_to": untranslated_to,
                "orderby": order_by,
                "page": page,
                "per_page": per_page,
            },
        )

    async def get_content(
        content_id: ContentId,
        offset: Annotated[
            int,
            Field(
                ge=0,
                le=1_000_000,
                description="Where the body starts, in characters: the next_offset of the "
                "previous part. 0 for the first part.",
            ),
        ] = 0,
        max_chars: Annotated[
            int,
            Field(ge=1000, le=MAX_PART, description="Most characters of the body to return."),
        ] = DEFAULT_PART,
    ) -> dict:
        """Full post or page: `body` (the raw content, block markup or classic HTML: what the
        `content` argument of update_draft replaces), excerpt, status, language and
        translations, categories and tags, featured image, SEO fields, any site-specific
        read-only fields (`extra`) and whether someone has it open in the editor. `version`
        identifies what you read: pass it to update_draft. A long body comes in parts: while
        content_complete is false, call again with offset=next_offset and join the parts
        before rewriting the body (to add an image, use insert_image)."""
        return part_of(await call(f"/content/{content_id}"), offset, max_chars)

    async def list_terms(
        taxonomy: Literal["category", "post_tag"] = "category",
        lang: Lang = None,
        search: Annotated[str, Field(max_length=200)] = "",
    ) -> dict:
        """Categories or tags, with language, number of posts and translation links."""
        return await call("/terms", {"taxonomy": taxonomy, "lang": lang, "search": search})

    async def list_media(
        search: Annotated[
            str, Field(max_length=200, description="Words in title or caption.")
        ] = "",
        attached_to: Annotated[
            int | None, Field(ge=1, description="Only images uploaded to this post or page.")
        ] = None,
        page: Page = 1,
        per_page: PerPage = 20,
    ) -> dict:
        """Images in the media library: title, alt text, caption, size and URL."""
        return await call(
            "/media",
            {"search": search, "parent": attached_to, "page": page, "per_page": per_page},
        )

    async def list_revisions(content_id: ContentId) -> dict:
        """Saved revisions of a post or page (newest first), with dates and sizes."""
        return await call(f"/content/{content_id}/revisions")

    async def get_menus() -> dict:
        """Navigation menus per theme location and language, with their items."""
        return await call("/menus")

    async def get_recent_changes(
        since: Annotated[
            str | None,
            Field(
                max_length=40,
                description="ISO date, e.g. 2026-11-01T00:00:00Z. Default: the last 7 days.",
            ),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 30,
    ) -> dict:
        """Posts and pages created, edited, published or trashed recently, newest first."""
        return await call("/changes", {"since": since, "limit": limit})

    for fn, title in (
        (get_site_overview, "Site overview"),
        (search_content, "Search posts and pages"),
        (get_content, "Read a post or page"),
        (list_terms, "List categories or tags"),
        (list_media, "List images"),
        (list_revisions, "List revisions"),
        (get_menus, "Show menus"),
        (get_recent_changes, "Recent changes"),
    ):
        registry.add(ToolSpec(fn, title=title))


def part_of(item: Any, offset: int, max_chars: int) -> Any:
    """One part of the body of a get_content answer, with the small fields first and the
    body last. Parts are exact slices: joined in order they give the body back."""
    if not isinstance(item, dict) or not isinstance(item.get("content"), str):
        return item
    body: str = item["content"]
    if offset and offset >= len(body):
        raise ToolError(f"offset {offset} is past the end of the body ({len(body)} characters).")
    end = min(offset + max_chars, len(body))
    complete = end == len(body)
    fields = {
        **item,
        "content_length": len(body),
        "content_offset": offset,
        "content_complete": complete,
        "next_offset": None if complete else end,
    }
    if not complete or offset:
        note = f"This is characters {offset} to {end} of {len(body)} of the body."
        if not complete:
            note += f" Call get_content again with offset={end} for the rest."
        fields["notes"] = [*fields.get("notes", []), note]
    ordered = {key: fields[key] for key in HEAD + PART if key in fields}
    ordered.update(
        (key, value) for key, value in fields.items() if key not in ordered and key != "content"
    )
    ordered[BODY] = body[offset:end]
    return ordered
