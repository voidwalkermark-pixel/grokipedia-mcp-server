"""Live smoke test against grokipedia.com (opt-in: `pytest -m live`).

search('Library of Alexandria') then get_page on the first hit. Grokipedia's backend can
return 502s or "Article Not Found" during outages; those are reported, not hidden.
"""

import json

import pytest

from grokipedia_mcp_server import server
from tests.conftest import run


@pytest.mark.live
def test_live_search_then_page():
    async def go():
        try:
            result = await server.search("Library of Alexandria", limit=5)
            print(json.dumps(result, indent=2, ensure_ascii=False)[:2000])
            if result.get("error") or not result.get("results"):
                return result, None
            page = await server.get_page(result["results"][0]["slug"])
            return result, page
        finally:
            await server.close_client()

    result, page = run(go())
    if result.get("error"):
        pytest.skip(f"upstream unavailable: {result['error']}")
    assert result["count"] >= 1
    assert page is not None and ("error" in page or page.get("title"))
    print(json.dumps({k: v for k, v in page.items() if k != "sections"}, indent=2, ensure_ascii=False)[:2000])
