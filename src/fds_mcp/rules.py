"""Rule set for FragDenStaat requests — the gate that runs before anything is sent.

The rules reproduce what froide's web form (``RequestForm``) enforces, plus a few
diligence rules of our own. This matters because the REST API validates
*considerably less* than the web form: ``MakeRequestSerializer`` knows neither
``min_length``, nor ``MAX_BODY_LENGTH``, nor ``validate_no_placeholder``. We hold
ourselves to the stricter variant so that a request created through the API stays
editable in the frontend.

Sources (froide @ bc6c2fa, 2026-09-03; fragdenstaat_de @ 88bfbba, 2026-09-04):
  froide/foirequest/forms/request.py      MAX_BODY_LENGTH = 5000, min_length 8, max 230
  froide/foirequest/validators.py         PLACEHOLDER_MARKER = "…", clean_reference
  froide/foirequest/serializers.py:238    MakeRequestSerializer (API)
  froide/publicbody/models/foilaw.py:25   get_applicable_law: order_by("-meta", "-priority")
  fragdenstaat_de/settings/base.py:734    request_throttle / message_throttle

Rule ids: R01-R17 run offline, L01-L05 need the network.
"""

from __future__ import annotations

import re
import unicodedata
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

# --- constants, taken 1:1 from froide -------------------------------------
MAX_SUBJECT_LENGTH = 230
MIN_SUBJECT_LENGTH = 8
MIN_BODY_LENGTH = 8
MAX_BODY_LENGTH = 5000            # applies to accounts that are not "trusted"
MIN_SUBJECT_SLUG_LENGTH = 4
PLACEHOLDER_MARKER = "…"          # horizontal ellipsis (U+2026)
MAX_TAG_LENGTH = 100              # foirequest tags are truncated to 100 chars
POSTAL_CONTENT_TYPES = {"application/pdf", "image/jpeg", "image/png"}

# Measured live on 2026-09-05: fragdenstaat.de (nginx) answers GET URLs from about
# 4096 bytes on with HTTP 400 (4086 bytes -> 200, 4106 bytes -> 400). Above that the
# prefilled form URL no longer carries the request text.
MAX_PREFILL_URL_LENGTH = 4096

# fragdenstaat.de request_throttle / message_throttle: (count, seconds)
REQUEST_THROTTLE = [(5, 5 * 60), (6, 6 * 3600), (10, 24 * 3600), (20, 7 * 24 * 3600)]
MESSAGE_THROTTLE = [(2, 5 * 60), (6, 6 * 3600), (8, 24 * 3600)]

DRAFT_STATES = ("draft", "validated", "approved", "submitted")
SUBMIT_METHODS = ("web_form", "api")


class Level(str, Enum):
    ERROR = "ERROR"
    WARN = "WARN"
    INFO = "INFO"


@dataclass(frozen=True)
class Finding:
    rule: str
    level: Level
    message: str

    def __str__(self) -> str:
        return f"[{self.level.value}] {self.rule}: {self.message}"

    def as_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "level": self.level.value, "message": self.message}


def slugify_like_django(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value)


# --- rule registry --------------------------------------------------------
OFFLINE_RULES: list[tuple[str, Callable[[dict], list[Finding]]]] = []
LIVE_RULES: list[tuple[str, Callable[[dict, Any], list[Finding]]]] = []


def offline_rule(rule_id: str):
    def deco(fn):
        OFFLINE_RULES.append((rule_id, fn))
        return fn
    return deco


def live_rule(rule_id: str):
    def deco(fn):
        LIVE_RULES.append((rule_id, fn))
        return fn
    return deco


def _body(req: dict) -> str:
    return req.get("body") or ""


def _subject(req: dict) -> str:
    return (req.get("subject") or "").strip()


# --- offline rules --------------------------------------------------------

