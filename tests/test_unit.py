"""Offline unit tests (no network)."""

import time

import httpx
import pytest

from grokipedia_mcp_server import __version__, server
from tests.conftest import run

CF_502 = (
    "<!DOCTYPE html><html><head><title>grokipedia.com | 502: Bad gateway</title></head>"
    "<body><div id='cf-wrapper'><h1><span>Bad gateway</span> <span>Error code 502</span></h1>"
    "<p>Visit <a href='https://www.cloudflare.com/5xx'>cloudflare.com</a></p></div></body></html>"
)
STUB_404 = (
    "<html><head><title>Article Not Found — Grokipedia</title></head><body><main>"
    "<p>Not yet written</p><h1>Alexandria</h1><p>Grokipedia doesn't have an article with this title yet.</p>"
    "</main></body></html>"
)
CONTENT = (
    "# Library of Alexandria\n\nThe Library of Alexandria was a major library [1].\n\n"
    "## History\n\nFounded c. 285 BC.\n\n### Early history\n\nPtolemy I.\n\n"
    "## Destruction\n\nBurned [2].\n\n## A\n\nshort heading\n\n## Legacy of the library\n\nStill cited.\n"
)
PAGE = {
    "slug": "Library_of_Alexandria",
    "title": "Library of Alexandria",
    "content": CONTENT,
    "description": "A major library of the ancient world.",
    "citations": [
        {"id": "1", "title": "Src A", "description": "d", "url": "https://example.org/a"},
        {"id": "2", "title": "Src B", "description": "d", "url": "https://example.org/b"},
    ],
}


def api_found(request):
    if request.url.path == "/api/page-preview":
        return httpx.Response(200, json={"found": True, "page": PAGE})
    return httpx.Response(404, text=STUB_404)


# --- error pages are never articles -----------------------------------------


def test_cloudflare_502_page_is_not_an_article(mock_origin):
    mock_origin(lambda r: httpx.Response(502, text=CF_502, headers={"content-type": "text/html"}))
    res = run(server.get_page("Alexandria"))
    assert "error" in res and "502" in res["error"]
    assert "sections" not in res and res.get("found") is not True
    cites = run(server.get_page_citations("Alexandria"))
    assert "error" in cites


def test_4xx_html_is_not_found(mock_origin):
    def handler(r):
        if r.url.path == "/api/page-preview":
            return httpx.Response(200, json={"found": False, "page": None})
        return httpx.Response(404, text="<html><body><main><h1>Some page</h1><p>text</p></main></body></html>")

    mock_origin(handler)
    res = run(server.get_page("Whatever"))
    assert res["found"] is False and res["sections"] == [] and res["citations"] == []


def test_not_yet_written_stub(mock_origin):
    def handler(r):
        if r.url.path == "/api/page-preview":
            return httpx.Response(200, json={"found": False, "page": None})
        return httpx.Response(404, text=STUB_404)

    mock_origin(handler)
    res = run(server.get_page("Alexandria"))
    assert res["found"] is False and res["summary"] == "Article not yet written."
    cites = run(server.get_page_citations("Alexandria"))
    assert cites["found"] is False and cites["count"] == 0
    sec = run(server.get_page_section("Alexandria", "History"))
    assert "error" in sec and sec["found"] is False


# --- API path / shapes --------------------------------------------------------


def test_get_page_api_shape(mock_origin):
    mock_origin(api_found)
    res = run(server.get_page("Library of Alexandria"))
    assert res["found"] is True and res["source"] == "api"
    assert res["url"] == "https://grokipedia.com/page/Library_of_Alexandria"
    assert [s["heading"] for s in res["sections"]][:3] == ["Library of Alexandria", "History", "Early history"]
    assert res["citations"][0] == {"number": "1", "url": "https://example.org/a", "title": "Src A"}
    assert "truncated" not in res


# --- truncation --------------------------------------------------------------


def test_truncation_caps_large_pages(mock_origin):
    big = "# Big\n\n" + ("word " * 400_000) + "\n\n## Later\n\nmore text\n"

    def handler(r):
        return httpx.Response(200, json={"found": True, "page": {"slug": "Big", "title": "Big", "content": big}})

    mock_origin(handler)
    res = run(server.get_page("Big"))
    total = sum(len(s["content"]) for s in res["sections"])
    assert res["truncated"] is True
    assert total <= server.MAX_CONTENT_CHARS + 1
    assert [s["heading"] for s in res["sections"]] == ["Big", "Later"]


# --- section matching --------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected",
    [
        ("history", "History"),  # exact beats substring ("Early history")
        ("HISTORY", "History"),
        ("Early", "Early history"),
        ("legacy", "Legacy of the library"),
        ("a", "A"),  # exact short heading
    ],
)
def test_match_section(query, expected):
    sections = server._extract_sections(CONTENT)
    assert server.match_section(sections, query)["heading"] == expected


@pytest.mark.parametrize("query", ["Legacy and influence", "zzz", "Destruction of the library", "  "])
def test_match_section_no_reverse_or_empty_match(query):
    sections = server._extract_sections(CONTENT) + [{"heading": "", "level": 2, "content": "x"}]
    assert server.match_section(sections, query) is None


