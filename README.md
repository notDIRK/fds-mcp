# fds-mcp

*[Deutsche Fassung](README.de.md)*

An [MCP](https://modelcontextprotocol.io) server for the
[FragDenStaat.de](https://fragdenstaat.de) API — the German freedom-of-information
platform built on [froide](https://github.com/okfde/froide).

It gives an AI assistant the *research and preparation* side of an FOI request: find the
responsible authority and prove why it is responsible, read the applicable act and its
deadline, track your own requests, collect the replies and their attachments, and draft a
new request as a local file that you review before anything leaves your machine.

Submitting is possible, but it is deliberately the hardest thing this server does.

---

## ⚠️ `POST /api/v1/request/` sends immediately and irreversibly

FragDenStaat's REST API has **no draft mode, no preview and no undo**. The moment a
`POST /api/v1/request/` succeeds, the e-mail is on its way to the authority, the request
is public (by default) under CC0, and it cannot be recalled.

Worse, `MakeRequestSerializer` has **no `law_type` field**. The API therefore always
files under `publicbody.default_law`, and because froide sorts by `("-meta", "-priority")`
that is almost always the *combined meta act* — not the specific act you meant. For a
municipality in Rhineland-Palatinate, asking for the LTranspG (law 16) through the API
silently files under "LTranspG, VIG" (law 18) instead.

**Because of this, the recommended exit of this server is `build_submit_url`**, which
hands you a prefilled web form with the correct `law_type` and lets you press send
yourself. `submit_request` exists, defaults to `dry_run=True`, and refuses unless five
independent gates all agree — see [Safety model](#safety-model).

---

## Documentation

- [OAuth setup, with screenshots](docs/oauth-setup.md) — registering the application,
  scopes, the redirect-URI rules, and what to do when the local HTTPS listener is blocked
- [README.de.md](README.de.md) and [docs/oauth-setup.de.md](docs/oauth-setup.de.md) — the
  same documents in German. US English is the source; the German version follows it.

## Installation

```bash
pip install git+https://github.com/dwolbeck/fds-mcp.git
```

Or from a checkout:

```bash
git clone https://github.com/dwolbeck/fds-mcp.git
cd fds-mcp
pip install -e ".[dev]"
```

Requires Python 3.10 or newer.

### Register it with your MCP client

The server speaks stdio. For Claude Code:

```bash
claude mcp add fds -- fds-mcp serve
```

For a client that reads a JSON config:

```json
{
  "mcpServers": {
    "fds": {
      "command": "fds-mcp",
      "args": ["serve"]
    }
  }
}
```

The four green tools work immediately, with no account and no token.

---

## OAuth setup

The yellow and red tools need an OAuth 2.0 bearer token. FragDenStaat supports exactly
two authentication schemes for its API — OAuth2 and session cookies. There is **no
personal API key and no Basic Auth** (froide's own `docs/api.rst` claims otherwise; it is
out of date).

### 1. Register an application

Log in and open <https://fragdenstaat.de/account/applications/register/>. All account
pages are protected by `recent_auth_required`, so you may be asked for your password
again.

| Field | Value |
| --- | --- |
| Name | anything, e.g. `fds-mcp` |
| Client type | `public` (PKCE is then mandatory) |
| Authorization grant type | `authorization-code` |
| Redirect URI | `https://localhost:8765/callback` |

**Only the schemes `https` and `fragdenstaat` are accepted.** `http://localhost/...` is
rejected at registration time — this is `OAUTH2_PROVIDER.ALLOWED_REDIRECT_URI_SCHEMES` on
the server. That is why the default redirect is an HTTPS loopback listener with a
self-signed certificate that `fds-mcp` generates for you (via `openssl`), and why the
fallback is `fragdenstaat://callback` with a manual paste.

### 2. Configure and log in

```bash
fds-mcp configure --client-id <your-client-id>
fds-mcp login
```

`login` runs Authorization Code + PKCE (S256), opens your browser, and catches the
redirect on `https://localhost:8765/callback`. Your browser will warn about the
self-signed certificate — that is the local listener; accept it.

Without a browser or without `openssl`:

```bash
fds-mcp login --manual     # uses fragdenstaat://callback, you paste the URL back
```

Tokens land in `~/.config/fds-mcp/tokens.json` with mode `0600`. Refresh happens
automatically; refresh tokens are valid for **180 days**.

```bash
fds-mcp status     # config, token and throttle state
fds-mcp whoami     # the authenticated account
fds-mcp logout --revoke
```

### Scopes

`fds-mcp` requests `read:user read:request make:request` by default. Override with
`fds-mcp configure --scopes "read:user read:request"` if you never want to submit.

| Scope | Needed for |
| --- | --- |
| `read:user` | identifying your own account (`/api/v1/user/`) |
| `read:request` | your own, including non-public, requests |
| `make:request` | `POST /api/v1/request/` — submitting |
| `write:message`, `write:attachment` | documenting postal mail (not yet implemented) |

Nothing here grants deletion. If you drop `make:request`, `submit_request` can never
work, and everything else still does.

---

## Tools

| Tool | Tier | Auth | Side effect |
| --- | --- | --- | --- |
| `search_authorities(query, jurisdiction=None, limit=20)` | 🟢 green | none | none |
| `get_authority(id)` | 🟢 green | none | none |
| `get_law(id)` | 🟢 green | none | none |
| `check_jurisdiction(place_name)` | 🟢 green | none | none |
| `list_my_requests(status=None, limit=50)` | 🟡 yellow | token, read only | none |
| `get_request(id)` | 🟡 yellow | token, read only | none |
| `get_messages(request_id)` | 🟡 yellow | token, read only | none |
| `list_attachments(message_id)` | 🟡 yellow | token, read only | none |
| `download_attachment(attachment_id, target_dir)` | 🟡 yellow | token, read only | writes a local file |
| `check_deadlines()` | 🟡 yellow | token, read only | none |
| `build_reply_draft(request_id, text, subject=None, path=None)` | 🟡 yellow | token, read only | writes a local YAML file if `path` is given |
| `create_request_draft(...)` | 🔴 red | none | writes a local YAML file, **no network at all** |
| `validate_draft(path)` | 🔴 red | none | reads the API for L01–L05 |
| `build_submit_url(path)` | 🔴 red | none | writes a local `.body.txt` sidecar |
| `submit_request(path, confirmation_token)` | 🔴 red | token + `make:request` | **sends the request, irreversibly** |
| `send_reply_via_browser(draft_path, confirmation_token)` | 🔴 red, **opt-in** | a logged-in browser profile | **sends the reply, irreversibly** |

All four red tools take `dry_run: bool = True`.

`send_reply_via_browser` is the sixteenth tool and is **not registered** unless
`FDS_MCP_BROWSER_SEND=1` is set. Without that variable it does not appear in the tool
list at all. Read [Sending replies](#sending-replies) before you switch it on.

### `check_jurisdiction` returns its evidence

It walks `/georegion/?name=<place>` up its `part_of` chain and asks
`/publicbody/?regions=<id>` at every level, then returns the region chain, the matching
authorities, and the list of API URLs it used. That matters in Rhineland-Palatinate,
where an *Ortsgemeinde* is often not listed on FragDenStaat at all while the
*Verbandsgemeindeverwaltung* that administers it is.

### `build_submit_url` is two-step for long requests

Measured on 2026-09-05: fragdenstaat.de answers GET URLs above roughly **4096 bytes**
with HTTP 400 (4086 bytes → 200, 4106 bytes → 400). A typical 4000-character request
exceeds that once URL-encoded. Above the limit the tool returns a short URL that prefills
subject and `law_type`, plus the body in a `.body.txt` file next to your draft, which you
paste into the form.

### What FragDenStaat's API cannot do

These are frontend-only, with no REST equivalent. The server does not pretend otherwise:

- **replying to an authority** — `POST /api/v1/message/` only creates *postal* messages
  (`OnlyPostalMessagesWritable`), and `subject`/`content` are read-only serializer
  fields. E-mail replies go through `/anfrage/<slug>/send/message/`, a CSRF-protected
  Django view that ignores bearer tokens. See [Sending replies](#sending-replies);
- **choosing the legal basis** — no `law_type` in the serializer;
- **drafts** — `RequestDraft` is not registered in the API router;
- setting status, resolution, tags or the law after the fact; publishing a request;
  filing an objection or escalating to the state information commissioner.

---

## Sending replies

**An e-mail reply to an authority cannot be sent through the FragDenStaat API.** Not with
a different payload, not with an extra scope, not with a better token. Three
measurements, taken on 2026-09-05 and kept honest by `tests/test_api_contract.py`:

1. `POST /api/v1/message/` with `kind: "email"` answers **HTTP 400** and reports, under
   the key `kind`: *"Nachrichten dieser Art können nicht über die API erstellt werden."*
   That is froide's `OnlyPostalMessagesWritable`.
2. The identical call with `kind: "post"` also answers 400 — the probe deliberately
   carries an unresolvable request URI, so nothing can be created either way — but it
   carries **no `kind` error**. That is the calibration. Without it the first measurement
   would prove nothing: a 400 could just as well come from the invalid URI, from the
   endpoint refusing every POST, or from a missing scope.
3. `POST https://fragdenstaat.de/anfrage/<slug>/send/message/` answers **HTTP 302 to
   `/account/login/`** — identically with and without a bearer token, same status, same
   `Location`. The web view is session + CSRF only. OAuth is not a way around point 1.

So the honest answer is: a human sends the reply. `build_reply_draft` is what makes that
short.

### `build_reply_draft`

Looks the request up, validates your text, and hands back the finished message, a subject
in froide's own format (`AW: <title> [#<id>]`) and the URL of the form. It writes nothing
to the network — there is no argument that makes it send.

A follow-up is validated **differently from a request**, and the difference is easy to get
wrong. froide frames a new request with the act's `letter_start`/`letter_end`; it does not
frame a follow-up at all. The textarea arrives prefilled with

```
Guten Tag,

…

Mit freundlichen Grüßen
<your name>
```

and exactly what stands in it is what the authority receives. Hence:

- `R19` **requires** a salutation and a closing formula, each exactly once — the inverse
  of `R10`, which forbids both while the frame is in play;
- `R04` rejects the placeholder `…` (U+2026) that is sitting in that form right now. It
  is the single most likely mistake on this path;
- `R06` keeps e-mail addresses and IBANs out of a thread that is public and CC0;
- the subject is capped at 230 characters.

Every result carries one more warning, unconditionally: **the form has your postal
address prefilled**, behind a checkbox labelled *"Adresse mitsenden"*. On a public request,
ticking it publishes where you live, under CC0, permanently. Leave it unticked unless the
authority has explicitly asked for your postal address.

### `send_reply_via_browser` — optional, off by default

There is a way to automate the last step anyway: drive the form in a browser that carries
your logged-in session. This server can do that, and it is **not switched on**. It is
registered only when `FDS_MCP_BROWSER_SEND=1` is set, and it needs an extra:

```bash
pip install 'fds-mcp[browser]'
python -m playwright install chromium
export FDS_MCP_BROWSER_SEND=1
```

It never composes text. It sends the `subject` and `body` of a reply draft file that
`build_reply_draft` wrote and a human then approved — there is no other input it takes.
Five gates:

1. the file is a reply draft with `status: approved`, and `send_address` is false;
2. no `ERROR` finding is open under the follow-up rules;
3. `confirmation_token` matches, byte for byte, the token a human wrote into the file;
4. the local ledger says another message stays inside `2/5min`, `6/6h`, `8/24h`. froide
   does not enforce `message_throttle` on this path in a way we can rely on, so this
   brake is voluntary;
5. in the form itself: *"Adresse mitsenden"* is off, the recipient can be read and is
   reported, subject and message read back byte for byte after being typed, no U+2026,
   and exactly one salutation and one closing formula. Anything it cannot find, it treats
   as a failure — a form that changed shape is a form it must not press buttons in.

Afterwards it asks the API whether a new message actually exists on the request. If none
does, the outcome is reported as `unconfirmed` and the draft is **not** marked sent.
Unclear is not failure and it is not success.

> [!WARNING]
> **What you are accepting when you switch this on**
>
> 1. **Browser automation defeats the principle that a human performs the last action.**
>    Every other exit in this server ends with a person clicking send. This one does not.
>
> 2. **Next to a general-purpose file-writing tool, gates 1 and 3 are not gates.** They
>    are two values in a YAML file on your disk. No tool in *this* server can set either —
>    `build_reply_draft` always writes `status: draft` and the placeholder token. But most
>    MCP hosts also give the model a `write_file` tool, and a model that can write files
>    can write `status: approved` and a token of its own choosing. Combine that with a
>    prompt injection out of an authority's reply — text this server reads and labels as
>    untrusted, but still puts in front of the model — and post to a public authority goes
>    out with no human in the loop. It cannot be recalled.
>
> 3. **The browser carries a logged-in session of yours.** A malfunction acts with your
>    full rights on fragdenstaat.de: your requests, your account pages, your address.
>
> 4. **Countermeasures**, in order of effectiveness:
>    - leave the feature off. Unset `FDS_MCP_BROWSER_SEND` and the tool does not exist.
>    - use a **separate browser profile** with no other logins, via
>      `FDS_MCP_BROWSER_PROFILE`. The session in that profile is the blast radius.
>    - put the draft directory **out of reach of your other tools** with
>      `FDS_MCP_DRAFT_DIR`. Gates 1 and 3 are only worth something while nothing else can
>      write that file.
>    - keep `dry_run=True` in normal use. It fills the form and stops before the click.


---

## Safety model

Seven rules are enforced in code, not merely documented. Each has tests in
`tests/test_security_gates.py` that prove it bites.

1. `submit_request` aborts unless the draft's `status` is `approved` — a human sets that.
2. It aborts while any `ERROR` finding is open.
3. It aborts when `law.wunsch_id != law.api_default_id`, because the API cannot set
   `law_type` and would file under the wrong act.
4. It aborts unless `confirmation_token` matches, byte for byte, the token a human wrote
   into the draft file. A tool must not invent that token.
5. Every red tool has `dry_run: bool = True` as its default.
6. A local ledger checks `5/5min`, `6/6h`, `10/24h`, `20/7d` before any POST and **aborts
   with a clear message instead of retrying**. FragDenStaat's terms of use B.1.4 lock an
   account for a month for *attempting* to circumvent the limits.
7. The HTTP client refuses every non-GET method unless `allow_write=True` was set
   explicitly. Only one function in the package ever sets it.

`send_reply_via_browser` has its own chain of five, listed under
[Sending replies](#sending-replies), with tests in `tests/test_browser_send.py`. It also
has a gate the others do not need: rule 0, *the tool is not registered at all* unless
`FDS_MCP_BROWSER_SEND=1`.

### The rule set

Offline rules `R01`–`R19` reproduce what froide's *web form* enforces — which is
considerably more than the REST API validates. Live rules `L01`–`L06` check against the
API: the authority exists and still has that name, the desired law is actually offered,
the recomputed API default matches what the draft claims, no duplicate request exists,
no sentence of your text is already in the law's own letter template, and the finished
letter contains every element it should.

Notable ones:

- `R06` refuses e-mail addresses and IBANs in the body. Public requests are CC0 and
  visible to everyone — do not put other people in them.
- `R10` treats a salutation or a closing formula as an **error** when `full_text=false`:
  froide frames the text itself with the law's `letter_start`/`letter_end`, so writing
  either yourself sends a doubled greeting.
- `R12` is an error for `submit_via: api` and only a hint for `submit_via: web_form` —
  the web form can choose the act, the API cannot.
- `R18` and `L06` check **the letter the authority receives**, not the body you wrote.
  With `full_text=false` the act's `letter_start`/`letter_end` supply part of the text, so
  an element may come from either side; `L06` fetches the frame and reports what neither
  half supplies. Six elements: legal basis (the only `ERROR`), cost pre-notification,
  cost cap, deadline, forwarding when the body is not responsible, electronic reply.

  The cost cap is why this exists. A real request went out without one, because the
  LTranspG `letter_end` does ask to be told the expected costs but names no ceiling and no
  fallback to free inspection on the premises. Everything else was covered by the
  template, which is exactly why reading the body alone found nothing.
- `R19` is the inverse of `R10` and applies only to follow-ups — see
  [Sending replies](#sending-replies).

### Where the server is allowed to write

Three tool arguments are file paths chosen by the *model*, and the same model reads
authority replies and attachments — text written by third parties. So the paths are
constrained rather than trusted:

- draft paths must end in `.yaml`/`.yml`, are resolved before they are checked (a
  symlink is judged by its target), and `save()` refuses to overwrite a file that is not
  itself a draft;
- `download_attachment` will not create a directory, and the attachment's file name is
  stripped to its basename with everything outside `[A-Za-z0-9._ -]` replaced;
- attachments are only ever fetched from `fragdenstaat.de` and
  `media.frag-den-staat.de`, and the bearer token is never sent anywhere else.

Four environment variables tighten this further, and are recommended whenever the server
runs unattended:

| Variable | Effect |
| --- | --- |
| `FDS_MCP_DRAFT_DIR` | every draft path must stay inside this directory (`:`-separated list) |
| `FDS_MCP_DOWNLOAD_DIR` | every `download_attachment` target must stay inside this directory |
| `FDS_MCP_BROWSER_SEND` | `1` registers `send_reply_via_browser`. Anything else, including unset, and the tool does not exist |
| `FDS_MCP_BROWSER_PROFILE` | browser profile directory for that tool. Point it at a profile logged in to fragdenstaat.de **and nothing else** |

Results that carry third-party text (`get_messages`, `get_request`,
`list_attachments`, `download_attachment`) name those fields in an
`untrusted_content` key. They are data. They do not choose file paths, URLs, tool calls
or confirmation tokens.

### The draft lifecycle

```
draft ──validate_draft──▶ validated ──a human edits the file──▶ approved ──submit_request──▶ submitted
```

A reply draft has its own, ending in `sent` rather than `submitted`, and only
`send_reply_via_browser` can reach that state — and only after the API has confirmed that
a new message exists.

Only a human moves a draft to `approved`, and only by editing the YAML file.

**Know the limit of that sentence.** Gate 1 (`status: approved`) and gate 4
(`confirmation_token`) are two values in a file on your disk. No tool in *this* server
can set either of them — `create_request_draft` always writes `status: draft` and the
placeholder token, and there is no tool that promotes a draft. But most MCP hosts give
the model a general-purpose file-writing tool as well, and a model that can write files
can write `status: approved` and a token of its own choosing. Combined with a prompt
injection out of an authority's reply, that is a path to a real submission.

So, if you run this alongside a filesystem tool:

- keep `submit_request` out of the picture entirely by configuring scopes without
  `make:request` — then no token this server holds can ever POST a request;
- or set `FDS_MCP_DRAFT_DIR` to a directory your other tools do not write to;
- or leave the recommended exit in place and use `build_submit_url`, where the send
  button is in your browser and not in a tool call;
- and leave `FDS_MCP_BROWSER_SEND` unset. The same reasoning applies to
  `send_reply_via_browser`, one step more sharply: it has no scope you can withhold, only
  a browser session you own.

Gates 2, 3, 5 and 7 do not depend on the file and hold regardless: the rule set runs
against live API data, the law check compares against the recomputed API default, the
throttle ledger is separate state, and the HTTP client refuses non-GET everywhere except
in `submit_request`.

---

## Development

```bash
pip install -e ".[dev]"
python -m pytest -m "not live"     # offline suite
python -m pytest -m live           # hits fragdenstaat.de
```

Network access is blocked by default via `pytest-socket`; only tests marked `live` may
reach `fragdenstaat.de`.

The live suite is read-only GETs with exactly two exceptions, both in
`tests/test_api_contract.py`, both of which cannot create anything: the message POSTs
carry an unresolvable request URI, and the web-form POST carries an empty body. **No test
opens a browser and no test sends a message.** The tests that need a token skip, rather
than fail, when `~/.config/fds-mcp/tokens.json` is absent.

The CLI also works without an MCP client:

```bash
fds-mcp validate examples/request-draft.yaml
fds-mcp validate examples/request-draft.yaml --live
```

---

## Facts, and where they come from

Everything this server asserts about the API was verified against `fragdenstaat.de` on
2026-09-05, against `okfde/froide@bc6c2fa` and `okfde/fragdenstaat_de@88bfbba`. The
source references are in the docstrings, down to file and line. If you find a claim that
is wrong or has gone stale, that is a bug — please open an issue.

The API is documented at <https://fragdenstaat.de/api/> with an OpenAPI 3.0.3 schema at
<https://fragdenstaat.de/api/v1/schema/> and a Swagger UI at
<https://fragdenstaat.de/api/v1/schema/swagger-ui/>. (`/api/v1/docs/`, which froide's own
docs mention, returns 404.)

---

## Please use this responsibly

FragDenStaat is run by a non-profit and paid for by donations. Every request you file
costs a public authority real working time. The rate limits are 5 requests per 5 minutes
and 20 per week for a reason. This server is built to help you file *better* requests,
not more of them.

Requests filed with `public: true` — the default — publish the entire e-mail
correspondence, all approved attachments and all uploaded documents to the world under
CC0.

---

## Disclaimer

**This project is not affiliated with, endorsed by, or connected to the Open Knowledge
Foundation Deutschland e.V., FragDenStaat, or the froide project.** It is an independent
third-party client that talks to a public API. All trademarks belong to their owners.

This is not legal advice.

## License

MIT — see [LICENSE](LICENSE). Copyright 2026 Dirk Wolbeck.