@offline_rule("R01-subject-length")
def rule_subject_length(req: dict) -> list[Finding]:
    s = _subject(req)
    if len(s) < MIN_SUBJECT_LENGTH:
        return [Finding("R01-subject-length", Level.ERROR,
                        f"Subject has {len(s)} characters, at least {MIN_SUBJECT_LENGTH} "
                        "are required.")]
    if len(s) > MAX_SUBJECT_LENGTH:
        return [Finding("R01-subject-length", Level.ERROR,
                        f"Subject has {len(s)} characters, at most {MAX_SUBJECT_LENGTH} "
                        "are allowed.")]
    return []


@offline_rule("R02-subject-slug")
def rule_subject_slug(req: dict) -> list[Finding]:
    slug = slugify_like_django(_subject(req))
    if len(slug) < MIN_SUBJECT_SLUG_LENGTH:
        return [Finding("R02-subject-slug", Level.ERROR,
                        f"Subject slugifies to {slug!r}; froide's clean_subject requires at "
                        f"least {MIN_SUBJECT_SLUG_LENGTH} characters.")]
    return []


@offline_rule("R03-body-length")
def rule_body_length(req: dict) -> list[Finding]:
    b = _body(req)
    out = []
    if len(b) < MIN_BODY_LENGTH:
        out.append(Finding("R03-body-length", Level.ERROR,
                           f"Body has {len(b)} characters, at least {MIN_BODY_LENGTH} "
                           "are required."))
    if len(b) > MAX_BODY_LENGTH:
        out.append(Finding("R03-body-length", Level.ERROR,
                           f"Body has {len(b)} characters; the web form cuts off at "
                           f"{MAX_BODY_LENGTH} (only 'trusted' accounts may exceed it)."))
    elif len(b) > MAX_BODY_LENGTH * 0.9:
        out.append(Finding("R03-body-length", Level.WARN,
                           f"Body has {len(b)} characters and is close to the "
                           f"{MAX_BODY_LENGTH} limit."))
    return out


@offline_rule("R04-placeholder")
def rule_placeholder(req: dict) -> list[Finding]:
    out = []
    for field, value in (("Subject", _subject(req)), ("Body", _body(req))):
        if PLACEHOLDER_MARKER in value:
            out.append(Finding("R04-placeholder", Level.ERROR,
                               f"{field} contains the placeholder {PLACEHOLDER_MARKER!r}; "
                               "froide's validate_no_placeholder rejects it."))
    return out


@offline_rule("R05-publicbody-set")
def rule_publicbody(req: dict) -> list[Finding]:
    pb = req.get("publicbody") or {}
    pb_id = pb.get("id")
    # bool is a subclass of int, and YAML happily produces one from "yes"/"true".
    # int(True) is 1, so without this the request would go to public body 1.
    if not isinstance(pb_id, int) or isinstance(pb_id, bool):
        return [Finding("R05-publicbody-set", Level.ERROR,
                        f"publicbody.id must be a number, got {pb_id!r}.")]
    if not pb.get("ermittelt_ueber"):
        return [Finding("R05-publicbody-set", Level.WARN,
                        "publicbody.ermittelt_ueber is missing — responsibility is not "
                        "evidenced.")]
    return []


@offline_rule("R06-pii")
def rule_pii(req: dict) -> list[Finding]:
    """Requests default to public and are released under CC0 — keep other people out."""
    b = _body(req)
    out = []
    mails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", b)
    if mails:
        out.append(Finding("R06-pii", Level.ERROR,
                           f"Body contains e-mail address(es): {sorted(set(mails))}"))
    phones = re.findall(r"(?<!\d)(?:\+49|0)\s?\d{2,5}[\s/-]\d{3,}", b)
    if phones:
        out.append(Finding("R06-pii", Level.WARN,
                           f"Body may contain phone number(s): {sorted(set(phones))}"))
    if re.findall(r"\bDE\d{2}[\s]?(?:\d{4}[\s]?){4}\d{2}\b", b):
        out.append(Finding("R06-pii", Level.ERROR, "Body contains an IBAN."))
    return out


