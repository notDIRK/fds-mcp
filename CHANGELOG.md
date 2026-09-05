# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`build_reply_draft`** (yellow): prepares a follow-up message to an authority — looks
  the request up, validates the text, and returns the finished message, the subject in
  froide's own format (`AW: <title> [#<id>]`) and the URL of the form. It sends nothing,
  and there is no argument that makes it.
- **Rule `R19-reply-needs-salutation`**, the inverse of `R10`. froide does not frame a
  follow-up, so salutation and closing formula have to stand in the text, exactly once
  each. The reply rule set also applies `R04` (the form is prefilled with U+2026), `R06`
  and the 230-character subject cap.
- **Rules `R18-full-text-self-contained` and `L06-required-elements`**, which check the
  letter the authority *receives* rather than the body that was written: legal basis (the
  only `ERROR`), cost pre-notification, cost cap, deadline, forwarding when not
  responsible, electronic reply.
- **`send_reply_via_browser`** (red, opt-in): sends an approved reply draft by driving a
  real browser. Not registered unless `FDS_MCP_BROWSER_SEND=1`; needs the optional extra
  `fds-mcp[browser]`. Five gates plus in-form checks, and an API confirmation afterwards —
  without it the outcome is reported as `unconfirmed`, never as success. The README states
  plainly what switching it on costs.
- **`tests/test_api_contract.py`**: live learning tests that fire when FragDenStaat changes
  the limits this server is built around — the `kind: "email"` refusal with its
  `kind: "post"` calibration, the web view's indifference to bearer tokens, and the set of
  resources the API router exposes.
- **German documentation** alongside the English original: `README.de.md` and
  `docs/oauth-setup.de.md`, linked in both directions. US English is the source.
- Environment variables `FDS_MCP_BROWSER_SEND` and `FDS_MCP_BROWSER_PROFILE`.

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

[0.1.0]: https://github.com/notDIRK/fds-mcp/releases/tag/v0.1.0
