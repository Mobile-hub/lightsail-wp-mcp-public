"""inspect_page and check_sitemap against a fake site: the bridge's /links answer and the
public pages, sitemaps and robots.txt a visitor gets."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from starlette.testclient import TestClient

from tests.conftest import BASE, access_token, make_settings
from tests.unit.test_write_tools import call, payload
from wpmcp.app import create_app
from wpmcp.tools.public import summarise
from wpmcp.wp.client import WordPressClient
from wpmcp.wp.public import PublicPage

SITE = "https://books.example.test"
LINKS = {
    8: {"id": 8, "type": "post", "lang": "en", "link": f"{SITE}/dog-man/", "noindex": False,
        "password": False, "translations": {"en": 8, "es": 9, "pt": 10}},
    9: {"id": 9, "type": "post", "lang": "es", "link": f"{SITE}/es/polican/", "noindex": False,
        "password": False, "translations": {"en": 8, "es": 9, "pt": 10}},
    11: {"id": 11, "type": "page", "lang": "en", "link": f"{SITE}/hidden/", "noindex": True,
         "password": False, "translations": {}},
    12: {"id": 12, "type": "page", "lang": "en", "link": f"{SITE}/moved/", "noindex": False,
         "password": False, "translations": {}},
}  # fmt: skip
PAGE = f"""<!doctype html><html lang="en-GB"><head>
<title>Dog Man: review</title>
<meta name="description" content="A review.">
<meta name="robots" content="max-image-preview:large">
<link rel="canonical" href="{SITE}/dog-man/">
<link rel="alternate" hreflang="en" href="{SITE}/dog-man/">
<link rel="alternate" hreflang="es" href="{SITE}/es/otro/">
<meta property="og:title" content="Dog Man">
<script type="application/ld+json">{{"@context":"https://schema.org","@graph":[
 {{"@type":"WebPage"}},{{"@type":["BlogPosting","Review"]}},{{"@type":"BreadcrumbList"}}]}}</script>
