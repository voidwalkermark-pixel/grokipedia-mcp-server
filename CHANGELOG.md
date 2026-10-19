# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [0.1.2] - 2026-10-19

### Added
- `get_page_section`: new `include_subsections` argument (default `true`). When the matched heading's
  own body is empty (a parent such as "History" whose text is in its subsections), the subsections'
  text is returned with their markdown headings, within the existing 20,000-character cap and
  `truncated` flag. New `subsections` field lists the nested headings; `includes_subsections` says
  whether their text was used.
- `get_page_citations`: each citation now has `sections`, the headings under which its inline marker
  appears in the article text. Markers in the API markdown are empty-text links (`[](url)`) and are
  matched to the reference list by exact URL. New `include_context` argument (default `false`) adds
  up to 3 short preceding-text snippets per citation. New top-level `section_mapping` is
  `"inline_markers"` or `"unavailable"` (then every `sections` is `null`; nothing is guessed, e.g. on the
  HTML fallback).

### Fixed
- `get_page` summary no longer starts with the page title repeated on its own line.
- Citation `number` (API path) is now the reference's 1-based position in the list, which is the
  number the website displays (`[n]` / `#ref-n`). The API's `id` field was used before, and it is
  sometimes out of order or empty.

## [0.1.1] - 2026-10-18

### Changed
- Metadata: author name updated (LICENSE, `pyproject.toml`, README).
- README: Windows install notes (harmless `git clone --branch` tag warning on Git 2.55 under
  PowerShell `$ErrorActionPreference = 'Stop'`; using `python` where the `py` launcher is not installed).
- No code changes.

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

[0.1.2]: https://github.com/voidwalkermark-pixel/grokipedia-mcp-server/releases/tag/v0.1.2
[0.1.1]: https://github.com/voidwalkermark-pixel/grokipedia-mcp-server/releases/tag/v0.1.1
[0.1.0]: https://github.com/voidwalkermark-pixel/grokipedia-mcp-server/releases/tag/v0.1.0
