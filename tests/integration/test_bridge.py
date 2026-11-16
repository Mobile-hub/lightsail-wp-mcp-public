"""Against the local WordPress stack (``bin/dev up``): the bridge plugin's access rules and
the read tools end to end. Skipped when the stack is not running.

WPMCP_IT_WP_URL and WPMCP_IT_WP_PASSWORD_FILE override the defaults below.
"""

from __future__ import annotations

import json
import os
import subprocess
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
    assert "🐻" in content["content"]
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
    settings = make_settings(wp_url=WP_URL, wp_password=password)
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
    assert stored["content"] == BLOCKS
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
    stored = tool(writer, "get_content", content_id=draft["id"])["content"]
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