@offline_rule("R07-reference-format")
def rule_reference(req: dict) -> list[Finding]:
    ref = req.get("reference")
    if ref and ":" not in ref:
        return [Finding("R07-reference-format", Level.ERROR,
                        "reference must have the form 'kind:value', otherwise froide's "
                        "clean_reference discards it silently.")]
    return []


@offline_rule("R08-tags")
def rule_tags(req: dict) -> list[Finding]:
    out = []
    for tag in req.get("tags") or []:
        if len(tag) > MAX_TAG_LENGTH:
            out.append(Finding("R08-tags", Level.WARN,
                               f"Tag {tag!r} will be truncated to {MAX_TAG_LENGTH} "
                               "characters."))
    return out


@offline_rule("R09-public-explicit")
def rule_public_explicit(req: dict) -> list[Finding]:
    if "public" not in req:
        return [Finding("R09-public-explicit", Level.ERROR,
                        "Field 'public' is missing. The default would be True (immediately "
                        "public) — that has to be a deliberate decision.")]
    return []


@offline_rule("R10-no-salutation")
def rule_no_salutation(req: dict) -> list[Finding]:
    """With full_text=False froide frames the text itself — do not frame it twice.

    ``letter_start`` of the law supplies the subject line and the salutation
    ("Guten Tag,"), ``letter_end`` supplies the closing formula plus the sender's name.
    Writing either yourself produces a request with a doubled greeting and a doubled
    sign-off. German phrases are matched because froide's German letter templates are
    what the text ends up next to.
    """
    if req.get("full_text"):
        return []
    body = _body(req)
    out = []
    head = "\n".join(body.strip().splitlines()[:3])
    salutation = re.search(
        r"(Sehr geehrte|Guten Tag|Guten Morgen|Hallo|Moin|Liebe[rs]?\s)", head)
    if salutation:
        out.append(Finding("R10-no-salutation", Level.ERROR,
                           f"Body opens with a salutation ({salutation.group(1)!r}). "
                           "The law's letter_start already supplies one."))
    tail = "\n".join(body.strip().splitlines()[-4:])
    closing = re.search(
        r"(Mit freundlichen Gr|Freundliche Gr|Beste Gr|Mit besten Gr|Viele Gr|"
        r"Herzliche Gr)", tail)
    if closing:
        out.append(Finding("R10-no-salutation", Level.ERROR,
                           f"Body ends with a closing formula ({closing.group(1)!r}). "
                           "The law's letter_end already supplies it, including the name."))
    return out


@offline_rule("R11-law-consistency")
def rule_law_consistency(req: dict) -> list[Finding]:
    """Does the body cite a law that does not match the chosen legal basis?"""
    law = req.get("law") or {}
    body = _body(req)
    out = []
    if law.get("wunsch_id") is None:
        out.append(Finding("R11-law-consistency", Level.ERROR,
                           "No legal basis chosen (law.wunsch_id)."))
    if "Landestransparenzgesetz" in body or "LTranspG" in body:
        if law.get("wunsch_law_type") not in ("IFG", "UIG"):
            out.append(Finding("R11-law-consistency", Level.WARN,
                               "Body invokes the LTranspG but law.wunsch_law_type does not "
                               "match."))
    return out


@offline_rule("R12-api-cannot-set-law")
def rule_api_law_gap(req: dict) -> list[Finding]:
    """The REST API cannot set law_type — MakeRequestSerializer has no such field.

    For submit_via=api a mismatch is a hard error; for submit_via=web_form it is only a
    hint, because ``?law_type=...`` picks the legal basis correctly there.
    """
    law = req.get("law") or {}
    via = req.get("submit_via", "web_form")
    if law.get("wunsch_id") is None or law.get("api_default_id") is None:
        return []
    if law["wunsch_id"] == law["api_default_id"]:
        return []
    level = Level.ERROR if via == "api" else Level.INFO
    return [Finding("R12-api-cannot-set-law", level,
                    f"Desired law {law['wunsch_id']} != API default {law['api_default_id']}. "
                    "POST /api/v1/request/ would set the wrong legal basis; "
                    f"submit_via={via!r}.")]