def test_get_page_section_tool(mock_origin):
    mock_origin(api_found)
    res = run(server.get_page_section("Library_of_Alexandria", "history"))
    assert res["heading"] == "History" and res["content"] == "Founded c. 285 BC." and res["truncated"] is False
    miss = run(server.get_page_section("Library_of_Alexandria", "Legacy and influence"))
    assert miss["error"].startswith("section not found") and "History" in miss["available_headings"]


# --- host / redirect / slug safety -------------------------------------------


def test_offsite_redirect_refused(mock_origin):
    def handler(r):
        if r.url.host == "grokipedia.com":
            return httpx.Response(302, headers={"location": "https://evil.example/x"})
        return httpx.Response(200, json={"results": [{"slug": "Pwn", "title": "t", "snippet": "s"}]})

    state = mock_origin(handler)
    res = run(server.search("x"))
    assert "error" in res and "evil.example" in res["error"]
    assert not any("evil.example" in u for u in state["requests"])


def test_http_downgrade_redirect_refused(mock_origin):
    def handler(r):
        if r.url.scheme == "https":
            return httpx.Response(301, headers={"location": "http://grokipedia.com/api/full-text-search"})
        return httpx.Response(200, json={"results": []})

    state = mock_origin(handler)
    assert "error" in run(server.search("x"))
    assert all(u.startswith("https://") for u in state["requests"])


def test_request_rejects_non_grokipedia_urls():
    for url in ["https://grokipedia.com.evil.example/x", "https://evil.example/", "http://grokipedia.com/x"]:
        with pytest.raises(ValueError):
            run(server.request("GET", url))


def test_slug_is_fully_encoded(mock_origin):
    state = mock_origin(lambda r: httpx.Response(404, text=STUB_404))
    run(server.get_page("../api/stats?x=1#frag"))
    html_urls = [u for u in state["requests"] if "/page/" in u]
    assert html_urls == ["https://grokipedia.com/page/..%2Fapi%2Fstats%3Fx%3D1%23frag"]
    assert not any("/api/stats" in u for u in state["requests"])


# --- retries / Retry-After ---------------------------------------------------


def test_retries_5xx_with_backoff(mock_origin):
    state = mock_origin(lambda r: httpx.Response(502, text="error code: 502"))
    res = run(server.search("x"))
    assert res == {"error": "origin error (502)"}
    assert len(state["requests"]) == 1 + server.MAX_RETRIES
    assert state["sleeps"] == [1.0, 1.5, 2.25]


def test_retry_after_short_is_honored(mock_origin):
    calls = {"n": 0}

    def handler(r):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "3"})
        return httpx.Response(200, json={"results": []})

    state = mock_origin(handler)
    res = run(server.search("x"))
    assert res["count"] == 0 and state["sleeps"] == [3.0]


def test_retry_after_long_fails_fast(mock_origin):
    state = mock_origin(lambda r: httpx.Response(502, headers={"retry-after": "60"}, text=CF_502))
    res = run(server.search("x"))
    assert res == {"error": "origin error (502)"}
    assert len(state["requests"]) == 1 and state["sleeps"] == []


def test_network_errors_retried(mock_origin):
    def handler(r):
        raise httpx.RemoteProtocolError("server disconnected", request=r)

    state = mock_origin(handler)
    res = run(server.search("x"))
    assert "disconnected" in res["error"] and len(state["requests"]) == 1 + server.MAX_RETRIES


# --- search rows -------------------------------------------------------------


def test_bad_search_rows_are_skipped(mock_origin):
    rows = [
        {"slug": "Library_of_Alexandria", "title": "Library of Alexandria",
         "snippet": "The <em>Library</em>  of\nAlexandria", "relevanceScore": 12.3},
        {"slug": None, "title": "bad"},
        {"title": "no slug"},
        "not-a-dict",
        {"slug": 42},
        {"slug": "a/b?c", "title": "odd"},
    ]
    mock_origin(lambda r: httpx.Response(200, json={"results": rows}))
    res = run(server.search("lib", limit=500, offset=-3))
    assert res["limit"] == 50 and res["offset"] == 0
    assert [r["slug"] for r in res["results"]] == ["Library_of_Alexandria", "a/b?c"]
    assert res["results"][0]["snippet"] == "The Library of Alexandria"
    assert res["results"][1]["url"] == "https://grokipedia.com/page/a%2Fb%3Fc"


def test_search_non_dict_json(mock_origin):
    mock_origin(lambda r: httpx.Response(200, json=["x"]))
    assert "error" in run(server.search("x"))


def test_empty_inputs():
    assert run(server.search("  ")) == {"error": "query required"}
    assert run(server.get_page(""))["error"] == "slug required"
    assert run(server.get_page_section("x", " "))["error"] == "heading required"


# --- rate limiter ------------------------------------------------------------


def test_rate_limiter_spacing():
    import asyncio

    async def go():
        lim = server.RateLimiter(0.2)
        stamps = []

        async def one():
            await lim.wait()
            stamps.append(time.monotonic())

        await asyncio.gather(*[one() for _ in range(4)])
        return stamps

    s = run(go())
    gaps = [b - a for a, b in zip(s, s[1:])]
    assert min(gaps) >= 0.19


def test_version_reported():
    assert server.mcp._mcp_server.version == __version__
