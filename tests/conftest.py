import asyncio

import httpx
import pytest

from grokipedia_mcp_server import server


@pytest.fixture
def mock_origin(monkeypatch):
    """Route all HTTP through an httpx.MockTransport and make the limiter/backoff instant.

    Usage: mock_origin(handler) where handler(request) -> httpx.Response.
    Returns a dict with "requests" (list of URLs) and "sleeps" (list of requested delays).
    """
    state = {"requests": [], "sleeps": []}
    real_sleep = asyncio.sleep

    async def fast_sleep(delay, *a, **k):
        state["sleeps"].append(delay)
        await real_sleep(0)

    def install(handler):
        def wrapped(request):
            state["requests"].append(str(request.url))
            return handler(request)

        monkeypatch.setattr(server, "_client", server.build_client(httpx.MockTransport(wrapped)))
        monkeypatch.setattr(server, "_limiter", server.RateLimiter(0))
        monkeypatch.setattr(server.asyncio, "sleep", fast_sleep)
        return state

    yield install
    server._client = None


def run(coro):
    return asyncio.run(coro)