@offline_rule("R13-status-gate")
def rule_status_gate(req: dict) -> list[Finding]:
    status = req.get("status")
    if status not in DRAFT_STATES:
        return [Finding("R13-status-gate", Level.ERROR, f"Unknown status {status!r}.")]
    return []


@offline_rule("R14-attachments")
def rule_attachments(req: dict) -> list[Finding]:
    out = []
    for att in req.get("attachments") or []:
        ct = att.get("content_type")
        if ct not in POSTAL_CONTENT_TYPES:
            out.append(Finding("R14-attachments", Level.ERROR,
                               f"{att.get('name')}: {ct} is not an accepted type "
                               f"(allowed: {sorted(POSTAL_CONTENT_TYPES)})."))
    return out


@offline_rule("R15-submit-via")
def rule_submit_via(req: dict) -> list[Finding]:
    if req.get("submit_via") not in SUBMIT_METHODS:
        return [Finding("R15-submit-via", Level.ERROR,
                        f"submit_via must be one of {SUBMIT_METHODS}.")]
    return []


@offline_rule("R16-prefill-url-length")
def rule_prefill_url_length(req: dict) -> list[Finding]:
    """Does the draft still fit into a prefilled form URL?"""
    if req.get("submit_via") != "web_form":
        return []
    law = req.get("law") or {}
    qs = urllib.parse.urlencode({
        "subject": _subject(req),
        "body": _body(req),
        "law_type": law.get("wunsch_law_type") or "",
    })
    pb = req.get("publicbody") or {}
    length = len(f"https://fragdenstaat.de/anfrage-stellen/an/{pb.get('id')}/?{qs}")
    if length > MAX_PREFILL_URL_LENGTH:
        return [Finding("R16-prefill-url-length", Level.WARN,
                        f"The prefilled URL would be {length} bytes; the server rejects "
                        f"anything above {MAX_PREFILL_URL_LENGTH} with HTTP 400. Prefill "
                        "only subject and law_type and paste the body from the .body.txt "
                        "file.")]
    return []


@offline_rule("R17-boilerplate-duplication")
def rule_boilerplate_duplication(req: dict) -> list[Finding]:
    """Phrases that the letter_end of the German FOI acts already contains."""
    if req.get("full_text"):
        return []
    body = _body(req).lower()
    hits = [phrase for phrase in (
        "verwaltungsaufwand", "voraussichtliche kosten", "empfangsbestaetigung",
        "empfangsbest\u00e4tigung", "in elektronischer form",
        "zustaendige behoerde weiterleiten", "zust\u00e4ndige beh\u00f6rde weiterleiten",
        "weitergabe meiner daten",
    ) if phrase in body]
    if hits:
        return [Finding("R17-boilerplate-duplication", Level.WARN,
                        f"Phrase(s) {hits} are probably already part of the law's "
                        "letter_end. Check for duplication.")]
    return []


# --- live rules (need the network) ---------------------------------------

@live_rule("L01-publicbody-exists")
def live_publicbody_exists(req: dict, client) -> list[Finding]:
    pb = req.get("publicbody") or {}
    data = client.get_authority(pb["id"])
    out = []
    if pb.get("name") and data["name"] != pb["name"]:
        out.append(Finding("L01-publicbody-exists", Level.ERROR,
                           f"Authority {pb['id']} is called {data['name']!r} live, the draft "
                           f"says {pb['name']!r}."))
    if pb.get("email") and data.get("email") != pb["email"]:
        out.append(Finding("L01-publicbody-exists", Level.WARN,
                           f"E-mail differs: live {data.get('email')!r} vs. {pb['email']!r}."))
    return out


