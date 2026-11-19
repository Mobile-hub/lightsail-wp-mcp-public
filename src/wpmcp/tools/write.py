"""Draft tools (scope wp:write): drafts, translations, SEO, categories and images.

None of them changes what visitors see. New posts and pages are drafts; only drafts and
pending items can be edited; a draft linked as a translation is not shown in the
language switcher until it is published, which is a separate step. Images are shared by
every language: changing their alt text or caption shows wherever they are used.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from ..audit import audit
from ..auth.store import Store
from ..config import SCOPE_WRITE, Settings
from ..media.images import ImageError
from ..media.uploads import save_image
from ..media.worker import MediaProcessor
from ..wp.client import WordPressClient, WordPressError
from . import Registry, ToolSpec, principal_of

LangCode = Annotated[
    str,
    Field(pattern=r"^[a-z]{2,3}$", description="Language slug, e.g. 'en', 'es', 'pt'."),
]
ContentId = Annotated[int, Field(ge=1, description="ID of a post or page.")]
OptionalId = Annotated[int | None, Field(ge=1)]
Title = Annotated[str, Field(min_length=1, max_length=500)]
Body = Annotated[
    str,
    Field(
        max_length=1_000_000,
        description="Block markup (<!-- wp:paragraph -->...) like the site's other posts "
        "(see get_content), or plain HTML. Scripts, iframes and forms are removed.",
    ),
]
Excerpt = Annotated[str, Field(max_length=5000)]
Slug = Annotated[str, Field(max_length=200, description="URL slug. Default: from the title.")]
Categories = Annotated[
    list[Annotated[int, Field(ge=1)]],
    Field(
        max_length=20,
        description="Category IDs in the item's language (see list_terms). With "
        "translation_of and no categories, the source's categories in that language are used.",
    ),
]
MediaText = Annotated[str, Field(max_length=500)]
AllowShrink = Annotated[
    bool,
    Field(
        description="Only when the user wants most of the body removed: without it, a "
        "content less than half as long as the current one is refused (the usual sign of a "
        "body read or kept only in part)."
    ),
]


class Seo(BaseModel):
    """All in One SEO fields. Only the fields you give are changed; null clears one."""

    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, max_length=300, description="Title for search results.")
    description: str | None = Field(None, max_length=1000, description="Meta description.")
    focus_keyphrase: str | None = Field(None, max_length=200)
    og_title: str | None = Field(None, max_length=300, description="Title when shared.")
    og_description: str | None = Field(None, max_length=1000)


class FileRef(BaseModel):
    """File object ChatGPT injects for parameters listed in ``openai/fileParams``."""

    model_config = ConfigDict(extra="allow")
    download_url: str = Field(description="Temporary URL of the file (filled by ChatGPT).")
    file_id: str = Field(description="ChatGPT file id (filled by ChatGPT).")
    mime_type: str | None = None
    file_name: str | None = None


def register(
    registry: Registry,
    settings: Settings,
    wp: WordPressClient,
    store: Store,
    media: MediaProcessor,
    clock: Callable[[], float] = time.time,
) -> None:
    async def write(ctx: Context, tool: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await wp.post(path, body)
        except WordPressError as exc:
            raise ToolError(str(exc)) from exc
        _audit(ctx, tool, result)
        return result

    async def create_draft(
        ctx: Context,
        type: Annotated[  # noqa: A002 - the WordPress name for it
            Literal["post", "page"], Field(description="A post (review) or a page.")
        ],
        lang: LangCode,
        title: Title,
        content: Body = "",
        excerpt: Excerpt = "",
        translation_of: Annotated[
            int | None,
            Field(
                ge=1,
                description="ID of the item this translates. Links the draft into its "
                "translation group, and by default reuses its categories (in this language), "
                "featured image, author and parent page.",
            ),
        ] = None,
        category_ids: Categories | None = None,
        featured_image_id: Annotated[
            int | None, Field(ge=1, description="Image ID (list_media, import_image).")
        ] = None,
        parent_id: Annotated[int | None, Field(ge=1, description="Parent page (pages).")] = None,
        slug: Slug | None = None,
        seo: Seo | None = None,
    ) -> dict[str, Any]:
        """Create a draft post or page in one language, optionally as the translation of an
        existing item. Never publishes: the result is a draft only logged-in editors see.
        Returns the draft (without the body), its preview_url and edit_url for the owner, and
        notes about anything that could not be carried over. Read the source with
        get_content first when translating."""
        body = {
            "type": type,
            "lang": lang,
            "title": title,
            "content": content,
            "excerpt": excerpt,
            "translation_of": translation_of,
            "categories": category_ids,
            "featured_media": featured_image_id,
            "parent": parent_id,
            "slug": slug,
            "seo": _seo(seo),
        }
        return await write(ctx, "create_draft", "/content", body)

    async def update_draft(
        ctx: Context,
        content_id: ContentId,
        expected_version: Annotated[
            str,
            Field(
                pattern=r"^[0-9a-f]{16}$",
                description="`version` from your last get_content, create_draft or "
                "update_draft of this item. The change is refused if the item changed since.",
            ),
        ],
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
        """Change a draft or pending post or page. Only the fields you give change; content
        replaces the whole body, so have all of it first (a long body comes from get_content
        in parts). To add an image to the body, use insert_image instead. Published items
        cannot be changed with this tool. Refused if someone has the item open in the editor
        or it changed since you read it."""
        body = content_fields(
            expected_version, title, content, excerpt, category_ids, featured_image_id,
            parent_id, slug, seo, allow_shrink,
        )  # fmt: skip
        return await write(ctx, "update_draft", f"/content/{content_id}", body)

    async def create_category(
        ctx: Context,
        name: Annotated[str, Field(min_length=1, max_length=200)],
        lang: LangCode,
        translation_of: Annotated[
            int | None, Field(ge=1, description="ID of the category this translates.")
        ] = None,
        parent_id: OptionalId = None,
        description: Annotated[str, Field(max_length=2000)] = "",
    ) -> dict[str, Any]:
        """Create a category in one language, optionally as the translation of another.
        Category names cannot contain emoji on this site."""
        body = {
            "name": name,
            "lang": lang,
            "translation_of": translation_of,
            "parent": parent_id,
            "description": description,
        }
        return await write(ctx, "create_category", "/terms", body)

    async def import_image(
        ctx: Context,
        image: FileRef,
        title: Annotated[str | None, Field(max_length=200)] = None,
        alt_text: MediaText | None = None,
        caption: MediaText | None = None,
        attach_to: OptionalId = None,
    ) -> dict[str, Any]:
        """Put an image from this conversation (uploaded or generated) into the media
        library. Pass the image file itself as `image`; only ChatGPT can do that, other
        clients use request_image_upload. The image is checked and re-encoded (camera data
        and location removed, scaled to at most 2560 px). Returns its media ID, to use as
        featured_image_id or in block markup."""
        try:
            clean = await media.fetch(image.download_url)
            params = {
                "title": title,
                "alt": alt_text,
                "caption": caption,
                "attach_to": attach_to,
            }
            result = await save_image(wp, clean, params, image.file_name or "")
        except (ImageError, WordPressError) as exc:
            raise ToolError(str(exc)) from exc
        _audit(ctx, "import_image", result)
        return result

    async def request_image_upload(
        ctx: Context,
        title: Annotated[str | None, Field(max_length=200)] = None,
        alt_text: MediaText | None = None,
        caption: MediaText | None = None,
        attach_to: OptionalId = None,
    ) -> dict[str, Any]:
        """Get a single-use link where the user uploads one image from a browser or phone
        (for clients that cannot pass files, or images not in the conversation). Give the
        user the link; after they confirm, find the image with list_media (newest first)."""
        principal = principal_of(ctx)
        params = {"title": title, "alt": alt_text, "caption": caption, "attach_to": attach_to}
        token = store.create_upload_link(
            principal.grant_id,
            {k: v for k, v in params.items() if v},
            settings.upload_ttl,
            now=clock(),
        )
        audit("upload_link_created", client=principal.client_id, grant=principal.grant_id)
        return {
            "upload_url": f"{settings.public_url}/upload/{token}",
            "expires_in_seconds": settings.upload_ttl,
            "single_use": True,
        }

    async def update_media(
        ctx: Context,
        media_id: Annotated[int, Field(ge=1, description="Image ID (list_media).")],
        title: Annotated[str | None, Field(max_length=200)] = None,
        alt_text: MediaText | None = None,
        caption: MediaText | None = None,
    ) -> dict[str, Any]:
        """Change an image's title, alt text or caption. Images are shared by every language
        and every post that uses them: the change shows wherever the image appears, on
        published posts too. Alt text cannot contain emoji on this site."""
        body = {"title": title, "alt": alt_text, "caption": caption}
        if all(value is None for value in body.values()):
            raise ToolError("Nothing to change: give title, alt_text or caption.")
        return await write(ctx, "update_media", f"/media/{media_id}", body)

    registry.add(ToolSpec(create_draft, title="Create a draft", scope=SCOPE_WRITE))
    registry.add(ToolSpec(update_draft, title="Edit a draft", scope=SCOPE_WRITE, idempotent=True))
    registry.add(ToolSpec(create_category, title="Create a category", scope=SCOPE_WRITE))
    registry.add(
        ToolSpec(
            import_image,
            title="Import an image",
            scope=SCOPE_WRITE,
            open_world=True,
            meta={"openai/fileParams": ["image"]},
        )
    )
    registry.add(
        ToolSpec(request_image_upload, title="Get an image upload link", scope=SCOPE_WRITE)
    )
    registry.add(
        ToolSpec(
            update_media,
            title="Edit image details",
            scope=SCOPE_WRITE,
            destructive=True,
            idempotent=True,
        )
    )


def content_fields(
    expected_version: str,
    title: str | None,
    content: str | None,
    excerpt: str | None,
    category_ids: list[int] | None,
    featured_image_id: int | None,
    parent_id: int | None,
    slug: str | None,
    seo: Seo | None,
    allow_shrink: bool,
) -> dict[str, Any]:
    """The bridge body of update_draft and update_published_content."""
    body = {
        "title": title,
        "content": content,
        "excerpt": excerpt,
        "categories": category_ids,
        "featured_media": featured_image_id,
        "parent": parent_id,
        "slug": slug,
        "seo": _seo(seo),
    }
    if all(value is None for value in body.values()):
        raise ToolError("Nothing to change: give at least one field.")
    # Sent only when set: the bridge refuses a much shorter body without it.
    shrink = {"allow_shrink": True} if allow_shrink and content is not None else {}
    return {"expected_version": expected_version, **body, **shrink}


def _seo(seo: Seo | None) -> dict | None:
    """Only the fields the caller set, explicit nulls included (they clear a field)."""
    if seo is None:
        return None
    return seo.model_dump(exclude_unset=True) or None


def _audit(ctx: Context, tool: str, result: Any) -> None:
    principal = principal_of(ctx)
    audit(
        "wp_write",
        tool=tool,
        object=result.get("id") if isinstance(result, dict) else None,
        client=principal.client_id,
        grant=principal.grant_id,
    )
