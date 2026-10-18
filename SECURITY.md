# Security Policy

## Scope and design

This server is read-only. It only makes HTTPS GET requests to `grokipedia.com` (enforced on every
request and redirect), needs no credentials, writes no files, starts no subprocesses, and sends no
telemetry. Page text returned to your MCP client is third-party content: treat it as untrusted input
(it could contain text that tries to instruct an AI model).

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please report privately: if the repository's **Security** tab offers **Report a vulnerability**, use
that. Otherwise open an issue titled "Security contact request" with no details, and a private channel
will be arranged. Please do not post vulnerability details publicly before a fix is available.
