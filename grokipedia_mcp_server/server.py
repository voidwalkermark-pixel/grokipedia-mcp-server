"""
Unofficial, read-only MCP server for Grokipedia (grokipedia.com).

- Contacts only https://grokipedia.com (enforced on every request, including redirects).
- No auth, no telemetry, no file writes, no subprocesses.
- stdio transport via the official MCP Python SDK (FastMCP, mcp 1.x).

Not affiliated with or endorsed by xAI. Grokipedia's endpoints used here are
unofficial and may change or break at any time.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import __version__

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_HOST = "grokipedia.com"
BASE = f"https://{ALLOWED_HOST}"
SEARCH_URL = f"{BASE}/api/full-text-search"
PAGE_PREVIEW_URL = f"{BASE}/api/page-preview"
PAGE_HTML_URL = f"{BASE}/page/{{slug}}"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

TIMEOUT = 30.0  # per network phase (read/write/pool); connect is 10s
CONNECT_TIMEOUT = 10.0
TOOL_DEADLINE = 50.0  # overall budget per tool call (MCP clients often time out ~60s)
MIN_INTERVAL = 1.0  # at most 1 request per second (process-wide)
MAX_RETRIES = 3  # retries after the first attempt
BACKOFF_BASE = 1.5  # 1s, 1.5s, 2.25s
MAX_RETRY_AFTER = 10.0  # if origin asks to wait longer than this, fail fast
MAX_CONTENT_CHARS = 20000  # cap on returned article text per call

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)

# ---------------------------------------------------------------------------
# Rate limiter + resilient HTTP client
# ---------------------------------------------------------------------------


class RateLimiter:
    def __init__(self, min_interval: float = MIN_INTERVAL) -> None:
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self.min_interval:
                await asyncio.sleep(self.min_interval - delta)
            self._last = time.monotonic()


_limiter = RateLimiter()
_client: httpx.AsyncClient | None = None


async def _check_host(request: httpx.Request) -> None:
    """Refuse any request (including redirect hops) that is not https://grokipedia.com."""
    if request.url.scheme != "https" or request.url.host != ALLOWED_HOST:
        raise httpx.UnsupportedProtocol(f"refusing off-host request to {request.url.host}")


