"""What visitors and search engines get (scope wp:read): one page's SEO tags, and which
published items the XML sitemap lists.

The pages are fetched from the site itself without credentials (wp/public.py). The tools
take content IDs, never addresses: the bridge gives each published item's permalink, so
nothing outside the site can be fetched through them. They answer short summaries, not
the HTML or the XML.
"""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Annotated, Any
from urllib.parse import unquote, urlsplit

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from ..wp.client import WordPressClient, WordPressError
from ..wp.public import PublicPage
from . import Registry, ToolSpec

SITEMAP_MAX_BYTES = 10 * 1024 * 1024
MAX_SITEMAPS = 50
MAX_LISTED = 50
_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", re.I | re.S)
_ROBOTS_SITEMAP = re.compile(r"^\s*sitemap\s*:\s*(\S+)", re.I | re.M)


def register(registry: Registry, wp: WordPressClient) -> None:
    site = wp.public

    async def links(ids: list[int] | None = None) -> dict[int, dict[str, Any]]:
        params = {"ids": ",".join(map(str, ids))} if ids else None
        try:
            found = await wp.get("/links", params)
        except WordPressError as exc:
            raise ToolError(str(exc)) from exc
        return {item["id"]: item for item in found.get("items", [])}

    async def fetch(url: str, max_bytes: int | None = None) -> PublicPage:
        try:
            return await site.get(url, **({"max_bytes": max_bytes} if max_bytes else {}))
        except WordPressError as exc:
            raise ToolError(str(exc)) from exc

    async def inspect_page(
        content_id: Annotated[int, Field(ge=1, description="ID of a published post or page.")],
    ) -> dict[str, Any]:
        """What search engines see on a published post or page, fetched from the site as a
        visitor: HTTP status (and redirect), title, meta description, robots, canonical,
        hreflang alternates, Open Graph tags, JSON-LD types (breadcrumbs, article, book...)
        and H1 headings, with `checks` naming problems found (canonical elsewhere, noindex,
        a published translation missing from hreflang, more than one H1...). Use it to
        verify SEO changes; the HTML itself is not returned."""
        item = (await links([content_id])).get(content_id)
        if item is None:
            raise ToolError(
                f"Item {content_id} is not a published post or page: visitors and search "
                "engines cannot see it."
            )
        others = [i for lang, i in item["translations"].items() if i != content_id]
        translations = await links(others) if others else {}
        if not site.on_site(item["link"]):
            raise ToolError(f"The address of item {content_id} is not on this site.")
        return summarise(await fetch(item["link"]), item, translations)

    async def check_sitemap(
        content_ids: Annotated[
            list[Annotated[int, Field(ge=1)]] | None,
            Field(
                min_length=1,
                max_length=100,
                description="Published posts or pages to look for. Default: every one.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Whether the site's XML sitemap (All in One SEO's, or WordPress's own) lists
        published posts and pages: the sitemaps found (from robots.txt or /sitemap.xml)
        with their number of addresses, and the published items it does not list, apart
        from those set to noindex. The XML itself is not returned."""
        published = await links(content_ids)
        robots = await fetch(site.origin + "/robots.txt")
        declared = _ROBOTS_SITEMAP.findall(robots.text) if robots.status == 200 else []
        roots = [u for u in declared if site.on_site(u)] or [
            site.origin + "/sitemap.xml",
            site.origin + "/wp-sitemap.xml",
        ]
        sitemaps, listed, notes = await read_sitemaps(roots, fetch, site.on_site)
        if not declared:
            notes.append("robots.txt names no sitemap (a Sitemap: line helps search engines).")
        indexable = [i for i in published.values() if not i["noindex"]]
        missing = [i for i in indexable if _key(i["link"]) not in listed]
        result: dict[str, Any] = {
            "robots_txt_sitemaps": declared,
            "sitemaps": sitemaps,
            "addresses": len(listed),
            "checked": len(published),
            "noindex": len(published) - len(indexable),
            "listed": len(indexable) - len(missing),
            "missing_total": len(missing),
            "missing": [
                {"id": i["id"], "type": i["type"], "lang": i["lang"], "link": i["link"]}
                for i in missing[:MAX_LISTED]
            ],
        }
        if content_ids:
            not_published = sorted(set(content_ids) - set(published))
            if not_published:
                notes.append(f"Not published, so not in any sitemap: {not_published}.")
        if notes:
            result["notes"] = notes
        return result

    registry.add(ToolSpec(inspect_page, title="Inspect a public page"))
    registry.add(ToolSpec(check_sitemap, title="Check the XML sitemap"))


async def read_sitemaps(roots, fetch, on_site) -> tuple[list[dict[str, Any]], set[str], list]:
    """The sitemaps reachable from the first root that answers, and every address listed."""
    notes: list[str] = []
    for root in roots:
        page = await fetch(root, SITEMAP_MAX_BYTES)
        if page.status != 200:
            continue
        queue, seen, sitemaps, listed = [(root, page)], {root}, [], set()
        while queue:
            url, page = queue.pop(0)
            locs = [html.unescape(loc) for loc in _LOC.findall(page.text)]
            if "<sitemapindex" in page.text:
                for child in locs:
                    if child in seen or not on_site(child):
                        continue
                    if len(seen) >= MAX_SITEMAPS:
                        notes.append(f"Only the first {MAX_SITEMAPS} sitemaps were read.")
                        break
                    seen.add(child)
                    child_page = await fetch(child, SITEMAP_MAX_BYTES)
                    if child_page.status == 200:
                        queue.append((child, child_page))
                    else:
                        notes.append(f"{child} answered HTTP {child_page.status}.")
            else:
                sitemaps.append({"url": url, "addresses": len(locs)})
                listed.update(_key(loc) for loc in locs)
        return sitemaps, listed, notes
    notes.append("No sitemap answered: " + ", ".join(roots) + ".")
    return [], set(), notes


def _key(url: str) -> str:
    """An address reduced for comparison (as the bridge's wpmcp_link_key does)."""
    parts = urlsplit(url.strip())
    return (parts.netloc + unquote(parts.path).rstrip("/")).lower()


# ------------------------------------------------------------------------- one page


class _Head(HTMLParser):
    """The tags of a page that search engines read; everything else is skipped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang: str | None = None
        self.titles: list[str] = []
        self.meta: dict[str, list[str]] = {}
        self.canonicals: list[str] = []
        self.hreflang: dict[str, str] = {}
        self.json_ld: list[str] = []
        self.h1: list[str] = []
        self._capture: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang") or None
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name and "content" in a:
                self.meta.setdefault(name, []).append(a["content"])
        elif tag == "link":
            rel = a.get("rel", "").lower().split()
            if "canonical" in rel and a.get("href"):
                self.canonicals.append(a["href"])
            if "alternate" in rel and a.get("hreflang") and a.get("href"):
                self.hreflang.setdefault(a["hreflang"].lower(), a["href"])
        elif tag == "title" and self._capture is None:
            self._start("title")
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._start("ld")
        elif tag == "h1" and self._capture is None:
            self._start("h1")

    def _start(self, what: str) -> None:
        self._capture, self._text = what, []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        ends = {"title": "title", "script": "ld", "h1": "h1"}
        if self._capture is None or ends.get(tag) != self._capture:
            return
        text = "".join(self._text)
        if self._capture == "ld":
            self.json_ld.append(text)
        else:
            text = " ".join(text.split())
            (self.titles if self._capture == "title" else self.h1).append(text)
        self._capture = None


def _types(node: Any, found: list[str]) -> None:
    """The @type of each JSON-LD node and of the nodes of its @graph."""
    if isinstance(node, list):
        for each in node:
            _types(each, found)
    elif isinstance(node, dict):
        kind = node.get("@type")
        for name in kind if isinstance(kind, list) else [kind]:
            if isinstance(name, str) and name not in found:
                found.append(name)
        _types(node.get("@graph"), found)


def _first(values: list[str] | None) -> str | None:
    return values[0] if values else None


def summarise(page: PublicPage, item: dict[str, Any], translations: dict[int, dict]) -> dict:
    """The SEO summary of a fetched page, with checks against what the bridge knows."""
    result: dict[str, Any] = {"id": item["id"], "url": item["link"], "status": page.status}
    checks: list[str] = []
    if page.status in (301, 302, 303, 307, 308):
        result["redirect_to"] = page.location
        checks.append(f"The address redirects (HTTP {page.status}) to {page.location}.")
        result["checks"] = checks
        return result
    if page.status != 200:
        checks.append(f"HTTP {page.status}: search engines do not index an error page.")
    if "html" not in page.content_type:
        result["checks"] = [*checks, f"Not an HTML page ({page.content_type or 'no type'})."]
        return result

    head = _Head()
    head.feed(page.text)
    head.close()
    meta = head.meta
    header = [page.robots_header] if page.robots_header else []
    robots = ", ".join(meta.get("robots", []) + header)
    types: list[str] = []
    ld_errors = 0
    for block in head.json_ld:
        try:
            _types(json.loads(block), types)
        except ValueError:
            ld_errors += 1
    result.update(
        {
            "lang": head.lang,
            "title": _first(head.titles),
            "description": _first(meta.get("description")),
            "robots": robots or None,
            "canonical": _first(head.canonicals),
            "hreflang": head.hreflang,
            "og": {
                key: _first(meta.get(f"og:{key}"))
                for key in ("title", "description", "url", "image", "type", "locale")
                if meta.get(f"og:{key}")
            },
            "json_ld_types": types,
            "h1": head.h1[:5],
        }
    )

    if not head.titles or not head.titles[0]:
        checks.append("No <title>.")
    if not meta.get("description"):
        checks.append("No meta description.")
    for name in ("description", "robots"):
        if len(meta.get(name, [])) > 1:
            checks.append(f"{len(meta[name])} meta {name} tags (search engines may pick any).")
    if "noindex" in robots.lower():
        checks.append("noindex: search engines are asked not to list this page.")
    if not head.canonicals:
        checks.append("No canonical link.")
    elif len(set(head.canonicals)) > 1:
        checks.append(f"{len(set(head.canonicals))} different canonical links.")
    elif _key(head.canonicals[0]) != _key(item["link"]):
        checks.append(f"The canonical points elsewhere: {head.canonicals[0]}.")
    expected = {i["lang"]: i["link"] for i in [item, *translations.values()] if i.get("lang")}
    if len(expected) > 1:
        for lang, link in sorted(expected.items()):
            shown = head.hreflang.get(lang) or next(
                (url for code, url in head.hreflang.items() if code.split("-")[0] == lang), None
            )
            if shown is None:
                checks.append(f"hreflang has no {lang} alternate (item {_id_of(lang, item)}).")
            elif _key(shown) != _key(link):
                checks.append(f"hreflang {lang} points to {shown}, not {link}.")
    unpublished = set(item["translations"]) - set(expected)
    if unpublished:
        langs = ", ".join(sorted(unpublished))
        checks.append(f"Translations not published, so not in hreflang: {langs}.")
    if len(head.h1) != 1:
        checks.append(f"{len(head.h1)} H1 headings (one is usual).")
    if ld_errors:
        checks.append(f"{ld_errors} JSON-LD block(s) are not valid JSON.")
    for key in ("title", "description"):
        if not meta.get(f"og:{key}"):
            checks.append(f"No og:{key} (used when the page is shared).")
    result["checks"] = checks
    return result


def _id_of(lang: str, item: dict[str, Any]) -> int | str:
    return item["translations"].get(lang, "?")
