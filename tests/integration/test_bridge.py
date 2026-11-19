"""Against the local WordPress stack (``bin/dev up``): the bridge plugin's access rules and
the read tools end to end. Skipped when the stack is not running.

WPMCP_IT_WP_URL and WPMCP_IT_WP_PASSWORD_FILE override the defaults below.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from tests.conftest import BASE, access_token, make_settings, rpc, rpc_result
from wpmcp.app import create_app

ROOT = Path(__file__).resolve().parents[2]
WP_URL = os.environ.get("WPMCP_IT_WP_URL", "http://127.0.0.1:8081")
PASSWORD_FILE = Path(
    os.environ.get("WPMCP_IT_WP_PASSWORD_FILE", ROOT / "var" / "dev-wp-app-password")
)


def _stack_up() -> bool:
    if not PASSWORD_FILE.is_file():
        return False
    try:
        return httpx.get(f"{WP_URL}/wp-json/", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _stack_up(), reason="local WordPress stack not running")


@pytest.fixture(scope="module")
def password() -> str:
    return PASSWORD_FILE.read_text().strip()


@pytest.fixture(scope="module")
def wp(password):
    with httpx.Client(base_url=f"{WP_URL}/wp-json", auth=("wpmcp", password), timeout=20) as c:
        yield c


@pytest.mark.parametrize(
    "route",
    [
        "/wp/v2/users/me",
        "/wp/v2/users",
        "/wp/v2/settings",
        "/wp/v2/plugins",
        "/wp/v2/themes",
        "/wp/v2/posts",
        "/wp/v2/comments",
        "/wp/v2/menus",
        "/wp/v2/menu-items",
        "/wp/v2/menu-locations",
        "/aioseo/v1/htaccess",
        "/pll/v1/languages",
        "/",
    ],
)
def test_integration_user_is_confined_to_the_bridge(wp, route):
    response = wp.get(route)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "wpmcp_route_denied"


def test_integration_user_reaches_the_bridge(wp):
    response = wp.get("/wpmcp/v1/overview")
    assert response.status_code == 200, response.text
    assert {lang["slug"] for lang in response.json()["languages"]} >= {"en", "es", "pt"}


def test_wrong_application_password_is_refused():
    response = httpx.get(f"{WP_URL}/wp-json/wpmcp/v1/overview", auth=("wpmcp", "x" * 24))
    assert response.status_code == 401


def test_bridge_needs_authentication():
    response = httpx.get(f"{WP_URL}/wp-json/wpmcp/v1/overview")
    assert response.status_code == 401


def test_bridge_rejects_unknown_language(wp):
    response = wp.get("/wpmcp/v1/content", params={"lang": "fr"})
    assert response.status_code == 400
    assert response.json()["code"] == "wpmcp_bad_lang"


def test_content_outside_posts_and_pages_is_not_found(wp):
    response = wp.get("/wpmcp/v1/content/999999")
    assert response.status_code == 404


# --- the read tools, through the real MCP endpoint and the real WordPress client


@pytest.fixture(scope="module")
def mcp(password):
    from tests.conftest import Clock, make_store

    clock = Clock()
    store = make_store(clock)
    settings = make_settings(wp_url=WP_URL, wp_password=password)
    app = create_app(settings, store=store, clock=clock)
    with TestClient(app, base_url=BASE) as client:
        token, _ = access_token(app, clock, scope="wp:read")
        yield client, token
    store.close()


def tool(mcp, name, /, **arguments):
    client, token = mcp
    result = rpc_result(rpc(client, token, "tools/call", {"name": name, "arguments": arguments}))
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def test_overview_tool(mcp):
    overview = tool(mcp, "get_site_overview")
    assert "content" not in overview and overview["counts"]["post"]["by_status"]
    assert [lang["slug"] for lang in overview["languages"] if lang["default"]] == ["en"]
    assert overview["versions"]["bridge"]


def test_search_by_language_returns_linked_translations(mcp):
    found = tool(mcp, "search_content", lang="es", query="Principito")
    assert found["total"] == 1
    item = found["items"][0]
    assert item["lang"] == "es"
    assert set(item["translations"]) == {"en", "es", "pt"}
    assert "content" not in item


def test_untranslated_filter(mcp):
    missing = tool(mcp, "search_content", lang="en", untranslated_to="es")
    titles = {item["title"] for item in missing["items"]}
    assert "A Bear Called Paddington" in titles
    assert "The Little Prince" not in titles


def test_get_content_returns_the_body_and_seo(mcp):
    item = tool(mcp, "search_content", lang="en", query="Paddington")["items"][0]
    content = tool(mcp, "get_content", content_id=item["id"])
    assert "🐻" in content["body"] and "content" not in content
    assert content["content_format"] == "blocks"
    assert content["categories"][0]["name"] == "Adventure"
    assert set(content["seo"]) >= {"title", "description", "focus_keyphrase", "noindex"}
    assert content["being_edited"] is False
    assert content["extra"] is None  # no wpmcp_bridge_extra_fields filter on the dev stack


def test_terms_revisions_media_menus_changes(mcp):
    terms = tool(mcp, "list_terms", lang="pt")
    assert all(term["lang"] == "pt" for term in terms["items"])
    item = tool(mcp, "search_content", lang="en", query="Paddington")["items"][0]
    assert tool(mcp, "list_revisions", content_id=item["id"])["post_id"] == item["id"]
    assert "items" in tool(mcp, "list_media")
    assert "menus" in tool(mcp, "get_menus")
    changes = tool(mcp, "get_recent_changes", since="2000-01-01T00:00:00Z")
    assert changes["total"] >= 4


# --- the draft tools, end to end (phase 4). Every item created here is deleted afterwards.

BLOCKS = """<!-- wp:heading {"level":3} -->
<h3 class="wp-block-heading">Porque é que gostamos</h3>
<!-- /wp:heading -->