def build_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9,*/*;q=0.8"},
        timeout=httpx.Timeout(TIMEOUT, connect=CONNECT_TIMEOUT),
        follow_redirects=True,
        max_redirects=5,
        event_hooks={"request": [_check_host]},
        trust_env=False,  # ignore proxy / netrc environment
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        transport=transport,
    )


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = build_client()
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _short_error(status: int | None, text: str | None = None) -> str:
    if status == 404:
        return "not found"
    if status == 429:
        return "rate limited by origin"
    if status and 500 <= status < 600:
        return f"origin error ({status})"
    if text and "<html" in text.lower()[:200]:
        return "unexpected HTML response"
    return "request failed"


async def request(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """Rate-limited request with retries on 429/5xx and transient network errors."""
    if not url.startswith(BASE + "/"):
        raise ValueError("refusing non-grokipedia.com URL")

    client = get_client()
    last_exc: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        await _limiter.wait()
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if attempt < MAX_RETRIES:
                    delay = BACKOFF_BASE**attempt
                    ra = resp.headers.get("retry-after", "").strip()
                    if ra.isdigit():
                        if float(ra) > MAX_RETRY_AFTER:
                            return resp  # origin asked for a long pause: fail fast
                        delay = max(delay, float(ra))
                    await asyncio.sleep(delay)
                    continue
            return resp
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                await asyncio.sleep(BACKOFF_BASE**attempt)
                continue
            raise

    raise last_exc or RuntimeError("request failed after retries")


async def get_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    resp = await request("GET", url, params=params)
    if resp.status_code >= 400:
        raise RuntimeError(_short_error(resp.status_code, resp.text[:100]))
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError("invalid JSON from origin") from None
    if not isinstance(data, dict):
        raise RuntimeError("unexpected JSON shape from origin")
    return data


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _slug_url(slug: str) -> str:
    # safe="" so "/", "?", "#" and ".." can never change the request path
    return f"{BASE}/page/{quote(slug, safe='')}"


def _extract_citations_from_markdown(content: str) -> list[dict[str, Any]]:
    """Pull numbered citations and bare URLs from markdown-ish content."""
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()

    for m in re.finditer(r"\[(\d+)\](?:\((https?://[^\s)]+)\))?", content):
        num = int(m.group(1))
        url = m.group(2) or ""
        key = url or f"#{num}"
        if key not in seen:
            seen.add(key)
            citations.append({"number": num, "url": url})

    for m in re.finditer(r"https?://[^\s\]\)\"']+", content):
        url = m.group(0).rstrip(".,;:")
        if url not in seen and not url.startswith(BASE):
            seen.add(url)
            citations.append({"number": None, "url": url})

    return citations[:100]


def _extract_sections(content: str) -> list[dict[str, Any]]:
    """Split markdown-ish content into sections by headings."""
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in content.splitlines():
        m = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if m:
            if current:
                current["content"] = _clean_text(current["content"])
                sections.append(current)
            current = {"heading": m.group(2).strip(), "level": len(m.group(1)), "content": ""}
        elif current is not None:
            current["content"] += line + "\n"

    if current:
        current["content"] = _clean_text(current["content"])
        sections.append(current)

    return sections


def _summary_from_content(content: str, max_len: int = 600) -> str:
    text = re.sub(r"^#{1,6}\s+.*$", "", content, flags=re.MULTILINE)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return _clean_text(content)[:max_len]
    s = paragraphs[0]
    if len(s) > max_len:
        s = s[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return s


def strip_leading_title(summary: str, title: str) -> str:
    """Drop a leading line that just repeats the page title (e.g. "Title\n\nText...").

    Only a whole first line (optionally a markdown heading) equal to the title is removed, so
    a sentence that merely starts with the title word ("Python is ...") is left untouched.
    """
    if not summary or not title:
        return summary
    lines = summary.lstrip().split("\n", 1)
    first = re.sub(r"^#{1,6}\s+", "", lines[0]).strip()
    if len(lines) == 2 and first.casefold() == title.strip().casefold() and lines[1].strip():
        return lines[1].strip()
    return summary


def section_with_subsections(sections: list[Any], sec: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Return (combined content, descendant headings) for a section in a flat heading list.

    Descendants are the following sections with a deeper level, up to the next heading of the
    same or a higher level. Combined content is the section's own text followed by each
    descendant as "<#...> heading" + text.
    """
    secs = [s for s in sections if isinstance(s, dict)]
    try:
        idx = next(i for i, s in enumerate(secs) if s is sec)
    except StopIteration:
        return sec.get("content") or "", []
    level = sec.get("level") or 0
    children: list[dict[str, Any]] = []
    for s in secs[idx + 1 :]:
        lv = s.get("level") or 0
        if not isinstance(lv, int) or not isinstance(level, int) or lv <= level:
            break
        children.append(s)
    parts = [sec.get("content") or ""] if (sec.get("content") or "") else []
    for ch in children:
        lv = ch.get("level") if isinstance(ch.get("level"), int) else level + 1
        block = f"{'#' * max(1, min(lv, 6))} {ch.get('heading') or ''}".rstrip()
        if ch.get("content"):
            block += "\n\n" + ch["content"]
        parts.append(block)
    return "\n\n".join(parts), [{"heading": ch.get("heading"), "level": ch.get("level")} for ch in children]


# Inline citation markers in the API markdown are empty-text links to the source URL, e.g.
# "...wiring.[](https://example.org/src)". A numbered link "[3](https://...)" is accepted too.
_CITE_MARKER = re.compile(r"\[(\d*)\]\((https?://[^\s)]+)\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
CONTEXT_CHARS = 160


def _plain(text: str) -> str:
    """Markdown-ish text -> plain text for context snippets."""
    text = _CITE_MARKER.sub("", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return _clean_text(text.replace("**", "").replace("__", ""))


def map_citation_sections(
    citations: list[dict[str, Any]], content: str | None, with_context: bool = False
) -> tuple[list[dict[str, Any]], str]:
    """Attach the headings under which each citation is referenced in the article text.

    Mapping is by exact URL match between inline markers and the citation list; nothing is
    inferred. Returns (citations, mode) where mode is "inline_markers" when at least one marker
    matched a citation (each citation then gets "sections": [...], possibly empty), or
    "unavailable" (each citation gets "sections": None).
    """
    by_url: dict[str, int] = {}
    for i, c in enumerate(citations):
        u = c.get("url") or ""
        if u and u not in by_url:
            by_url[u] = i
    hits: dict[int, list[str]] = {}
    ctx: dict[int, list[dict[str, str]]] = {}
    matched = False
    heading: str | None = None
    for line in (content or "").splitlines():
        m = _HEADING.match(line.strip())
        if m:
            heading = m.group(2).strip()
            continue
        for mk in _CITE_MARKER.finditer(line):
            i = by_url.get(mk.group(2))
            if i is None:
                continue
            matched = True
            if heading is not None and heading not in hits.setdefault(i, []):
                hits[i].append(heading)
            if with_context:
                before = _plain(line[: mk.start()])
                snippet = before[-CONTEXT_CHARS:]
                if len(before) > CONTEXT_CHARS:
                    snippet = "…" + snippet.split(" ", 1)[-1]
                lst = ctx.setdefault(i, [])
                if len(lst) < 3:
                    lst.append({"section": heading, "text": snippet})
    out = []
    for i, c in enumerate(citations):
        c = {**c, "sections": hits.get(i, []) if matched else None}
        if with_context:
            c["contexts"] = ctx.get(i, []) if matched else None
        out.append(c)
    return out, ("inline_markers" if matched else "unavailable")


def _not_found(slug: str, title: str | None = None, summary: str = "Article not found.") -> dict[str, Any]:
    return {
        "title": title or slug.replace("_", " "),
        "slug": slug,
        "url": _slug_url(slug),
        "summary": summary,
        "sections": [],
        "citations": [],
        "found": False,
        "source": "html",
    }


def parse_html_page(html: str, slug: str) -> dict[str, Any]:
    """Fallback: parse article HTML into the same shape as the API path."""
    soup = BeautifulSoup(html, "lxml")

    body_text = soup.get_text(" ", strip=True)
    if "Not yet written" in body_text or "doesn't have an article" in body_text.lower():
        title_el = soup.find("h1")
        return _not_found(slug, title_el.get_text(strip=True) if title_el else None, "Article not yet written.")

    title_el = soup.find("h1") or soup.find("title")
    title = title_el.get_text(strip=True) if title_el else slug.replace("_", " ")
    if " — Grokipedia" in title:
        title = title.split(" — ")[0].strip()

    main = soup.find("main") or soup.find("article") or soup.find("body")
    content_parts: list[str] = []
    if main:
        for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote"]):
            text = el.get_text(" ", strip=True)
            if not text:
                continue
            if el.name.startswith("h"):
                content_parts.append(f"{'#' * int(el.name[1])} {text}")
            else:
                content_parts.append(text)
    content = "\n\n".join(content_parts) or body_text

    citations = _extract_citations_from_markdown(content)
    if main:
        for a in main.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and not href.startswith(BASE):
                if not any(c["url"] == href for c in citations):
                    citations.append({"number": None, "url": href, "title": a.get_text(strip=True)[:120]})

    return {
        "title": title,
        "slug": slug,
        "url": _slug_url(slug),
        "summary": _summary_from_content(content),
        "sections": _extract_sections(content),
        "citations": citations[:80],
        "found": True,
        "source": "html",
    }


async def fetch_page_data(slug: str) -> dict[str, Any]:
    """Public page shape (without the internal raw-content field)."""
    page = await fetch_page_raw(slug)
    page.pop("_content", None)
    return page


async def fetch_page_raw(slug: str) -> dict[str, Any]:
    """Prefer /api/page-preview; fall back to parsing the public HTML page.

    On the API path the result carries the raw markdown under "_content" (internal; stripped
    by fetch_page_data) so citation markers can be located in the article text.
    """
    slug = (slug or "").strip().replace(" ", "_")
    if not slug:
        raise ValueError("slug required")

    # 1. Structured endpoint used by the site's own clients
    try:
        data = await get_json(PAGE_PREVIEW_URL, params={"slug": slug})
        if data.get("found") and isinstance(data.get("page"), dict):
            page = data["page"]
            content = page.get("content") or page.get("content_text") or ""
            api_citations = page.get("citations")
            norm = []
            if isinstance(api_citations, list) and api_citations:
                # The site numbers references by their 1-based position in this list (the
                # rendered "[n]" markers link to "#ref-n"). The API's own "id" field is not
                # reliable (sometimes out of order or empty), so it is not used as the number.
                for i, c in enumerate(api_citations, 1):
                    if isinstance(c, dict):
                        norm.append({
                            "number": str(i),
                            "url": c.get("url") or c.get("href") or "",
                            "title": c.get("title") or "",
                        })
                    elif isinstance(c, str):
                        norm.append({"number": str(i), "url": c, "title": ""})
            else:
                for c in _extract_citations_from_markdown(content):
                    norm.append({"number": c.get("number"), "url": c.get("url") or "", "title": ""})
            sections = page.get("sections")
            if not isinstance(sections, list) or not all(isinstance(s, dict) for s in sections):
                sections = _extract_sections(content)
            return {
                "title": page.get("title") or slug.replace("_", " "),
                "slug": page.get("slug") or slug,
                "url": _slug_url(page.get("slug") or slug),
                "summary": strip_leading_title(
                    page.get("description") or page.get("summary") or _summary_from_content(content),
                    page.get("title") or slug.replace("_", " "),
                ),
                "sections": sections,
                "citations": norm,
                "found": True,
                "source": "api",
                "_content": content,
            }
    except Exception:
        pass  # API unavailable / not found: fall through to HTML

    # 2. HTML fallback
    try:
        resp = await request("GET", _slug_url(slug))
    except Exception as exc:
        raise RuntimeError(f"page unavailable: {exc}") from exc
    if resp.status_code == 429 or resp.status_code >= 500:
        # e.g. a Cloudflare "502 Bad gateway" page: never present it as an article
        raise RuntimeError(f"page unavailable: {_short_error(resp.status_code)}")
    parsed = parse_html_page(resp.text, slug)
    if resp.status_code >= 400 and parsed.get("found"):
        return _not_found(slug)
    return parsed


def truncate_page(page: dict[str, Any], max_chars: int = MAX_CONTENT_CHARS) -> dict[str, Any]:
    """Cap total section text so a huge article cannot flood the client's context."""
    budget = max_chars
    out = []
    truncated = False
    for sec in page.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        c = sec.get("content") or ""
        if budget <= 0:
            truncated = True
            out.append({**sec, "content": ""})
            continue
        if len(c) > budget:
            c = c[:budget] + "…"
            truncated = True
        budget -= len(c)
        out.append({**sec, "content": c})
    page = {**page, "sections": out}
    if truncated:
        page["truncated"] = True
    return page


def match_section(sections: list[Any], heading: str) -> dict[str, Any] | None:
    """Exact (case-insensitive) heading match first, then heading-contains-query."""
    want = heading.strip().lower()
    if not want:
        return None
    secs = [s for s in sections if isinstance(s, dict) and (s.get("heading") or "").strip()]
    for sec in secs:
        if sec["heading"].strip().lower() == want:
            return sec
    for sec in secs:
        if want in sec["heading"].lower():
            return sec
    return None


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("grokipedia", log_level="WARNING")
mcp._mcp_server.version = __version__


@mcp.tool(annotations=READ_ONLY)
async def search(query: str, limit: int = 10, offset: int = 0) -> dict[str, Any]:
    """Search Grokipedia full-text index.

    Args:
        query: Search terms.
        limit: Max results (1-50, default 10).
        offset: Pagination offset (default 0).
    """
    if not query or not query.strip():
        return {"error": "query required"}
    limit = max(1, min(int(limit), 50))
    offset = max(0, int(offset))

    try:
        data = await asyncio.wait_for(
            get_json(SEARCH_URL, params={"query": query.strip(), "limit": limit, "offset": offset}),
            TOOL_DEADLINE,
        )
        out = []
        for r in data.get("results") or []:
            if not isinstance(r, dict) or not isinstance(r.get("slug"), str) or not r["slug"]:
                continue  # skip malformed rows instead of failing the whole search
            out.append({
                "slug": r["slug"],
                "title": r.get("title"),
                "snippet": _clean_text(re.sub(r"<[^>]+>", "", str(r.get("snippet") or ""))),
                "relevance": r.get("relevanceScore") or r.get("relevance"),
                "url": _slug_url(r["slug"]),
            })
        return {"query": query, "limit": limit, "offset": offset, "count": len(out), "results": out}
    except asyncio.TimeoutError:
        return {"error": f"timed out after {TOOL_DEADLINE:.0f}s"}
    except Exception as exc:
        return {"error": str(exc)[:200]}


@mcp.tool(annotations=READ_ONLY)
async def get_page(slug: str) -> dict[str, Any]:
    """Fetch a Grokipedia page: title, summary, sections, citations, and URL.

    Tries the site's page-preview endpoint first, then falls back to parsing the public
    HTML page. Section text is capped (see "truncated").
    """
    try:
        return truncate_page(await asyncio.wait_for(fetch_page_data(slug), TOOL_DEADLINE))
    except asyncio.TimeoutError:
        return {"error": f"timed out after {TOOL_DEADLINE:.0f}s", "slug": slug}
    except Exception as exc:
        return {"error": str(exc)[:200], "slug": slug}


@mcp.tool(annotations=READ_ONLY)
async def get_page_section(slug: str, heading: str, include_subsections: bool = True) -> dict[str, Any]:
    """Return one section by heading (exact case-insensitive match, else heading containing the text).

    Args:
        slug: Page slug, e.g. "Printed_circuit_board".
        heading: Section heading to find.
        include_subsections: If the section's own body is empty (a parent heading whose text
            lives in its subsections), return the subsections' text with their headings
            (default true). "subsections" always lists the nested headings.
    """
    if not heading or not heading.strip():
        return {"error": "heading required", "slug": slug}
    try:
        page = await asyncio.wait_for(fetch_page_data(slug), TOOL_DEADLINE)
        if not page.get("found", True) and not page.get("sections"):
            return {"error": page.get("summary") or "page not available", "slug": slug, "found": False}

        sections = page.get("sections") or []
        sec = match_section(sections, heading)
        if sec is not None:
            own = sec.get("content") or ""
            combined, subsections = section_with_subsections(sections, sec)
            use_children = bool(include_subsections and not own.strip() and subsections)
            content = combined if use_children else own
            return {
                "slug": page.get("slug"),
                "title": page.get("title"),
                "heading": sec.get("heading"),
                "level": sec.get("level"),
                "content": content[:MAX_CONTENT_CHARS],
                "truncated": len(content) > MAX_CONTENT_CHARS,
                "includes_subsections": use_children,
                "subsections": subsections,
                "url": page.get("url"),
            }
        return {
            "error": f"section not found: {heading}",
            "available_headings": [s.get("heading") for s in sections if isinstance(s, dict)],
            "slug": slug,
        }
    except asyncio.TimeoutError:
        return {"error": f"timed out after {TOOL_DEADLINE:.0f}s", "slug": slug}
    except Exception as exc:
        return {"error": str(exc)[:200], "slug": slug}


@mcp.tool(annotations=READ_ONLY)
async def get_page_citations(slug: str, include_context: bool = False) -> dict[str, Any]:
    """Return the citations / references for a page, with where each is cited.

    Each citation has "sections": the headings under which its inline marker appears in the
    article text (matched by exact URL; [] if never cited inline). When the article text has
    no usable inline markers (e.g. HTML fallback), "sections" is null and "section_mapping" is
    "unavailable"; mappings are never guessed.

    Args:
        slug: Page slug.
        include_context: Also return up to 3 short text snippets preceding each marker.
    """
    try:
        page = await asyncio.wait_for(fetch_page_raw(slug), TOOL_DEADLINE)
        cites, mode = map_citation_sections(page.get("citations") or [], page.get("_content"), include_context)
        return {
            "found": page.get("found", True),
            "slug": page.get("slug"),
            "title": page.get("title"),
            "url": page.get("url"),
            "citations": cites,
            "count": len(cites),
            "section_mapping": mode,
        }
    except asyncio.TimeoutError:
        return {"error": f"timed out after {TOOL_DEADLINE:.0f}s", "slug": slug}
    except Exception as exc:
        return {"error": str(exc)[:200], "slug": slug}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Unofficial read-only MCP server for Grokipedia (stdio).")
    parser.add_argument("--version", action="version", version=f"grokipedia-mcp-server {__version__}")
    parser.parse_args()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
