"""Local draft store — YAML files, never any network traffic.

froide has a ``RequestDraft`` model but it is not registered in the API router, so
drafts only exist in the web frontend (``/anfrage-stellen/draft/<pk>/``). This module is
our stand-in: a draft is a plain YAML file on the user's disk that a human can read,
edit and diff before anything is submitted.

State machine: ``draft`` -> ``validated`` -> ``approved`` -> ``submitted``.
Only ``approved`` opens the door to :func:`fds_mcp.server.submit_request`, and only
together with the confirmation token that a human typed into the file.
"""

from __future__ import annotations

import datetime as dt
import secrets
from pathlib import Path
from typing import Any

import yaml

from .errors import FdsMcpError
from .rules import DRAFT_STATES, MAX_PREFILL_URL_LENGTH, SUBMIT_METHODS, slugify_like_django

CONFIRMATION_PLACEHOLDER = "REPLACE-ME-BY-HAND"

_HEADER = """\
# FragDenStaat request draft — created by fds-mcp. NOTHING HAS BEEN SENT.
#
# State machine: draft -> validated -> approved -> submitted
#   draft      the file exists, nothing checked
#   validated  validate_draft() ran without ERROR findings
#   approved   a HUMAN read the text and set this by hand
#   submitted  set by submit_request() after a successful POST
#
# submit_request() refuses to run unless
#   * status is "approved",
#   * no ERROR finding is open,
#   * law.wunsch_id == law.api_default_id (the API cannot choose the legal basis), and
#   * the confirmation_token below matches the one passed to the tool.
# Replace the confirmation_token yourself. A tool must not invent it for you.
"""


class DraftError(FdsMcpError):
    """Malformed draft file, forbidden path, or an illegal state transition."""


def new_draft(
    *,
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
    tags: list[str] | None = None,
    reference: str = "",
) -> dict[str, Any]:
    """Build the draft dictionary. Pure data — no I/O, no network."""
    if submit_via not in SUBMIT_METHODS:
        raise DraftError(f"submit_via must be one of {SUBMIT_METHODS}, got {submit_via!r}")
    draft: dict[str, Any] = {
        "status": "draft",
        "meta": {
            "slug": slugify_like_django(subject)[:60] or "request",
            "created": dt.date.today().isoformat(),
            "generator": "fds-mcp",
        },
        "publicbody": {
            "id": int(publicbody_id),
            "name": publicbody_name,
            "email": publicbody_email,
            "ermittelt_ueber": ermittelt_ueber,
        },
        "law": {
            "wunsch_id": int(law_wunsch_id),
            "wunsch_law_type": law_wunsch_law_type,
            "api_default_id": (int(law_api_default_id)
                               if law_api_default_id is not None else None),
        },
        "submit_via": submit_via,
        "public": bool(public),
        "full_text": bool(full_text),
        "subject": subject.strip(),
        "body": body,
        "confirmation_token": CONFIRMATION_PLACEHOLDER,
        "findings": [],
    }
    if tags:
        draft["tags"] = list(tags)
    if reference:
        draft["reference"] = reference
    return draft


def dump(draft: dict[str, Any]) -> str:
    return _HEADER + yaml.safe_dump(draft, allow_unicode=True, sort_keys=False,
                                    default_flow_style=False, width=100)


def save(draft: dict[str, Any], path: str | Path) -> Path:
    target = _safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump(draft), encoding="utf-8")
    return target


def load(path: str | Path) -> dict[str, Any]:
    target = _safe_path(path)
    if not target.exists():
        raise DraftError(f"Draft file not found: {target}")
    with open(target, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise DraftError(f"{target} does not contain a YAML mapping.")
    return data


def set_status(draft: dict[str, Any], status: str) -> dict[str, Any]:
    if status not in DRAFT_STATES:
        raise DraftError(f"Unknown status {status!r}; allowed: {DRAFT_STATES}")
    draft["status"] = status
    return draft


def confirmation_token(draft: dict[str, Any]) -> str:
    """The token a human wrote into the file. Empty/placeholder counts as unset."""
    token = str(draft.get("confirmation_token") or "").strip()
    if not token or token == CONFIRMATION_PLACEHOLDER:
        return ""
    return token


def suggest_confirmation_token() -> str:
    """A suggestion the human may copy into the file — never written automatically."""
    return f"SEND-{secrets.token_hex(4).upper()}"


def body_file(path: str | Path) -> Path:
    """Sidecar file holding the request text, for the two-step prefill workflow."""
    return _safe_path(path).with_suffix(".body.txt")


def prefill_too_long(url: str) -> bool:
    return len(url) > MAX_PREFILL_URL_LENGTH


def _safe_path(path: str | Path) -> Path:
    p = Path(path).expanduser()
    if "\x00" in str(p):
        raise DraftError("Draft path contains a null byte.")
    return p.resolve()