<!-- wp:paragraph -->
<p>Um <strong>livro</strong>, <em>ilustrado</em>: <a href="https://x.org/">aqui</a> 🐻📚</p>
<!-- /wp:paragraph -->

<!-- wp:list -->
<ul class="wp-block-list"><!-- wp:list-item -->
<li>Para ler em voz alta</li>
<!-- /wp:list-item --></ul>
<!-- /wp:list -->

<!-- wp:quote -->
<blockquote class="wp-block-quote"><!-- wp:paragraph -->
<p>«Só se vê bem com o coração.»</p>
<!-- /wp:paragraph --></blockquote>
<!-- /wp:quote -->

<!-- wp:columns -->
<div class="wp-block-columns"><!-- wp:column -->
<div class="wp-block-column"><!-- wp:paragraph -->
<p>Idade: 6+</p>
<!-- /wp:paragraph --></div>
<!-- /wp:column --></div>
<!-- /wp:columns -->"""


def dev_wp(*args: str) -> None:
    """WP-CLI as the site admin in the dev stack (cleanup only)."""
    command = [str(ROOT / "bin" / "dev"), "wp", *args]
    subprocess.run(command, check=False, capture_output=True)  # noqa: S603 - fixed command


@pytest.fixture(scope="module")
def created():
    ids: dict[str, list[int]] = {"post": [], "term": [], "media": []}
    yield ids
    for post_id in ids["post"] + ids["media"]:
        dev_wp("post", "delete", str(post_id), "--force")
    for term_id in ids["term"]:
        dev_wp("term", "delete", "category", str(term_id))


@pytest.fixture(scope="module")
def writer(password):
    from tests.conftest import Clock, make_store

    clock = Clock()
    store = make_store(clock)
    # The whole module runs within one hour: more live changes than the default budget.
    settings = make_settings(wp_url=WP_URL, wp_password=password, write_budget=500, live_budget=500)
    app = create_app(settings, store=store, clock=clock)
    with TestClient(app, base_url=BASE) as client:
        token, _ = access_token(app, clock, scope="wp:read wp:write")
        yield client, token
    store.close()


def attempt(mcp, name, /, **arguments):
    client, token = mcp
    return rpc_result(rpc(client, token, "tools/call", {"name": name, "arguments": arguments}))


def find(mcp, title):
    return tool(mcp, "search_content", lang="en", query=title)["items"][0]


def test_translation_draft_is_linked_but_hidden_from_visitors(writer, created):
    source = find(writer, "Paddington")
    if "pt" in source["translations"]:
        pytest.skip("a pt translation of the fixture already exists (bin/dev reset)")
    draft = tool(
        writer,
        "create_draft",
        type="post",
        lang="pt",
        translation_of=source["id"],
        title="Um Urso Chamado Paddington 🐻",
        content=BLOCKS,
        seo={"title": "Paddington em português", "focus_keyphrase": "urso paddington"},
    )
    created["post"].append(draft["id"])
    assert draft["status"] == "draft" and draft["lang"] == "pt"
    assert draft["translations"] == {"en": source["id"], "pt": draft["id"]}
    # The source's category, in Portuguese; the author is the source's, not the integration user.
    assert [c["name"] for c in draft["categories"]] == ["Aventura PT"]
    assert draft["seo"]["title"] == "Paddington em português"
    assert draft["seo"]["focus_keyphrase"] == "urso paddington"
    assert "notes" not in draft, draft.get("notes")
    assert "content" not in draft and draft["preview_url"]

    # The block markup survives the save exactly, emoji included.
    stored = tool(writer, "get_content", content_id=draft["id"])
    assert stored["body"] == BLOCKS
    assert stored["title"] == "Um Urso Chamado Paddington 🐻"

    # Visitors see neither the draft nor a link to it.
    english = httpx.get(source["link"], follow_redirects=True, timeout=20)
    assert english.status_code == 200
    assert 'hreflang="pt"' not in english.text
    assert httpx.get(f"{WP_URL}/?p={draft['id']}", timeout=20).status_code == 404

    # A second translation into the same language is refused.
    again = attempt(
        writer, "create_draft", type="post", lang="pt", translation_of=source["id"], title="x"
    )
    assert again["isError"] is True and "already has a pt translation" in str(again)


def test_update_draft_checks_the_version_and_patches_seo(writer, created):
    draft = tool(writer, "create_draft", type="post", lang="en", title="wpmcp test draft")
    created["post"].append(draft["id"])
    first = tool(
        writer,
        "update_draft",
        content_id=draft["id"],
        expected_version=draft["version"],
        excerpt="Short summary",
        seo={"title": "Test SEO title", "focus_keyphrase": "test phrase"},
    )
    assert first["excerpt"] == "Short summary"
    second = tool(
        writer,
        "update_draft",
        content_id=draft["id"],
        expected_version=first["version"],
        seo={"description": "Only the description changes"},
    )
    assert second["seo"]["title"] == "Test SEO title"
    assert second["seo"]["focus_keyphrase"] == "test phrase"
    assert second["seo"]["description"] == "Only the description changes"

    stale = attempt(
        writer,
        "update_draft",
        content_id=draft["id"],
        expected_version=draft["version"],
        title="lost update",
    )
    assert stale["isError"] is True and "changed since that version was read" in str(stale)


def test_published_items_and_scripts(writer, created):
    published = tool(writer, "get_content", content_id=find(writer, "Paddington")["id"])
    refused = attempt(
        writer,
        "update_draft",
        content_id=published["id"],
        expected_version=published["version"],
        title="hijacked",
    )
    assert refused["isError"] is True and "only drafts and pending items" in str(refused)
    draft = tool(
        writer,
        "create_draft",
        type="page",
        lang="en",
        title="wpmcp test page",
        content="<p>Hello</p><script>alert(1)</script><iframe src='https://evil'></iframe>",
    )
    created["post"].append(draft["id"])
    assert any("WordPress changed the content" in note for note in draft["notes"])
    stored = tool(writer, "get_content", content_id=draft["id"])["body"]
    assert "<script" not in stored and "<iframe" not in stored


def test_categories_language_rules(writer, created):
    english = next(
        t for t in tool(writer, "list_terms", lang="en")["items"] if t["name"] == "Adventure"
    )
    wrong = attempt(
        writer, "create_draft", type="post", lang="es", title="x", category_ids=[english["id"]]
    )
    assert wrong["isError"] is True and "is in en, not es" in str(wrong)
    emoji = attempt(writer, "create_category", name="Ursos 🐻", lang="pt")
    assert emoji["isError"] is True and "emoji" in str(emoji)
    category = tool(writer, "create_category", name="wpmcp Clássicos", lang="pt")
    created["term"].append(category["id"])
    assert category["lang"] == "pt" and category["name"] == "wpmcp Clássicos"


def test_image_upload_and_details(writer, created):
    from io import BytesIO

    from PIL import Image

    image = Image.new("RGB", (64, 48), "purple")
    exif = Image.Exif()
    exif.get_ifd(0x8825).update({1: "N", 2: (40.0, 25.0, 0.0)})
    data = BytesIO()
    image.save(data, "JPEG", exif=exif)

    client, token = writer
    link = tool(writer, "request_image_upload", title="wpmcp test cover", alt_text="A cover")
    path = link["upload_url"].removeprefix(BASE)
    page = client.post(path, files={"file": ("IMG_1.JPG", data.getvalue(), "image/jpeg")})
    assert page.status_code == 200, page.text
    media = tool(writer, "list_media", search="wpmcp test cover")["items"][0]
    created["media"].append(media["id"])
    assert media["alt"] == "A cover" and media["mime_type"] == "image/jpeg"
    original = httpx.get(media["url"], timeout=20).content
    assert b"Exif" not in original[:4096]

    updated = tool(writer, "update_media", media_id=media["id"], caption="Nova legenda")
    assert updated["caption"] == "Nova legenda"
    emoji = attempt(writer, "update_media", media_id=media["id"], alt_text="Capa 🐻")
    assert emoji["isError"] is True and "emoji" in str(emoji)


# --- the live tools, end to end (phases 5 and 6). Items created here are deleted afterwards.


def test_publish_schedule_unpublish_and_the_digest(writer, created):
    draft = tool(writer, "create_draft", type="post", lang="en", title="wpmcp live test")
    created["post"].append(draft["id"])
    stale = attempt(writer, "publish_content", content_id=draft["id"], expected_version="0" * 16)
    assert stale["isError"] is True and "changed since" in str(stale)

    published = tool(
        writer, "publish_content", content_id=draft["id"], expected_version=draft["version"]
    )
    assert published["status"] == "publish"
    assert httpx.get(published["link"], follow_redirects=True, timeout=20).status_code == 200
    again = attempt(
        writer, "publish_content", content_id=draft["id"], expected_version=published["version"]
    )
    assert again["isError"] is True and "only drafts and pending" in str(again)

    back = tool(
        writer, "unpublish_content", content_id=draft["id"], expected_version=published["version"]
    )
    assert back["status"] == "draft"
    assert httpx.get(f"{WP_URL}/?p={draft['id']}", timeout=20).status_code == 404

    when = (datetime.now(UTC) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    scheduled = tool(
        writer,
        "schedule_content",
        content_id=draft["id"],
        expected_version=back["version"],
        publish_at=when,
    )
    assert scheduled["status"] == "future" and scheduled["date_gmt"] == when

    # Each live change is waiting for the owner's hourly e-mail (action and ID only).
    log = subprocess.run(  # noqa: S603 - fixed command
        [str(ROOT / "bin" / "dev"), "wp", "option", "get", "wpmcp_live_log", "--format=json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    actions = [e["action"] for e in json.loads(log) if e["id"] == draft["id"]]
    assert actions[-3:] == ["published", "unpublished", "scheduled"]
    assert "wpmcp live test" not in log


def test_live_edit_keeps_a_revision_and_restores_it(writer, created):
    draft = tool(
        writer,
        "create_draft",
        type="post",
        lang="en",
        title="wpmcp live edit",
        content="<p>One</p>",
    )
    created["post"].append(draft["id"])
    live = tool(
        writer, "publish_content", content_id=draft["id"], expected_version=draft["version"]
    )
    refused = attempt(
        writer, "update_draft", content_id=draft["id"], expected_version=live["version"], title="x"
    )
    assert refused["isError"] is True

    edited = tool(
        writer,
        "update_published_content",
        content_id=draft["id"],
        expected_version=live["version"],
        content="<p>Two</p>",
        seo={"title": "Edited SEO"},
    )
    assert edited["seo"]["title"] == "Edited SEO"
    assert edited["before"]["revision_id"] and edited["before"]["seo"]["title"] is None
    assert tool(writer, "get_content", content_id=draft["id"])["body"] == "<p>Two</p>"

    restored = tool(
        writer,
        "restore_revision",
        content_id=draft["id"],
        expected_version=edited["version"],
        revision_id=edited["before"]["revision_id"],
    )
    assert tool(writer, "get_content", content_id=draft["id"])["body"] == "<p>One</p>"
    other = attempt(
        writer,
        "restore_revision",
        content_id=draft["id"],
        expected_version=restored["version"],
        revision_id=1,
    )
    assert other["isError"] is True and "is not one of item" in str(other)


def test_trash_and_restore(writer, created):
    draft = tool(writer, "create_draft", type="page", lang="en", title="wpmcp trash test")
    created["post"].append(draft["id"])
    live = tool(
        writer, "publish_content", content_id=draft["id"], expected_version=draft["version"]
    )
    trashed = tool(
        writer, "trash_content", content_id=draft["id"], expected_version=live["version"]
    )
    assert trashed["status"] == "trash"
    assert httpx.get(live["link"], timeout=20).status_code == 404
    back = tool(writer, "restore_from_trash", content_id=draft["id"])
    assert back["status"] == "draft"


def test_protected_front_page(writer, created):
    page = tool(writer, "create_draft", type="page", lang="en", title="wpmcp front page")
    created["post"].append(page["id"])
    live = tool(writer, "publish_content", content_id=page["id"], expected_version=page["version"])
    dev_wp("option", "update", "page_on_front", str(page["id"]))
    try:
        for name in ("trash_content", "unpublish_content"):
            refused = attempt(writer, name, content_id=page["id"], expected_version=live["version"])
            assert refused["isError"] is True and "is protected" in str(refused), name
    finally:
        dev_wp("option", "update", "page_on_front", "0")


def test_link_translations_merges_and_refuses_conflicts(writer, created):
    english = tool(writer, "create_draft", type="post", lang="en", title="wpmcp link en")
    spanish = tool(writer, "create_draft", type="post", lang="es", title="wpmcp link es")
    other = tool(writer, "create_draft", type="post", lang="es", title="wpmcp link es 2")
    created["post"] += [english["id"], spanish["id"], other["id"]]
    linked = tool(
        writer, "link_translations", translations={"en": english["id"], "es": spanish["id"]}
    )
    assert linked["translations"] == {"en": english["id"], "es": spanish["id"]}
    conflict = attempt(
        writer, "link_translations", translations={"en": english["id"], "es": other["id"]}
    )
    assert conflict["isError"] is True and "would be the es translation" in str(conflict)
    wrong = attempt(
        writer, "link_translations", translations={"pt": english["id"], "es": other["id"]}
    )
    assert wrong["isError"] is True and "is in en, not pt" in str(wrong)


def test_menus_apply_and_restore(writer):
    menus = tool(writer, "get_menus")["menus"]
    spanish = next(m for m in menus if m["lang"] == "es")
    kinds = [item["kind"] for item in spanish["items"]]
    assert "language_switcher" in kinds
    page_es = find_lang(writer, "es", "Principito")

    applied = tool(
        writer,
        "apply_menu",
        menu_id=spanish["menu_id"],
        expected_version=spanish["version"],
        items=[
            {"kind": "post", "id": page_es["id"], "title": "Reseña"},
            {"kind": "link", "url": "https://example.org/libros", "title": "Libros", "parent": 0},
        ],
    )
    assert [i["kind"] for i in applied["items"]] == ["post", "link", "language_switcher"]
    assert applied["items"][1]["parent"] == applied["items"][0]["id"]
    assert any("language switcher was kept" in n for n in applied["notes"])

    stale = attempt(
        writer,
        "apply_menu",
        menu_id=spanish["menu_id"],
        expected_version=spanish["version"],
        items=[],
    )
    assert stale["isError"] is True and "changed since" in str(stale)
    english_post = find(writer, "Little Prince")
    wrong_lang = attempt(
        writer,
        "apply_menu",
        menu_id=spanish["menu_id"],
        expected_version=applied["version"],
        items=[{"kind": "post", "id": english_post["id"]}],
    )
    assert wrong_lang["isError"] is True and "the menu is in es" in str(wrong_lang)
    script = attempt(
        writer,
        "apply_menu",
        menu_id=spanish["menu_id"],
        expected_version=applied["version"],
        items=[{"kind": "link", "url": "javascript:alert(1)", "title": "x"}],
    )
    assert script["isError"] is True and "https://" in str(script)
    unknown = attempt(writer, "apply_menu", menu_id=999999, expected_version="0" * 16, items=[])
    assert unknown["isError"] is True and "not shown by a theme location" in str(unknown)

    restored = tool(writer, "restore_menu", menu_id=spanish["menu_id"])
    assert [i["kind"] for i in restored["items"]] == kinds
    assert restored["version"] == spanish["version"]


def find_lang(mcp, lang, title):
    return tool(mcp, "search_content", lang=lang, query=title)["items"][0]


# --- long bodies and images in a body (0.6.0)


def upload_image(writer, created, title: str, alt: str = "") -> int:
    from io import BytesIO

    from PIL import Image

    data = BytesIO()
    Image.new("RGB", (80, 60), "orange").save(data, "PNG")
    client, _ = writer
    link = tool(writer, "request_image_upload", title=title, **({"alt_text": alt} if alt else {}))
    page = client.post(
        link["upload_url"].removeprefix(BASE),
        files={"file": ("x.png", data.getvalue(), "image/png")},
    )
    assert page.status_code == 200, page.text
    media_id = tool(writer, "list_media", search=title)["items"][0]["id"]
    created["media"].append(media_id)
    return media_id


@pytest.fixture(scope="module")
def image_id(writer, created):
    return upload_image(writer, created, "wpmcp inline image", alt="A chip on a board")


def test_get_content_answers_the_version_first_and_long_bodies_in_parts(writer, created):
    body = "".join(
        f"<!-- wp:paragraph -->\n<p>Paragraph {n} about 2 nm chips 🐻</p>\n"
        "<!-- /wp:paragraph -->\n\n"
        for n in range(600)
    )
    draft = tool(writer, "create_draft", type="page", lang="en", title="wpmcp long", content=body)
    created["post"].append(draft["id"])
    assert list(draft)[:4] == ["id", "version", "being_edited", "content_length"]
    assert draft["content_length"] == len(body)

    client, token = writer
    result = rpc_result(
        rpc(client, token, "tools/call", {"name": "get_content",
                                          "arguments": {"content_id": draft["id"]}})
    )  # fmt: skip
    text = result["content"][0]["text"]
    assert text.index('"version"') < text.index('"seo"') < text.index('"body"')
    parts, offset = [], 0
    while offset is not None:
        part = tool(writer, "get_content", content_id=draft["id"], offset=offset)
        assert part["version"] == draft["version"]
        parts.append(part["body"])
        offset = part["next_offset"]
    assert len(parts) > 1 and "".join(parts) == body


def test_a_much_shorter_body_needs_allow_shrink(writer, created):
    body = (
        "<!-- wp:paragraph -->\n<p>"
        + "Long enough paragraph. " * 100
        + "</p>\n<!-- /wp:paragraph -->"
    )
    draft = tool(writer, "create_draft", type="post", lang="en", title="wpmcp shrink", content=body)
    created["post"].append(draft["id"])
    half = body[: len(body) // 3]
    refused = attempt(
        writer, "update_draft", content_id=draft["id"], expected_version=draft["version"],
        content=half,
    )  # fmt: skip
    assert refused["isError"] is True and "less than half" in str(refused)
    # A small cut goes through, and so does a big one the caller says is intended.
    trimmed = tool(
        writer, "update_draft", content_id=draft["id"], expected_version=draft["version"],
        content=body.replace("Long enough paragraph. ", "Long paragraph. ", 10),
    )  # fmt: skip
    live = tool(
        writer, "publish_content", content_id=draft["id"], expected_version=trimmed["version"]
    )
    refused = attempt(
        writer, "update_published_content", content_id=draft["id"],
        expected_version=live["version"], content="<p>x</p>",
    )  # fmt: skip
    assert refused["isError"] is True and "less than half" in str(refused)
    cut = tool(
        writer, "update_published_content", content_id=draft["id"],
        expected_version=live["version"], content=half, allow_shrink=True,
    )  # fmt: skip
    # WordPress closes the paragraph the cut left open.
    assert len(half) <= cut["content_length"] < len(body) // 2
    assert cut["before"]["revision_id"]


ARTICLE = """<!-- wp:heading -->
<h2 class="wp-block-heading">Chipmaker unveils 2 nm laptop processor</h2>
<!-- /wp:heading -->