@live_rule("L02-law-offered")
def live_law_offered(req: dict, client) -> list[Finding]:
    pb = req.get("publicbody") or {}
    law = req.get("law") or {}
    data = client.get_authority(pb["id"])
    law_ids = {entry["id"] for entry in data.get("laws", []) if isinstance(entry, dict)}
    if law.get("wunsch_id") not in law_ids:
        return [Finding("L02-law-offered", Level.ERROR,
                        f"Law {law.get('wunsch_id')} is not listed for authority {pb['id']}. "
                        f"Available: {sorted(law_ids)}")]
    return []


@live_rule("L03-api-default-law")
def live_api_default_law(req: dict, client) -> list[Finding]:
    """Recomputes get_applicable_law(): order_by('-meta', '-priority')[0]."""
    pb = req.get("publicbody") or {}
    law = req.get("law") or {}
    data = client.get_authority(pb["id"])
    laws = [entry for entry in data.get("laws", []) if isinstance(entry, dict)]
    if not laws:
        return [Finding("L03-api-default-law", Level.WARN, "Authority lists no laws.")]
    default = applicable_law(laws)
    if law.get("api_default_id") not in (None, default["id"]):
        return [Finding("L03-api-default-law", Level.ERROR,
                        f"law.api_default_id={law['api_default_id']} does not match the "
                        f"actual default {default['id']} ({default['name']!r}).")]
    return []


@live_rule("L04-no-duplicate")
def live_no_duplicate(req: dict, client) -> list[Finding]:
    """Is there already a request to this authority with an identical subject slug?"""
    pb = req.get("publicbody") or {}
    slug = slugify_like_django(_subject(req))
    existing = list(client.paginate("/request/", {"public_body": pb["id"]}, max_items=200))
    hits = [r for r in existing if slugify_like_django(r.get("title", "")) == slug]
    if hits:
        return [Finding("L04-no-duplicate", Level.ERROR,
                        "A request with the same subject already exists: "
                        f"{[h.get('url') for h in hits]}")]
    return []


@live_rule("L05-boilerplate-overlap")
def live_boilerplate_overlap(req: dict, client) -> list[Finding]:
    """Compare the body with letter_start/letter_end of the law actually chosen.

    Whole sentences that already appear in the law's own framing would be sent twice.
    """
    if req.get("full_text"):
        return []
    law = req.get("law") or {}
    law_id = law.get("wunsch_id")
    if law_id is None:
        return []
    data = client.get_law(law_id)
    frame = f"{data.get('letter_start') or ''}\n{data.get('letter_end') or ''}"

    def sentences(text: str) -> set[str]:
        raw = re.split(r"[.\n!?]+", text)
        return {" ".join(x.split()).lower() for x in raw
                if len(" ".join(x.split())) >= 25}

    shared = sentences(_body(req)) & sentences(frame)
    if shared:
        return [Finding("L05-boilerplate-overlap", Level.ERROR,
                        f"{len(shared)} sentence(s) already appear verbatim in the letter "
                        f"frame of law {law_id}: {[s[:60] for s in sorted(shared)]}")]
    return []


def applicable_law(laws: list[dict]) -> dict:
    """Reproduce froide's ``get_applicable_law()``: ``order_by('-meta', '-priority')[0]``.

    ``default_law`` is not exposed by the API, so it has to be recomputed. This is the
    reason the API almost always picks the *meta* law.
    """
    return sorted(laws, key=lambda law: (not law.get("meta"), -(law.get("priority") or 0)))[0]


# --- execution ------------------------------------------------------------

def run_offline(req: dict) -> list[Finding]:
    out: list[Finding] = []
    for _rid, fn in OFFLINE_RULES:
        out.extend(fn(req))
    return out


def run_live(req: dict, client) -> list[Finding]:
    out: list[Finding] = []
    for _rid, fn in LIVE_RULES:
        out.extend(fn(req, client))
    return out


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.level is Level.ERROR]
