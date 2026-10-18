# grokipedia-mcp-server

An **unofficial, read-only** [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server
for [Grokipedia](https://grokipedia.com). It lets an MCP client (Claude Desktop, Cursor, VS Code,
Grok-compatible agents, etc.) search Grokipedia and read articles, sections and citations over stdio.

> **Not affiliated with, endorsed by, or supported by xAI or Grokipedia.** It uses Grokipedia's
> public but **unofficial, undocumented** endpoints, which may change or break at any time.

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `search` | `query` (str), `limit` (1–50, default 10), `offset` (default 0) | `{query, limit, offset, count, results: [{slug, title, snippet, relevance, url}]}` |
| `get_page` | `slug` (str; spaces become `_`) | `{title, slug, url, summary, sections: [{heading, level, content}], citations: [{number, url, title}], found, source, truncated?}` |
| `get_page_section` | `slug`, `heading` | `{slug, title, heading, level, content, truncated, url}` (exact case-insensitive heading match first, then a heading containing the text) |
| `get_page_citations` | `slug` | `{found, slug, title, url, citations, count}` |

All tools are annotated read-only. Failures come back as a normal result with an `"error"` key
(for example `{"error": "origin error (502)"}`); a page that doesn't exist returns `"found": false`.

## Install

Requires **Python 3.11–3.13**. The server is installed straight from this GitHub repo (it is not on PyPI).

### Option A: uv / uvx (recommended, any OS)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run it without a manual install:

```bash
uvx --from git+https://github.com/voidwalkermark-pixel/grokipedia-mcp-server@v0.1.1 grokipedia-mcp-server --version
```

Or install it as a persistent tool (puts `grokipedia-mcp-server` on your PATH):

```bash
uv tool install git+https://github.com/voidwalkermark-pixel/grokipedia-mcp-server@v0.1.1
```

### Option B: pip in a virtual environment

**Linux / macOS**

```bash
python3 -m venv ~/.venvs/grokipedia-mcp
~/.venvs/grokipedia-mcp/bin/pip install "git+https://github.com/voidwalkermark-pixel/grokipedia-mcp-server@v0.1.1"
~/.venvs/grokipedia-mcp/bin/grokipedia-mcp-server --version
```

**Windows (PowerShell)**

```powershell
py -3.13 -m venv "$env:USERPROFILE\.venvs\grokipedia-mcp"
& "$env:USERPROFILE\.venvs\grokipedia-mcp\Scripts\pip.exe" install "git+https://github.com/voidwalkermark-pixel/grokipedia-mcp-server@v0.1.1"
& "$env:USERPROFILE\.venvs\grokipedia-mcp\Scripts\grokipedia-mcp-server.exe" --version
```

If the `py` launcher isn't installed, use `python -m venv ...` instead (check `python --version` is 3.11–3.13).

`pip install git+...` needs [Git](https://git-scm.com/download/win) on PATH. Without Git, install from
the release archive instead:
`pip install https://github.com/voidwalkermark-pixel/grokipedia-mcp-server/archive/refs/tags/v0.1.1.zip`

### Option C: reproducible install with hash-pinned dependencies

```bash
git clone --branch v0.1.1 https://github.com/voidwalkermark-pixel/grokipedia-mcp-server
cd grokipedia-mcp-server
python -m venv .venv
# Linux/macOS: .venv/bin/python   Windows: .venv\Scripts\python.exe
.venv/bin/python -m pip install --require-hashes -r requirements.lock.txt
.venv/bin/python -m pip install --no-deps .
```

> **Windows note:** with Git for Windows 2.55, `git clone --branch vX.Y.Z ...` may print a warning like
> `refs/tags/vX.Y.Z ... is not a commit!` for the annotated release tag. It is harmless (the clone
> checks out the tagged commit; confirm with `git -C grokipedia-mcp-server describe --tags`), but Git
> writes it to stderr, so PowerShell with `$ErrorActionPreference = 'Stop'` may treat it as a failure.
> Run the clone with `$ErrorActionPreference = 'Continue'` for that command, or check `$LASTEXITCODE`
> (0 = success) instead of relying on stderr.

`requirements.lock.txt` is a cross-platform lock (Linux, macOS, Windows; Python ≥ 3.11) generated with
`uv pip compile requirements.txt --universal --python-version 3.11 --generate-hashes`.

## MCP client configuration (stdio)

Use the **absolute path** to the installed executable.

Linux / macOS:

```json
{
  "mcpServers": {
    "grokipedia": {
      "command": "/home/you/.venvs/grokipedia-mcp/bin/grokipedia-mcp-server",
      "args": []
    }
  }
}
```

Windows (note the doubled backslashes in JSON):

```json
{
  "mcpServers": {
    "grokipedia": {
      "command": "C:\\Users\\you\\.venvs\\grokipedia-mcp\\Scripts\\grokipedia-mcp-server.exe",
      "args": []
    }
  }
}
```

With uvx instead of a venv:

```json
{
  "mcpServers": {
    "grokipedia": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/voidwalkermark-pixel/grokipedia-mcp-server@v0.1.1", "grokipedia-mcp-server"]
    }
  }
}
```

`python -m grokipedia_mcp_server` also works as the command.

## Behaviour and limits

- **Network:** only HTTPS GETs to `grokipedia.com`, enforced on every request *and every redirect hop*.
  Proxy and netrc environment variables are ignored. No credentials, no telemetry, no file writes.
- **Rate limit:** at most **1 request/second** per server process (shared by all tools). `get_page`,
  `get_page_section` and `get_page_citations` each fetch the page (1–2 requests); there is no cache yet.
- **Retries:** up to 3 retries on HTTP 429/5xx and transient network errors, with 1 s / 1.5 s / 2.25 s
  backoff. A `Retry-After` of up to 10 s is honoured; a longer one fails fast instead of hammering a
  struggling origin.
- **Timeouts:** 10 s connect, 30 s per read, and an overall 50 s budget per tool call.
- **Page fetching:** tries `/api/page-preview?slug=` first, then parses the public `/page/<slug>` HTML.
  Error pages (e.g. a Cloudflare "502 Bad gateway") are never returned as articles.
- **Size cap:** article text is capped at 20,000 characters per call; `truncated: true` marks a cut.
  Use `get_page_section` to read a specific part of a long article.
- **Outages:** when Grokipedia's backend is down you will see errors such as `origin error (502)` or
  `found: false` / "Article not yet written." for pages that do exist. Cloudflare may still serve some
  cached responses during an outage, so results can be inconsistent between queries.
- **Untrusted content:** returned article text is third-party content; MCP clients and models should
  treat it as data, not instructions.

## Development

```bash
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --require-hashes -r requirements.lock.txt
pip install --no-deps -e . && pip install pytest
pytest                 # offline unit tests + stdio test
pytest -m live -s      # opt-in live smoke test against grokipedia.com
```

## Origin

The first draft of this server was generated with Grok. It was then reviewed line by line, tested,
and hardened (redirect host checks, slug encoding, error-page handling, truncation, retry and
`Retry-After` fixes, pinned and hash-locked dependencies) before release. See `CHANGELOG.md`.

## License

[MIT](LICENSE) © 2026 Voidwalker Mark