<!-- wp:paragraph {"className":"lead"} -->
<p class="lead">The new chip promises <strong>longer battery life</strong> 🔋</p>
<!-- /wp:paragraph -->

<!-- wp:quote -->
<blockquote class="wp-block-quote"><!-- wp:paragraph -->
<p>“It is the biggest jump in a decade.”</p>
<!-- /wp:paragraph --></blockquote>
<!-- /wp:quote -->

<!-- wp:paragraph -->
<p>Laptops with it ship in spring; the chip also appears in the quote above.</p>
<!-- /wp:paragraph -->"""


def test_insert_image_into_a_draft_keeps_the_rest_of_the_body(writer, created, image_id):
    draft = tool(
        writer, "create_draft", type="post", lang="en", title="wpmcp insert", content=ARTICLE
    )
    created["post"].append(draft["id"])

    after = tool(
        writer, "insert_image", content_id=draft["id"], expected_version=draft["version"],
        media_id=image_id, position="after_text", after_text="LONGER  battery life",
        alt_text="Placa con el chip", caption="The <em>new</em> chip<script>x</script>",
    )  # fmt: skip
    assert "before" not in after and "content" not in after
    body = tool(writer, "get_content", content_id=draft["id"])["body"]
    head, image, tail = body.partition(f'<!-- wp:image {{"id":{image_id},"sizeSlug":"large"')
    assert image, body
    # Everything around the image is the original, byte for byte.
    assert head == ARTICLE[: ARTICLE.index("<!-- wp:quote -->")]
    assert (
        tail.split("<!-- /wp:image -->", 1)[1]
        == "\n\n" + ARTICLE[ARTICLE.index("<!-- wp:quote -->") :]
    )
    assert 'alt="Placa con el chip"' in tail and f'class="wp-image-{image_id}"' in tail
    assert "<em>new</em> chip</figcaption>" in tail and "<script" not in tail

    # The image's own alt text by default; start and end.
    start = tool(writer, "insert_image", content_id=draft["id"], expected_version=after["version"],
                 media_id=image_id, position="start")  # fmt: skip
    end = tool(writer, "insert_image", content_id=draft["id"], expected_version=start["version"],
               media_id=image_id, size="medium")  # fmt: skip
    body = tool(writer, "get_content", content_id=draft["id"])["body"]
    assert body.startswith("<!-- wp:image") and body.rstrip().endswith("<!-- /wp:image -->")
    assert body.count('alt="A chip on a board"') == 2 and "size-medium" in body
    assert body.count("<!-- wp:image") == 3

    for after_text, message in (
        ("chip", "blocks contain that text: give longer after_text"),
        ("not in this article", "No block contains that text"),
    ):
        refused = attempt(
            writer, "insert_image", content_id=draft["id"], expected_version=end["version"],
            media_id=image_id, position="after_text", after_text=after_text,
        )  # fmt: skip
        assert refused["isError"] is True and message in str(refused), after_text
    stale = attempt(writer, "insert_image", content_id=draft["id"],
                    expected_version=draft["version"], media_id=image_id)  # fmt: skip
    assert stale["isError"] is True and "changed since" in str(stale)
    not_image = attempt(writer, "insert_image", content_id=draft["id"],
                        expected_version=end["version"], media_id=draft["id"])  # fmt: skip
    assert not_image["isError"] is True and "No image with ID" in str(not_image)


def test_insert_image_into_classic_content_and_without_alt(writer, created):
    bare = upload_image(writer, created, "wpmcp bare image")
    page = tool(writer, "create_draft", type="page", lang="es", title="wpmcp clásica",
                content="<p>Hola</p>\n<p>Adiós</p>")  # fmt: skip
    created["post"].append(page["id"])
    refused = attempt(writer, "insert_image", content_id=page["id"],
                      expected_version=page["version"], media_id=bare, position="after_text",
                      after_text="Hola")  # fmt: skip
    assert refused["isError"] is True and "classic" in str(refused)
    done = tool(writer, "insert_image", content_id=page["id"], expected_version=page["version"],
                media_id=bare)  # fmt: skip
    assert any("no alt text" in note for note in done["notes"])
    body = tool(writer, "get_content", content_id=page["id"])["body"]
    assert body.startswith("<p>Hola</p>\n<p>Adiós</p>\n\n<!-- wp:image")


def test_insert_image_into_a_published_item_is_a_live_edit(writer, created, image_id):
    draft = tool(writer, "create_draft", type="page", lang="pt", title="wpmcp publicada",
                 content=ARTICLE)  # fmt: skip
    created["post"].append(draft["id"])
    live = tool(
        writer, "publish_content", content_id=draft["id"], expected_version=draft["version"]
    )
    inserted = tool(writer, "insert_image", content_id=draft["id"],
                    expected_version=live["version"], media_id=image_id,
                    alt_text="Um chip")  # fmt: skip
    assert inserted["status"] == "publish" and inserted["before"]["revision_id"]
    page = httpx.get(live["link"], follow_redirects=True, timeout=20).text
    assert 'alt="Um chip"' in page

    log = subprocess.run(  # noqa: S603 - fixed command
        [str(ROOT / "bin" / "dev"), "wp", "option", "get", "wpmcp_live_log", "--format=json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert [e["action"] for e in json.loads(log) if e["id"] == draft["id"]][-1] == "inserted image"

    tool(writer, "restore_revision", content_id=draft["id"], expected_version=inserted["version"],
         revision_id=inserted["before"]["revision_id"])  # fmt: skip
    assert tool(writer, "get_content", content_id=draft["id"])["body"] == ARTICLE

    dev_wp("option", "update", "page_on_front", str(draft["id"]))
    try:
        current = tool(writer, "get_content", content_id=draft["id"])
        refused = attempt(writer, "insert_image", content_id=draft["id"],
                          expected_version=current["version"], media_id=image_id)  # fmt: skip
        assert refused["isError"] is True and "is protected" in str(refused)
    finally:
        dev_wp("option", "update", "page_on_front", "0")


# --- the bridge's operations as abilities (0.7.0)


def dev_eval(code: str) -> str:
    """PHP in the dev stack (WP-CLI eval as the site admin); returns its output."""
    command = [str(ROOT / "bin" / "dev"), "wp", "eval", code]
    done = subprocess.run(command, check=True, capture_output=True, text=True)  # noqa: S603
    return done.stdout


AS_INTEGRATION_USER = 'wp_set_current_user( get_user_by( "login", "wpmcp" )->ID );'


def test_every_operation_is_an_ability_with_the_tools_annotations(writer):
    abilities = json.loads(
        dev_eval(
            "echo wp_json_encode( array_map( fn ( $a ) => $a->get_meta(),"
            ' wp_get_abilities( array( "namespace" => "wpmcp" ) ) ) );'
        )
    )
    client, token = writer
    tools = {t["name"]: t for t in rpc_result(rpc(client, token, "tools/list"))["tools"]}
    # Public pages are read by the service itself; the bridge only gives their addresses.
    python_only = {
        "get_connection_info",
        "import_image",
        "request_image_upload",
        "inspect_page",
        "check_sitemap",
    }
    site = {name for name, t in tools.items() if t["description"].startswith("Site ability")}
    names = {name.removeprefix("wpmcp/").replace("-", "_") for name in abilities}
    assert names == set(tools) - python_only - site
    for name, meta in abilities.items():
        hints = tools[name.removeprefix("wpmcp/").replace("-", "_")]["annotations"]
        expected = (hints["readOnlyHint"], hints["destructiveHint"], hints["idempotentHint"])
        found = meta["annotations"]
        assert (found["readonly"], found["destructive"], found["idempotent"]) == expected, name
        assert meta["show_in_rest"] is False and meta["public"] is False, name


def test_routes_run_through_the_abilities(writer, created):
    draft = tool(writer, "create_draft", type="post", lang="en", title="wpmcp ability test")
    created["post"].append(draft["id"])
    item = draft["id"]
    php = f"""{AS_INTEGRATION_USER}
    add_action( "wp_ability_invoked", function ( $name ) {{ echo "invoked $name\n"; }} );
    $r = rest_do_request( new WP_REST_Request( "GET", "/wpmcp/v1/content/{item}" ) );
    $v = $r->get_data()["version"]; echo "rest $v\n";
    $a = wp_get_ability( "wpmcp/get-content" );
    $v = $a->execute( array( "id" => {item} ) )["version"]; echo "direct $v\n";
    foreach ( array( array( "id" => 0 ), array( "id" => {item}, "x" => 1 ) ) as $bad ) {{
        $e = $a->execute( $bad )->get_error_code(); echo "bad $e\n";
    }}
    wp_set_current_user( 0 );
    $e = $a->execute( array( "id" => {item} ) )->get_error_code(); echo "anon $e\n";
    """
    lines = dev_eval(php).splitlines()
    assert "invoked wpmcp/get-content" in lines
    assert f"rest {draft['version']}" in lines and f"direct {draft['version']}" in lines
    assert lines.count("bad ability_invalid_input") == 2
    assert "anon ability_invalid_permissions" in lines


# --- site abilities listed by the owner (WPMCP_BRIDGE_ABILITIES, set by bin/dev provision)


def test_listed_site_abilities_are_tools_confined_to_the_integration_user(writer):
    client, token = writer
    tools = {t["name"]: t for t in rpc_result(rpc(client, token, "tools/list"))["tools"]}
    assert {"core_get_user_info", "core_get_site_info"} <= set(tools)
    assert not {name for name in tools if name.startswith("example_")}  # not on this site
    assert "core_get_environment_info" not in tools  # on the site, not on the list

    me = tool(writer, "core_get_user_info")
    assert me["ability"] == "core/get-user-info" and me["result"]["user_login"] == "wpmcp"
    # Listed, but it needs manage_options, which the integration user never has.
    refused = attempt(writer, "core_get_site_info")
    assert refused["isError"] is True and "permission" in str(refused)

    off_list = dev_eval(
        AS_INTEGRATION_USER
        + '$q = new WP_REST_Request( "POST", "/wpmcp/v1/abilities/run" );'
        + '$q->set_body_params( array( "name" => "core/get-environment-info" ) );'
        + "echo rest_do_request( $q )->get_status();"
    )
    assert off_list.strip() == "404"


# --- bulk changes, public pages and menu widgets (0.8.0)


def published(writer, lang, title):
    """A published fixture item (other tests leave drafts with the same words)."""
    found = tool(writer, "search_content", lang=lang, query=title, status="publish")
    return found["items"][0]


def draft(writer, created, title, content, lang="en"):
    item = tool(writer, "create_draft", type="post", lang=lang, title=title, content=content)
    created["post"].append(item["id"])
    return item


def entry(item, version=None):
    return {"content_id": item["id"], "expected_version": version or item["version"]}


def body_of(writer, item_id):
    return tool(writer, "get_content", content_id=item_id, max_chars=60_000)["body"]


def test_search_results_carry_the_version(writer, created):
    item = draft(writer, created, "wpmcp versioned search", "<p>x</p>")
    found = tool(writer, "search_content", lang="en", status="draft", query="versioned search")
    assert found["items"][0]["version"] == item["version"]


def test_bulk_add_links_adds_one_link_where_missing_and_undoes(writer, created):
    target = published(writer, "en", "Little Prince")
    link = target["link"]
    html = (
        f'<section class="x-collection"><h2>Series</h2><p>See <a href="{link}">all</a>.</p>'
        "</section>"
    )
    wrapped = draft(writer, created, "wpmcp bulk wrapped", "<article><div><p>A</p></div></article>")
    blocks = draft(
        writer,
        created,
        "wpmcp bulk blocks",
        "<!-- wp:paragraph -->\n<p>B</p>\n<!-- /wp:paragraph -->",
    )
    linked = draft(writer, created, "wpmcp bulk linked", f'<p>Already: <a href="{link}">x</a></p>')
    spanish = draft(writer, created, "wpmcp bulk es", "<p>es</p>", lang="es")
    stale = draft(writer, created, "wpmcp bulk stale", "<p>stale</p>")
    items = [entry(wrapped), entry(blocks), entry(linked), entry(spanish), entry(stale, "0" * 16)]
    args = {"target_id": target["id"], "html": html, "position": "before_closing_tags"}

    dry = tool(writer, "bulk_add_links", items=items, dry_run=True, **args)
    assert dry["batch_id"] is None
    assert dry["would_change"] == [wrapped["id"], blocks["id"]]
    assert dry["unchanged"] == [linked["id"]]
    assert {r["id"]: r["code"] for r in dry["refused"]} == {
        spanish["id"]: "wpmcp_lang_mismatch",
        stale["id"]: "wpmcp_stale",
    }
    assert body_of(writer, wrapped["id"]) == "<article><div><p>A</p></div></article>"

    run = tool(writer, "bulk_add_links", items=items, **args)
    assert run["counts"] == {"changed": 2, "unchanged": 1, "refused": 2}
    assert body_of(writer, wrapped["id"]) == f"<article><div><p>A</p>{html}</div></article>"
    assert body_of(writer, blocks["id"]).endswith(f"<!-- wp:html -->{html}<!-- /wp:html -->")
    versions = {c["id"]: c["version"] for c in run["changed"]}
    assert (
        versions[wrapped["id"]] == tool(writer, "get_content", content_id=wrapped["id"])["version"]
    )

    again = tool(writer, "bulk_add_links", items=items, **args)
    assert again["batch_id"] is None and again["counts"]["changed"] == 0
    assert sorted(again["unchanged"]) == sorted([wrapped["id"], blocks["id"], linked["id"]])

    undone = tool(writer, "undo_bulk_change", batch_id=run["batch_id"])
    assert sorted(undone["undone"]) == sorted(versions) and undone["skipped"] == []
    assert body_of(writer, wrapped["id"]) == "<article><div><p>A</p></div></article>"
    assert "<a " not in body_of(writer, blocks["id"])
    repeat = attempt(writer, "undo_bulk_change", batch_id=run["batch_id"])
    assert repeat["isError"] is True and "already undone" in str(repeat)
    unknown = attempt(writer, "undo_bulk_change", batch_id="0" * 12)
    assert unknown["isError"] is True and "no batch" in str(unknown)


def test_bulk_add_links_refuses_markup_kses_would_strip_and_bad_html(writer, created):
    target = published(writer, "en", "Little Prince")
    # An administrator's post with an iframe: the integration user saving it would lose it.
    admin_item = int(
        dev_eval(
            "kses_remove_filters(); echo wp_insert_post( array( 'post_title' => 'wpmcp iframe',"
            " 'post_content' => '<p>x</p><iframe src=\"https://example.org/\"></iframe>',"
            " 'post_status' => 'draft' ) );"
        )
    )
    created["post"].append(admin_item)
    dev_eval(f"pll_set_post_language( {admin_item}, 'en' );")
    item = tool(writer, "get_content", content_id=admin_item)
    html = f'<p><a href="{target["link"]}">guide</a></p>'
    result = tool(writer, "bulk_add_links", target_id=target["id"], html=html, items=[entry(item)])
    assert result["refused"][0]["code"] == "wpmcp_markup_lost"
    assert "<iframe>" in result["refused"][0]["reason"]
    assert "iframe" in body_of(writer, admin_item)

    no_link = attempt(
        writer, "bulk_add_links", target_id=target["id"], html="<p>no link</p>", items=[entry(item)]
    )
    assert no_link["isError"] is True and target["link"] in str(no_link)
    unpublished = attempt(
        writer, "bulk_add_links", target_id=admin_item, html=html, items=[entry(item)]
    )
    assert unpublished["isError"] is True and "published post or page" in str(unpublished)


def test_bulk_links_on_published_items_are_live_and_in_the_digest(writer, created):
    target = published(writer, "en", "Little Prince")
    item = draft(writer, created, "wpmcp bulk live", "<p>live</p>")
    live = tool(writer, "publish_content", content_id=item["id"], expected_version=item["version"])
    html = f'<p><a href="{target["link"]}">guide</a></p>'
    run = tool(writer, "bulk_add_links", target_id=target["id"], html=html, items=[entry(live)])
    assert run["changed"][0]["id"] == item["id"]
    log = json.loads(dev_eval("echo wp_json_encode( get_option( 'wpmcp_live_log' ) );"))
    batch = [e for e in log if run["batch_id"] in e["action"]]
    assert batch and batch[-1]["ids"] == [item["id"]] and batch[-1]["id"] == 0
    revisions = tool(writer, "list_revisions", content_id=item["id"])["items"]
    assert revisions, "a revision is kept before a live change"
    tool(writer, "undo_bulk_change", batch_id=run["batch_id"])
    assert "<a " not in body_of(writer, item["id"])


def test_bulk_update_seo_writes_only_what_differs(writer, created):
    first = draft(writer, created, "wpmcp seo one", "<p>1</p>")
    second = draft(writer, created, "wpmcp seo two", "<p>2</p>")
    seo = {"title": "Bulk SEO title", "description": "Bulk description"}
    run = tool(
        writer,
        "bulk_update_seo",
        items=[{**entry(first), "seo": seo}, {**entry(second), "seo": {"title": "Only title"}}],
    )
    assert run["counts"]["changed"] == 2
    item = tool(writer, "get_content", content_id=first["id"])
    assert (
        item["seo"]["title"] == "Bulk SEO title"
        and item["seo"]["description"] == seo["description"]
    )
    assert item["version"] == {c["id"]: c["version"] for c in run["changed"]}[first["id"]]

    # The same values again, with an old version: unchanged, not refused.
    again = tool(writer, "bulk_update_seo", items=[{**entry(first), "seo": seo}])
    assert again["unchanged"] == [first["id"]] and again["batch_id"] is None
    # One field the same, one different: only the different one is written.
    newer = tool(
        writer,
        "bulk_update_seo",
        items=[{**entry(item), "seo": {"title": "Bulk SEO title", "description": None}}],
    )
    assert newer["counts"]["changed"] == 1
    cleared = tool(writer, "get_content", content_id=first["id"])["seo"]
    assert cleared["title"] == "Bulk SEO title" and cleared["description"] is None

    tool(writer, "undo_bulk_change", batch_id=newer["batch_id"])
    assert (
        tool(writer, "get_content", content_id=first["id"])["seo"]["description"]
        == seo["description"]
    )
    tool(writer, "undo_bulk_change", batch_id=run["batch_id"])
    restored = tool(writer, "get_content", content_id=first["id"])["seo"]
    assert restored["title"] is None and restored["description"] is None


def test_menus_shown_by_widgets_and_adding_items(writer):
    menus = tool(writer, "get_menus")["menus"]
    footers = {m["lang"]: m for m in menus if m.get("widget_area") == "wpmcp-dev-footer"}
    assert set(footers) == {"en", "es"}
    assert footers["en"]["widget_title"] == "Links" and footers["en"]["location"] is None
    assert footers["en"]["widget_area_name"] == "Dev footer"
    assert "wpmcp dev footer hidden" not in {m["name"] for m in menus}  # inactive widget

    footer = footers["en"]
    before = [(i["kind"], i["title"]) for i in footer["items"]]
    post_en = published(writer, "en", "Little Prince")
    added = tool(
        writer,
        "add_menu_items",
        menu_id=footer["menu_id"],
        expected_version=footer["version"],
        items=[
            {"kind": "post", "id": post_en["id"], "title": "Guide"},
            {"kind": "link", "url": "https://example.org/contact", "title": "Same link"},
        ],
        position=0,
    )
    assert [(i["kind"], i["title"]) for i in added["items"]] == [("post", "Guide"), *before]
    assert "1 item(s) already in the menu were skipped." in added["notes"]
    repeat = tool(
        writer,
        "add_menu_items",
        menu_id=footer["menu_id"],
        expected_version=added["version"],
        items=[{"kind": "post", "id": post_en["id"]}],
    )
    assert repeat["version"] == added["version"] and "nothing changed" in repeat["notes"][0]
    post_es = published(writer, "es", "Principito")
    wrong = attempt(
        writer,
        "add_menu_items",
        menu_id=footer["menu_id"],
        expected_version=added["version"],
        items=[{"kind": "post", "id": post_es["id"]}],
    )
    assert wrong["isError"] is True and "the menu is in en" in str(wrong)
    restored = tool(writer, "restore_menu", menu_id=footer["menu_id"])
    assert restored["version"] == footer["version"]

    hidden = int(dev_eval("echo wp_get_nav_menu_object( 'wpmcp dev footer hidden' )->term_id;"))
    refused = attempt(writer, "add_menu_items", menu_id=hidden, expected_version="0" * 16,
                      items=[{"kind": "post", "id": post_en["id"]}])  # fmt: skip
    assert refused["isError"] is True and "or a menu widget" in str(refused)


def test_inspect_page_and_check_sitemap_read_the_public_site(writer):
    post = published(writer, "en", "Little Prince")
    page = tool(writer, "inspect_page", content_id=post["id"])
    assert page["status"] == 200 and page["url"] == post["link"]
    assert page["canonical"] == post["link"]
    assert set(page["hreflang"]) >= {"en", "es", "pt"}
    assert "BreadcrumbList" in page["json_ld_types"]
    assert not [c for c in page["checks"] if "canonical" in c or "hreflang" in c]

    ids = list(post["translations"].values())
    found = tool(writer, "check_sitemap", content_ids=ids)
    assert found["sitemaps"] and found["checked"] == len(ids)
    assert found["listed"] == len(ids) and found["missing"] == []
