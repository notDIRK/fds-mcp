"""The MCP server: twelve tools in three safety tiers.

  green   no authentication, no side effects   — pure research
  yellow  OAuth token, read only               — your own requests, messages, files
  red     writes, hard-gated                   — drafting, validating, submitting

The red tier exists because ``POST /api/v1/request/`` on fragdenstaat.de sends the
e-mail to the authority *immediately*. There is no draft mode in the API, no preview and
no undo. So the default exit of this server is :func:`build_submit_url`, which hands a
prefilled web form to the human, and :func:`submit_request` only fires when four
independent gates all agree.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import secrets
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from . import drafts, rules
from .client import (
    AuthRequired,
    FdsClient,
    FdsError,
    TruncatedResult,
    WriteBlocked,
    make_request_url,
)
from .config import BASE_URL
from .drafts import DraftError
from .errors import FdsMcpError
from .throttle import ThrottleExceeded, ThrottleLedger

mcp = MCPServer(
    "fds-mcp",
    title="FragDenStaat (froide)",
    version="0.1.0",
    instructions=(
        "Research and preparation for freedom-of-information requests on "
        "fragdenstaat.de. Green tools are free to call. Red tools touch a real, "
        "irreversible submission channel: POST /api/v1/request/ sends the e-mail to the "
        "authority at once. Never call submit_request with dry_run=False unless the user "
        "explicitly asked for it in this turn and supplied the confirmation token "
        "themselves."
    ),
)


class SubmitBlocked(FdsMcpError):
    """A submission gate refused. The message names the gate."""


# --------------------------------------------------------------------------
# client factories — separate so that tests can substitute them
# --------------------------------------------------------------------------

def read_client() -> FdsClient:
    """Anonymous, read-only client (green tier)."""
    return FdsClient()


def token_client() -> FdsClient:
    """Token-backed, still read-only client (yellow tier)."""
    if os.environ.get("FDS_TOKEN"):
        return FdsClient.from_env()
    return FdsClient.authenticated()


def write_client() -> FdsClient:
    """The only place in this package that unlocks non-GET methods (red tier)."""
    if os.environ.get("FDS_TOKEN"):
        return FdsClient.from_env(allow_write=True)
    return FdsClient.authenticated(allow_write=True)


def _findings(items: list[rules.Finding]) -> list[dict[str, str]]:
    return [f.as_dict() for f in items]


# ==========================================================================
# GREEN — no authentication, no side effects
# ==========================================================================

@mcp.tool()
def search_authorities(query: str, jurisdiction: str | None = None,
                       limit: int = 20) -> dict:
    """Search public bodies on fragdenstaat.de. GREEN: no auth, no side effects.

    Args:
        query: free-text search, e.g. a town or an authority name.
        jurisdiction: optional filter — numeric id, slug ("rheinland-pfalz") or
            name ("Rheinland-Pfalz"). The API itself only accepts the id.
        limit: maximum number of results (server page size is 50).
    """
    with read_client() as client:
        filters: dict[str, Any] = {"limit": max(1, min(int(limit), 50))}
        resolved = client.resolve_jurisdiction(jurisdiction)
        if resolved is not None:
            filters["jurisdiction"] = resolved
        found = client.search_authorities(query, **filters)
        results = [
            {
                "id": pb["id"],
                "name": pb["name"],
                "email": pb.get("email"),
                "url": pb.get("site_url") or pb.get("url"),
                "jurisdiction": (pb.get("jurisdiction") or {}).get("name"),
                "classification": (pb.get("classification") or {}).get("name"),
                "number_of_requests": pb.get("number_of_requests"),
            }
            for pb in found[: int(limit)]
        ]
    return {
        "query": query,
        "jurisdiction_filter": jurisdiction,
        "count": len(results),
        "results": results,
        "source": f"{BASE_URL}/api/v1/publicbody/search/?q={query}",
    }


@mcp.tool()
def get_authority(id: int) -> dict:
    """Full record for one public body, including its laws. GREEN.

    The API does not expose ``default_law``, so the law the REST API would apply is
    recomputed here the way froide's ``get_applicable_law()`` does it
    (``order_by('-meta', '-priority')``). That default is almost always the *meta* law,
    which is why the API cannot be used to file under a specific act.

    Args:
        id: numeric public body id.
    """
    with read_client() as client:
        pb = client.get_authority(int(id))
    laws = [entry for entry in pb.get("laws", []) if isinstance(entry, dict)]
    default = rules.applicable_law(laws) if laws else None
    return {
        "id": pb["id"],
        "name": pb["name"],
        "email": pb.get("email"),
        "address": pb.get("address"),
        "url": pb.get("site_url") or pb.get("url"),
        "jurisdiction": (pb.get("jurisdiction") or {}).get("name"),
        "classification": (pb.get("classification") or {}).get("name"),
        "regions": pb.get("regions", []),
        "request_note": pb.get("request_note"),
        "laws": [
            {
                "id": law["id"],
                "name": law.get("name"),
                "law_type": law.get("law_type"),
                "meta": law.get("meta"),
                "priority": law.get("priority"),
                "max_response_time": law.get("max_response_time"),
                "max_response_time_unit": law.get("max_response_time_unit"),
                "email_only": law.get("email_only"),
            }
            for law in laws
        ],
        "api_default_law_id": default["id"] if default else None,
        "api_default_law_name": default.get("name") if default else None,
        "note": (
            "api_default_law_* is what POST /api/v1/request/ would use. To file under a "
            "different act, use build_submit_url() with the desired law_type."
        ),
        "source": f"{BASE_URL}/api/v1/publicbody/{pb['id']}/",
    }


@mcp.tool()
def get_law(id: int) -> dict:
    """One freedom-of-information act with its deadline rules. GREEN.

    Args:
        id: numeric law id, e.g. from ``get_authority(...)["laws"]``.
    """
    with read_client() as client:
        law = client.get_law(int(id))
    return {
        "id": law["id"],
        "name": law.get("name"),
        "law_type": law.get("law_type"),
        "meta": law.get("meta"),
        "combined": law.get("combined"),
        "priority": law.get("priority"),
        "max_response_time": law.get("max_response_time"),
        "max_response_time_unit": law.get("max_response_time_unit"),
        "email_only": law.get("email_only"),
        "requires_signature": law.get("requires_signature"),
        "mediator": law.get("mediator"),
        "request_note": law.get("request_note"),
        "description": law.get("description"),
        "url": law.get("site_url") or law.get("url"),
        "source": f"{BASE_URL}/api/v1/law/{law['id']}/",
    }


@mcp.tool()
def check_jurisdiction(place_name: str, include_wider: bool = False) -> dict:
    """Which authority covers a given place? GREEN — returns the evidence trail.

    Resolves ``/georegion/?name=<place>``, walks its ``part_of`` chain upwards, and asks
    ``/publicbody/?regions=<id>`` level by level, from the most specific outwards. It
    stops at the **first level that yields any authority**, because that is the body
    actually responsible; the remaining, wider levels are reported by id only.

    This matters in states such as Rhineland-Palatinate, where an *Ortsgemeinde* is often
    not listed on fragdenstaat.de at all while the *Verbandsgemeindeverwaltung* that runs
    its administration is.

    Args:
        place_name: name of the municipality, district or state.
        include_wider: also list the authorities of the wider levels. Off by default —
            at country level that is thousands of bodies and tells you nothing.
    """
    evidence: list[str] = [f"{BASE_URL}/api/v1/georegion/?name={place_name}"]
    with read_client() as client:
        regions = client.find_georegions(place_name)
        if not regions:
            return {
                "place": place_name,
                "found": False,
                "message": (f"No GeoRegion named {place_name!r}. Try search_authorities() "
                            "with the name instead."),
                "matches": [],
                "evidence": evidence,
            }

        matches: list[dict] = []
        for region in regions:
            chain = _region_chain(client, region, evidence)
            covering: list[dict] = []
            matched_level: dict | None = None
            wider: list[dict] = []

            for step in chain:
                if matched_level is not None and not include_wider:
                    wider.append(step)
                    continue
                bodies = client.authorities_for_region(step["id"])
                evidence.append(f"{BASE_URL}/api/v1/publicbody/?regions={step['id']}")
                if not bodies:
                    continue
                if matched_level is None:
                    matched_level = step
                covering.extend(_authority_at(pb, step) for pb in bodies)

            matches.append({
                "region": chain[0],
                "region_chain": chain,
                "matched_at": matched_level,
                "covering_authorities": covering,
                "wider_levels_not_queried": wider,
            })

    return {
        "place": place_name,
        "found": True,
        "matches": matches,
        "evidence": evidence,
        "note": (
            "An authority appears here because its own 'regions' field contains the region "
            "under 'matched_at' — not because of an assumption. Levels wider than the "
            "match were not queried; pass include_wider=true to see them, but expect "
            "hundreds of results at district level and thousands at country level."
        ),
    }


def _region_chain(client: FdsClient, region: dict, evidence: list[str]) -> list[dict]:
    """The region and its ancestors. Guards against cycles: GeoRegion 1 is its own parent."""
    chain: list[dict] = []
    seen: set[int] = set()
    current: dict | None = region
    while current is not None and int(current["id"]) not in seen:
        seen.add(int(current["id"]))
        chain.append({
            "id": current["id"],
            "name": current["name"],
            "kind": current.get("kind"),
            "kind_detail": current.get("kind_detail"),
            "level": current.get("level"),
        })
        parent_id = _id_from_uri(current.get("part_of") or "")
        if parent_id is None or parent_id in seen:
            break
        evidence.append(f"{BASE_URL}/api/v1/georegion/{parent_id}/")
        current = client.get_georegion(parent_id)
    return chain


def _authority_at(pb: dict, step: dict) -> dict:
    return {
        "id": pb["id"],
        "name": pb["name"],
        "email": pb.get("email"),
        "classification": (pb.get("classification") or {}).get("name"),
        "jurisdiction": (pb.get("jurisdiction") or {}).get("name"),
        "matched_region_id": step["id"],
        "matched_region_name": step["name"],
        "matched_region_kind": step.get("kind_detail"),
    }


# ==========================================================================
# YELLOW — OAuth token, read only
# ==========================================================================

@mcp.tool()
def list_my_requests(status: str | None = None, limit: int = 50) -> dict:
    """Your own FOI requests. YELLOW: needs a token (scope read:request), read only.

    Args:
        status: optional filter, e.g. "awaiting_response" or "resolved".
        limit: maximum number of requests to return.
    """
    with token_client() as client:
        me = client.whoami()
        filters: dict[str, Any] = {"user": me["id"], "limit": max(1, int(limit))}
        if status:
            filters["status"] = status
        found = client.list_requests(**filters)
    return {
        "user_id": me["id"],
        "status_filter": status,
        "count": len(found),
        "requests": [_request_summary(r) for r in found],
    }


@mcp.tool()
def get_request(id: int) -> dict:
    """One FOI request in detail. YELLOW (public requests work without a token).

    Args:
        id: numeric request id.
    """
    with token_client() as client:
        req = client.get_request(int(id))
    summary = _request_summary(req)
    summary.update({
        "description": req.get("description"),
        "summary": req.get("summary"),
        "refusal_reason": req.get("refusal_reason"),
        "costs": req.get("costs"),
        "tags": req.get("tags"),
        "law": _law_ref(req.get("law")),
        "source": f"{BASE_URL}/api/v1/request/{req['id']}/",
    })
    return summary


@mcp.tool()
def get_messages(request_id: int) -> dict:
    """All messages of a request, oldest first. YELLOW.

    Args:
        request_id: numeric request id.
    """
    with token_client() as client:
        messages = client.get_messages(int(request_id))
    return {
        "request_id": int(request_id),
        "count": len(messages),
        "messages": [
            {
                "id": m["id"],
                "timestamp": m.get("timestamp"),
                "kind": m.get("kind"),
                "is_response": m.get("is_response"),
                "sender": m.get("sender"),
                "subject": m.get("redacted_subject") or m.get("subject"),
                "content": m.get("redacted_content") or m.get("content"),
                "status_name": m.get("status_name"),
                "attachment_count": len(m.get("attachments") or []),
            }
            for m in messages
        ],
        "note": (
            "Replying to an authority is NOT possible through the API. "
            "POST /api/v1/message/ only creates postal messages. Use "
            f"{BASE_URL}/a/<slug>/send/message/ in the browser."
        ),
    }


@mcp.tool()
def list_attachments(message_id: int) -> dict:
    """Attachments belonging to one message. YELLOW.

    Args:
        message_id: numeric message id (from get_messages).
    """
    with token_client() as client:
        found = client.get_attachments(int(message_id))
    return {
        "message_id": int(message_id),
        "count": len(found),
        "attachments": [
            {
                "id": a["id"],
                "name": a.get("name"),
                "filetype": a.get("filetype"),
                "size": a.get("size"),
                "approved": a.get("approved"),
                "is_redacted": a.get("is_redacted"),
                "pending": a.get("pending"),
                "site_url": a.get("site_url"),
            }
            for a in found
        ],
    }


@mcp.tool()
def download_attachment(attachment_id: int, target_dir: str) -> dict:
    """Download one attachment into a local directory. YELLOW: reading only.

    Args:
        attachment_id: numeric attachment id (from list_attachments).
        target_dir: existing or creatable local directory.
    """
    directory = Path(target_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with token_client() as client:
        att = client.get_attachment(int(attachment_id))
        url = att.get("file_url")
        if not url:
            raise FdsError(
                f"Attachment {attachment_id} has no file_url — it may still be pending "
                "conversion or may not be approved for download."
            )
        name = _safe_filename(att.get("name") or f"attachment-{attachment_id}")
        target = directory / name
        written = client.download(url, target)
    return {
        "attachment_id": int(attachment_id),
        "name": name,
        "path": str(target),
        "bytes": written,
        "filetype": att.get("filetype"),
        "approved": att.get("approved"),
    }


@mcp.tool()
def check_deadlines() -> dict:
    """Your open requests whose statutory deadline has passed. YELLOW.

    froide exposes no "deadline expired" flag; it is computed from ``due_date`` against
    the current time, exactly as the frontend does.
    """
    now = dt.datetime.now(dt.timezone.utc)
    with token_client() as client:
        me = client.whoami()
        found = client.list_requests(user=me["id"], limit=500)

    overdue, pending, undated = [], [], []
    for req in found:
        if req.get("status") == "resolved" or req.get("resolution"):
            continue
        due_raw = req.get("due_date")
        due = _parse_dt(due_raw)
        entry = _request_summary(req)
        if due is None:
            undated.append(entry)
            continue
        entry["days_remaining"] = (due - now).days
        (overdue if due < now else pending).append(entry)

    overdue.sort(key=lambda e: e.get("days_remaining", 0))
    pending.sort(key=lambda e: e.get("days_remaining", 0))
    return {
        "checked_at": now.isoformat(),
        "user_id": me["id"],
        "overdue_count": len(overdue),
        "overdue": overdue,
        "pending": pending,
        "without_due_date": undated,
        "note": (
            "After the deadline the next step is an objection or an appeal to the state "
            "information commissioner. Both are only possible in the web interface."
        ),
    }


# ==========================================================================
# RED — writes, hard-gated. dry_run defaults to True everywhere.
# ==========================================================================

@mcp.tool()
def create_request_draft(
    path: str,
    subject: str,
    body: str,
    publicbody_id: int,
    law_wunsch_id: int,
    law_api_default_id: int | None = None,
    law_wunsch_law_type: str | None = None,
    publicbody_name: str | None = None,
    publicbody_email: str | None = None,
    ermittelt_ueber: str | None = None,
    public: bool = True,
    full_text: bool = False,
    submit_via: str = "web_form",
    dry_run: bool = True,
) -> dict:
    """Write a local YAML draft. RED tier, but performs NO network traffic at all.

    The file starts in status ``draft`` with a placeholder confirmation token. A human
    has to read the text, set ``status: approved`` and replace ``confirmation_token``
    before anything can be submitted.

    Args:
        path: where to write the YAML file.
        subject: request subject, 8-230 characters.
        body: the request text, at most 5000 characters.
        publicbody_id: recipient authority id.
        law_wunsch_id: the law you want to file under.
        law_api_default_id: the law the REST API would apply — see get_authority().
        law_wunsch_law_type: law_type of the desired law, e.g. "IFG" or "UIG".
        publicbody_name: authority name, for the L01 cross-check.
        publicbody_email: authority e-mail, for the L01 cross-check.
        ermittelt_ueber: how responsibility was established (a URL or a sentence).
        public: whether the request will be public. Public means CC0 and visible to all.
        full_text: True sends your text verbatim, False lets froide frame it.
        submit_via: "web_form" (human presses send) or "api" (immediate dispatch).
        dry_run: True (default) returns the draft without writing the file.
    """
    draft = drafts.new_draft(
        subject=subject, body=body, publicbody_id=int(publicbody_id),
        law_wunsch_id=int(law_wunsch_id), law_api_default_id=law_api_default_id,
        law_wunsch_law_type=law_wunsch_law_type, publicbody_name=publicbody_name,
        publicbody_email=publicbody_email, ermittelt_ueber=ermittelt_ueber,
        public=public, full_text=full_text, submit_via=submit_via,
    )
    findings = rules.run_offline(draft)
    result: dict[str, Any] = {
        "dry_run": bool(dry_run),
        "path": str(Path(path).expanduser()),
        "written": False,
        "status": draft["status"],
        "findings": _findings(findings),
        "error_count": len(rules.errors(findings)),
        "suggested_confirmation_token": drafts.suggest_confirmation_token(),
        "next_steps": [
            "Read the body yourself.",
            "Run validate_draft(path) — with dry_run=False for the live rules.",
            "Set status: approved and a confirmation_token in the file, by hand.",
            "Prefer build_submit_url(path); submit_request() sends irreversibly.",
        ],
    }
    if dry_run:
        result["draft_yaml"] = drafts.dump(draft)
        return result
    written = drafts.save(draft, path)
    result["path"] = str(written)
    result["written"] = True
    return result


@mcp.tool()
def validate_draft(path: str, dry_run: bool = True) -> dict:
    """Run the rule set against a draft. RED tier.

    Offline rules R01-R16 always run. The live rules L01-L04 (authority exists, law is
    offered, API default law, duplicate check) need the network and therefore only run
    with ``dry_run=False``.

    Args:
        path: path to the draft YAML file.
        dry_run: True (default) runs offline rules only and does not touch the file.
    """
    draft = drafts.load(path)
    findings = rules.run_offline(draft)
    live_ran = False
    if not dry_run:
        with read_client() as client:
            findings += rules.run_live(draft, client)
        live_ran = True

    errs = rules.errors(findings)
    result = {
        "path": str(Path(path).expanduser()),
        "dry_run": bool(dry_run),
        "live_rules_ran": live_ran,
        "status": draft.get("status"),
        "findings": _findings(findings),
        "error_count": len(errs),
        "warning_count": len([f for f in findings if f.level is rules.Level.WARN]),
        "info_count": len([f for f in findings if f.level is rules.Level.INFO]),
        "passes": not errs,
    }
    if not dry_run:
        draft["findings"] = result["findings"]
        if not errs and draft.get("status") == "draft":
            drafts.set_status(draft, "validated")
            result["status"] = "validated"
        drafts.save(draft, path)
        result["file_updated"] = True
    return result


@mcp.tool()
def build_submit_url(path: str, dry_run: bool = True) -> dict:
    """Build the prefilled web form URL — the recommended way out. RED tier.

    This is the only route on which the legal basis can actually be chosen
    (``?law_type=...``); the REST API's MakeRequestSerializer has no such field. Sending
    stays with the human.

    fragdenstaat.de answers GET URLs above roughly 4096 bytes with HTTP 400 (measured
    2026-09-05). Longer drafts therefore get a two-step answer: a short URL that
    prefills subject and law_type, plus the body in a sidecar text file.

    Args:
        path: path to the draft YAML file.
        dry_run: True (default) does not write the sidecar .body.txt file.
    """
    draft = drafts.load(path)
    findings = rules.run_offline(draft)
    errs = rules.errors(findings)
    if errs:
        return {
            "path": str(Path(path).expanduser()),
            "ok": False,
            "reason": "Draft has ERROR findings; fix them before building a URL.",
            "findings": _findings(errs),
        }

    law = draft.get("law") or {}
    pb_id = int((draft.get("publicbody") or {})["id"])
    subject = str(draft.get("subject", "")).strip()
    body = draft.get("body") or ""
    law_type = law.get("wunsch_law_type")
    public = bool(draft.get("public", True))

    full_url = make_request_url(pb_id, subject, body, law_type=law_type, public=public)
    out: dict[str, Any] = {
        "path": str(Path(path).expanduser()),
        "ok": True,
        "dry_run": bool(dry_run),
        "two_step": False,
        "url": full_url,
        "url_bytes": len(full_url),
        "max_url_bytes": rules.MAX_PREFILL_URL_LENGTH,
        "findings": _findings(findings),
    }
    if not drafts.prefill_too_long(full_url):
        return out

    short_url = make_request_url(pb_id, subject, "", law_type=law_type, public=public)
    sidecar = drafts.body_file(path)
    out.update({
        "two_step": True,
        "url": short_url,
        "url_bytes": len(short_url),
        "full_url_bytes": len(full_url),
        "body_file": str(sidecar),
        "body_file_written": False,
        "instructions": [
            f"1. Open the form: {short_url}",
            f"2. Paste the request text from {sidecar} into the body field.",
            "3. Check everything, then press send yourself.",
        ],
    })
    if not dry_run:
        sidecar.write_text(body, encoding="utf-8")
        out["body_file_written"] = True
    return out


@mcp.tool()
def submit_request(path: str, confirmation_token: str, dry_run: bool = True) -> dict:
    """Actually POST the request to fragdenstaat.de. RED tier — IRREVERSIBLE.

    ``POST /api/v1/request/`` sends the e-mail to the authority immediately. There is no
    draft, no preview and no undo. Five gates therefore have to agree, in this order:

      1. the draft's status is ``approved`` (a human set it),
      2. no ERROR finding is open,
      3. ``law.wunsch_id == law.api_default_id`` — the API cannot set law_type, so a
         mismatch would file the request under the wrong act,
      4. ``confirmation_token`` matches the value a human wrote into the draft file,
      5. the local throttle ledger says another submission stays inside
         5/5min, 6/6h, 10/24h, 20/7d.

    Args:
        path: path to the approved draft YAML file.
        confirmation_token: must equal the ``confirmation_token`` inside the draft file.
        dry_run: True (default) runs every gate and reports, but sends nothing.
    """
    draft = drafts.load(path)
    target = Path(path).expanduser().resolve()

    # --- gate 1: human approval in the file --------------------------------
    status = draft.get("status")
    if status == "submitted":
        raise SubmitBlocked(
            f"Gate 1 (status): {target} is already marked 'submitted'. Refusing to send "
            "the same request twice."
        )
    if status != "approved":
        raise SubmitBlocked(
            f"Gate 1 (status): draft status is {status!r}, must be 'approved'. A human "
            "has to read the text and set 'status: approved' in the file."
        )

    # --- gate 2: no open ERROR finding -------------------------------------
    findings = rules.run_offline(draft)
    if not dry_run:
        with read_client() as client:
            findings += rules.run_live(draft, client)
    errs = rules.errors(findings)
    if errs:
        raise SubmitBlocked(
            "Gate 2 (rules): "
            + f"{len(errs)} ERROR finding(s) open:\n  - "
            + "\n  - ".join(str(f) for f in errs)
        )

    # --- gate 3: the API cannot choose the legal basis ---------------------
    law = draft.get("law") or {}
    wunsch, api_default = law.get("wunsch_id"), law.get("api_default_id")
    if wunsch is None or api_default is None:
        raise SubmitBlocked(
            "Gate 3 (law): law.wunsch_id and law.api_default_id must both be set. "
            "get_authority() reports api_default_law_id."
        )
    if wunsch != api_default:
        raise SubmitBlocked(
            f"Gate 3 (law): desired law {wunsch} != API default {api_default}. "
            "MakeRequestSerializer has no law_type field, so the API would file this "
            "request under the wrong act. Use build_submit_url() instead."
        )

    # --- gate 4: the human-set confirmation token --------------------------
    stored = drafts.confirmation_token(draft)
    if not stored:
        raise SubmitBlocked(
            "Gate 4 (confirmation): the draft has no confirmation_token. A human must "
            "write one into the file; a tool must not invent it."
        )
    if not secrets.compare_digest(stored, str(confirmation_token or "")):
        raise SubmitBlocked(
            "Gate 4 (confirmation): the supplied confirmation_token does not match the "
            "one stored in the draft file."
        )

    # --- gate 5: local rate-limit bookkeeping ------------------------------
    ledger = ThrottleLedger()
    ledger.check()

    plan = {
        "publicbodies": [int((draft.get("publicbody") or {})["id"])],
        "subject": str(draft.get("subject", "")).strip(),
        "body": draft.get("body") or "",
        "public": bool(draft.get("public", True)),
        "full_text": bool(draft.get("full_text", False)),
        "tags": draft.get("tags") or [],
        "reference": draft.get("reference") or "",
        "law_id_that_will_be_applied": api_default,
    }
    if dry_run:
        return {
            "path": str(target),
            "dry_run": True,
            "submitted": False,
            "gates_passed": ["status", "rules", "law", "confirmation", "throttle"],
            "would_post_to": f"{BASE_URL}/api/v1/request/",
            "payload": plan,
            "findings": _findings(findings),
            "throttle": ledger.status(),
            "warning": (
                "Calling this again with dry_run=False sends the e-mail to the authority "
                "immediately and irreversibly."
            ),
        }

    with write_client() as client:
        response = client.create_request(
            publicbody_ids=plan["publicbodies"],
            subject=plan["subject"],
            body=plan["body"],
            public=plan["public"],
            full_text=plan["full_text"],
            tags=plan["tags"] or None,
            reference=plan["reference"],
        )
    ledger.record()

    draft["submitted"] = {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "url": (response or {}).get("url"),
        "law_id_applied": api_default,
    }
    drafts.set_status(draft, "submitted")
    drafts.save(draft, target)

    return {
        "path": str(target),
        "dry_run": False,
        "submitted": True,
        "response": response,
        "url": (response or {}).get("url"),
        "throttle": ledger.status(),
    }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _request_summary(req: dict) -> dict:
    return {
        "id": req.get("id"),
        "title": req.get("title"),
        "url": req.get("site_url") or req.get("url"),
        "status": req.get("status"),
        "readable_status": req.get("readable_status"),
        "resolution": req.get("resolution"),
        "due_date": req.get("due_date"),
        "created_at": req.get("created_at"),
        "last_message": req.get("last_message"),
        "public": req.get("public"),
        "public_body": _name_of(req.get("public_body")),
        "jurisdiction": req.get("jurisdiction_name"),
    }


def _name_of(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("name")
    return value


def _law_ref(value: Any) -> Any:
    if isinstance(value, dict):
        return {"id": value.get("id"), "name": value.get("name"),
                "law_type": value.get("law_type")}
    return value


def _id_from_uri(uri: str) -> int | None:
    match = re.search(r"/(\d+)/?$", str(uri))
    return int(match.group(1)) if match else None


def _parse_dt(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]")


def _safe_filename(name: str) -> str:
    """Strip any directory component; attachment names come from the server."""
    base = os.path.basename(str(name).replace("\\", "/")).strip()
    base = _UNSAFE.sub("_", base).lstrip(".")
    return base or "attachment"


def run() -> None:
    """Entry point: serve over stdio."""
    mcp.run("stdio")


__all__ = [
    "mcp", "run", "SubmitBlocked",
    "search_authorities", "get_authority", "get_law", "check_jurisdiction",
    "list_my_requests", "get_request", "get_messages", "list_attachments",
    "download_attachment", "check_deadlines",
    "create_request_draft", "validate_draft", "build_submit_url", "submit_request",
    "AuthRequired", "FdsError", "TruncatedResult", "WriteBlocked", "DraftError",
    "ThrottleExceeded",
]
