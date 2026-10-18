# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [0.1.0] - 2026-10-18

Initial public release.

### Added
- Read-only MCP server (stdio) for grokipedia.com with four tools: `search`, `get_page`,
  `get_page_section`, `get_page_citations`.
- Page fetch via `/api/page-preview` with fallback to parsing the public `/page/<slug>` HTML.
- Process-wide rate limit (1 request/second), 3 retries with backoff on 429/5xx and transient
  network errors, `Retry-After` support (fail fast if the origin asks for more than 10 s),
  50 s overall budget per tool call.
- Console script `grokipedia-mcp-server`; `python -m grokipedia_mcp_server`.
- Offline unit tests, stdio integration test, opt-in live smoke test (Python 3.11–3.13).

### Security / hardening (relative to the initial generated draft)
- Every request, including redirect hops, must be `https://grokipedia.com`.
- Slugs are fully percent-encoded (no path traversal to other endpoints).
- Origin/Cloudflare error pages (5xx/429) and 4xx pages are never returned as articles.
- Article text capped at 20,000 characters per call (`truncated: true` when cut).
- Exact heading match before substring match; no reverse/empty-heading matches.
- Malformed search rows are skipped instead of failing the whole search.
- Proxy/netrc environment variables ignored; tools annotated read-only.
- Dependencies pinned with a hash lock; lxml 6.1.3 (fixes CVE-2026-41066).

[0.1.0]: https://github.com/voidwalkermark-pixel/grokipedia-mcp-server/releases/tag/v0.1.0