<script type="application/ld+json">{{not json</script>
</head><body><h1>Dog Man</h1><h1>Again</h1><script>var x = "<h1>no</h1>";</script></body></html>"""
INDEX = f"""<?xml version="1.0"?><sitemapindex>
<sitemap><loc><![CDATA[{SITE}/post-sitemap.xml]]></loc></sitemap>
<sitemap><loc>{SITE}/page-sitemap.xml</loc></sitemap>
<sitemap><loc>https://elsewhere.example/evil.xml</loc></sitemap>
<sitemap><loc>{SITE}/gone-sitemap.xml</loc></sitemap></sitemapindex>"""
POSTS = f"<urlset><url><loc>{SITE}/dog-man</loc></url><url><loc>{SITE}/x/</loc></url></urlset>"
PAGES = f"<urlset><url><loc>{SITE}/moved/</loc></url></urlset>"


class FakeSite:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.robots = f"User-agent: *\nDisallow:\nSitemap: {SITE}/sitemap.xml\n"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/wp-json/wpmcp/v1/abilities":
            return httpx.Response(200, json={"items": []})
        if path == "/wp-json/wpmcp/v1/links":
            ids = request.url.params.get("ids")
            wanted = [int(i) for i in ids.split(",")] if ids else list(LINKS)
            return httpx.Response(200, json={"items": [LINKS[i] for i in wanted if i in LINKS]})
        html = {"content-type": "text/html; charset=UTF-8"}
        xml = {"content-type": "text/xml"}
        pages = {
            "/dog-man/": httpx.Response(200, text=PAGE, headers=html),
            "/moved/": httpx.Response(301, headers={"location": f"{SITE}/new/"}),
            "/robots.txt": httpx.Response(200, text=self.robots),
            "/sitemap.xml": httpx.Response(200, text=INDEX, headers=xml),
            "/post-sitemap.xml": httpx.Response(200, text=POSTS, headers=xml),
            "/page-sitemap.xml": httpx.Response(200, text=PAGES, headers=xml),
        }
        return pages.get(path, httpx.Response(404, text="not found"))

    def public_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if not r.url.path.startswith("/wp-json/")]


@pytest.fixture
def site():
    return FakeSite()


@pytest.fixture
def app(store, clock, site):
    settings = make_settings(wp_url=SITE, wp_password="pw")
    wp = WordPressClient(settings, transport=httpx.MockTransport(site))
    return create_app(settings, store=store, clock=clock, wp=wp)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=BASE) as test_client:
        yield test_client


def test_inspect_page_summarises_what_search_engines_see(app, client, clock, site):
    token, _ = access_token(app, clock, scope="wp:read")
    page = payload(call(client, token, "inspect_page", {"content_id": 8}))
    assert page["status"] == 200 and page["lang"] == "en-GB"
    assert page["title"] == "Dog Man: review" and page["description"] == "A review."
    assert page["canonical"] == f"{SITE}/dog-man/"
    assert page["json_ld_types"] == ["WebPage", "BlogPosting", "Review", "BreadcrumbList"]
    assert page["h1"] == ["Dog Man", "Again"]
    assert page["og"] == {"title": "Dog Man"}
    checks = "\n".join(page["checks"])
    assert f"hreflang es points to {SITE}/es/otro/, not {SITE}/es/polican/" in checks
    assert "Translations not published, so not in hreflang: pt" in checks
    assert "2 H1 headings" in checks and "1 JSON-LD block(s) are not valid" in checks
    assert "No og:description" in checks and "canonical" not in checks
    assert "<head>" not in json.dumps(page)  # a summary, not the page
    # Fetched as a visitor: no credentials on the public page.
    public = site.public_requests()
    assert [r.url.path for r in public] == ["/dog-man/"]
    assert "authorization" not in public[0].headers


def test_inspect_page_reports_redirects_and_unpublished_items(app, client, clock):
    token, _ = access_token(app, clock, scope="wp:read")
    moved = payload(call(client, token, "inspect_page", {"content_id": 12}))
    assert moved["status"] == 301 and moved["redirect_to"] == f"{SITE}/new/"
    missing = call(client, token, "inspect_page", {"content_id": 99})
    assert missing["isError"] is True and "not a published post or page" in str(missing)


def test_inspect_page_never_fetches_another_host(app, client, clock, site):
    LINKS[13] = {**LINKS[12], "id": 13, "link": "https://elsewhere.example/x/"}
    try:
        token, _ = access_token(app, clock, scope="wp:read")
        result = call(client, token, "inspect_page", {"content_id": 13})
        assert result["isError"] is True and "not on this site" in str(result)
        assert site.public_requests() == []
    finally:
        del LINKS[13]


def test_check_sitemap_finds_what_it_does_not_list(app, client, clock, site):
    token, _ = access_token(app, clock, scope="wp:read")
    found = payload(call(client, token, "check_sitemap"))
    assert found["robots_txt_sitemaps"] == [f"{SITE}/sitemap.xml"]
    assert found["sitemaps"] == [
        {"url": f"{SITE}/post-sitemap.xml", "addresses": 2},
        {"url": f"{SITE}/page-sitemap.xml", "addresses": 1},
    ]
    assert (found["checked"], found["noindex"], found["listed"]) == (4, 1, 2)
    assert found["missing"] == [{"id": 9, "type": "post", "lang": "es", "link": LINKS[9]["link"]}]
    assert any("gone-sitemap.xml answered HTTP 404" in n for n in found["notes"])
    # The sitemap on another host was not fetched.
    assert "elsewhere.example" not in {r.url.host for r in site.requests}


def test_check_sitemap_for_some_items_and_without_robots(app, client, clock, site):
    site.robots = "User-agent: *\n"
    token, _ = access_token(app, clock, scope="wp:read")
    found = payload(call(client, token, "check_sitemap", {"content_ids": [8, 99]}))
    assert (found["checked"], found["listed"], found["missing_total"]) == (1, 1, 0)
    notes = "\n".join(found["notes"])
    assert "robots.txt names no sitemap" in notes and "Not published" in notes and "99" in notes
    site_paths = [r.url.path for r in site.public_requests()]
    assert site_paths[:2] == ["/robots.txt", "/sitemap.xml"]


BAD = f"""<html><head><meta name="robots" content="noindex"><meta name="robots" content="x">
<link rel="canonical" href="{SITE}/a/"><link rel="canonical" href="{SITE}/b/">
</head><body></body></html>"""


def page(text: str, status: int = 200, content_type: str = "text/html") -> PublicPage:
    return PublicPage(f"{SITE}/dog-man/", status, None, content_type, "noarchive", text)


@pytest.mark.parametrize(
    ("fetched", "expected"),
    [
        (page(BAD), ["No <title>.", "No meta description.", "2 meta robots tags",
                     "noindex: search engines", "2 different canonical links", "0 H1 headings",
                     "No og:title"]),
        (page(f'<html><head><link rel="canonical" href="{SITE}/other/"></head></html>'),
         ["The canonical points elsewhere"]),
        (page("<html></html>", status=404), ["HTTP 404", "No canonical link."]),
        (page("{}", content_type="application/json"), ["Not an HTML page (application/json)"]),
    ],
)  # fmt: skip
def test_summarise_names_each_problem(fetched, expected):
    item = {**LINKS[8], "translations": {}}
    result = summarise(fetched, item, {})
    checks = "\n".join(result["checks"])
    for text in expected:
        assert text in checks, (text, checks)


def test_public_pages_that_cannot_be_read_are_tool_errors(store, clock):
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/wp-json/wpmcp/v1/links":
            return httpx.Response(200, json={"items": [LINKS[8]]})
        if request.url.path.startswith("/wp-json/"):
            return httpx.Response(200, json={"items": []})
        if request.url.path == "/dog-man/":
            raise httpx.ConnectError("refused")
        return httpx.Response(200, text="x" * (11 * 1024 * 1024))

    settings = make_settings(wp_url=SITE, wp_password="pw")
    wp = WordPressClient(settings, transport=httpx.MockTransport(broken))
    app = create_app(settings, store=store, clock=clock, wp=wp)
    with TestClient(app, base_url=BASE) as client:
        token, _ = access_token(app, clock, scope="wp:read")
        down = call(client, token, "inspect_page", {"content_id": 8})
        assert down["isError"] is True and "could not be reached" in str(down)
        big = call(client, token, "check_sitemap")
        assert big["isError"] is True and "larger than" in str(big)
    with pytest.raises(ValueError, match="not an address of this site"):
        asyncio.run(wp.public.get("https://elsewhere.example/"))
