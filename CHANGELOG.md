# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-09-05

First release.

### Added

- **MCP stdio server** (`fds-mcp serve`) exposing 14 tools in three safety tiers.
- **Green tools** (no authentication, no side effects): `search_authorities`,
  `get_authority`, `get_law`, `check_jurisdiction`.
  - `get_authority` recomputes the law that the REST API would apply, because the API
    does not expose `default_law`.
  - `check_jurisdiction` walks the GeoRegion `part_of` chain and returns the evidence
    trail alongside the covering authorities.
- **Yellow tools** (OAuth token, read only): `list_my_requests`, `get_request`,
  `get_messages`, `list_attachments`, `download_attachment`, `check_deadlines`.
- **Red tools** (`dry_run=True` by default): `create_request_draft` (local YAML only, no
  network), `validate_draft`, `build_submit_url` (two-step above the measured 4096-byte
  URL limit), `submit_request`.
- **Five submission gates** enforced in code: approved status, no open ERROR finding,
  desired law equal to the API default, a human-set confirmation token, and local
  rate-limit bookkeeping against 5/5min, 6/6h, 10/24h and 20/7d.
- **Read-only HTTP client**: every non-GET method is refused unless `allow_write=True`
  is set explicitly. Pagination de-duplicates by id and raises rather than truncating
  silently.
- **OAuth 2.0 Authorization Code + PKCE** (`fds-mcp login`) with an HTTPS loopback
  listener on `https://localhost:8765/callback` and a `fragdenstaat://callback` fallback
  for manual paste. Tokens are stored at `~/.config/fds-mcp/tokens.json` with mode 0600
  and refreshed automatically.
- **Rule set** `R01`–`R17` offline and `L01`–`L05` live, reproducing froide's web-form
  validation, which is considerably stricter than the REST API's.
- CLI subcommands `serve`, `configure`, `login`, `whoami`, `status`, `logout`,
  `validate`.
- Test suite with `pytest-socket`; network access is blocked except for tests marked
  `live`, which perform read-only GETs.

### Known limitations

Not offered, because the API does not support it: replying to an authority by e-mail,
choosing the legal basis via `POST /api/v1/request/`, server-side drafts, and setting
status, resolution, tags or the law after the fact. Documenting postal mail (tus upload
plus `kind: post`) is possible through the API but is not implemented yet.

[0.1.0]: https://github.com/dwolbeck/fds-mcp/releases/tag/v0.1.0
