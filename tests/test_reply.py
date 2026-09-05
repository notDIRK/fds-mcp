"""Replying to an authority: rule R19, ``build_reply_draft`` and the reply draft file.

A follow-up is the mirror image of a request. froide frames a request with the act's
letter_start/letter_end; it does not frame a follow-up at all. R10 therefore forbids a
salutation and R19 requires one, and the two rules must never both fire on the same text.

Nothing in this file touches the network. ``build_reply_draft`` reads one request through
the API and is given a fake client here.
"""

from __future__ import annotations

import pytest

from fds_mcp import drafts, rules, server
from fds_mcp.client import FdsError

GOOD_REPLY = (
    "Guten Tag,\n\n"
    "vielen Dank für Ihre Nachricht. Ich bitte ergänzend um die Gebührenordnung, "
    "die für die Überlassung der Halle gilt.\n\n"
    "Mit freundlichen Grüßen\n"
    "Vorname Nachname"
)


def ids(findings):
    return {f.rule for f in findings}


def err_ids(findings):
    return {f.rule for f in rules.errors(findings)}


def reply(body=GOOD_REPLY, subject="AW: Antrag [#379655]"):
    return {"subject": subject, "body": body}


class FakeRequestClient:
    """Stands in for the yellow-tier client. Records what was asked for."""

    def __init__(self, record=None):
        self.record = record if record is not None else {
            "id": 379655,
            "title": "Antrag nach dem LTranspG – Schutzhütte Düngenheim",
            "slug": "antrag-nach-dem-ltranspg-schutzhuette-duengenheim",
            "url": "/anfrage/antrag-nach-dem-ltranspg-schutzhuette-duengenheim/",
            "site_url": None,
            "public": True,
            "public_body": {"id": 4929, "name": "Verbandsgemeindeverwaltung Kaisersesch"},
        }
        self.asked_for = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_request(self, req_id):
        self.asked_for = req_id
        return self.record


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeRequestClient()
    monkeypatch.setattr(server, "token_client", lambda: client)
    return client


# ==========================================================================
# R19 — the inverse of R10
# ==========================================================================

def test_r19_accepts_a_reply_with_greeting_and_closing():
    assert rules.errors(rules.run_reply(reply())) == []


def test_r19_a_reply_without_a_salutation_is_an_error():
    body = "Ich bitte ergänzend um die Gebührenordnung.\n\nMit freundlichen Grüßen\nX"
    assert "R19-reply-needs-salutation" in err_ids(rules.run_reply(reply(body)))


def test_r19_a_reply_without_a_closing_formula_is_an_error():
    body = "Guten Tag,\n\nich bitte ergänzend um die Gebührenordnung."
    assert "R19-reply-needs-salutation" in err_ids(rules.run_reply(reply(body)))


def test_r19_a_doubled_salutation_is_an_error():
    body = GOOD_REPLY.replace("Guten Tag,", "Guten Tag,\n\nSehr geehrte Damen und Herren,")
    findings = rules.run_reply(reply(body))
    assert "R19-reply-needs-salutation" in err_ids(findings)
    assert any("2 instances" in f.message for f in findings)


def test_r19_a_doubled_closing_is_an_error():
    body = GOOD_REPLY + "\n\nMit freundlichen Grüßen\nVorname Nachname"
    assert "R19-reply-needs-salutation" in err_ids(rules.run_reply(reply(body)))


def test_r10_and_r19_are_exact_opposites():
    """The same text: forbidden in a framed request body, required in a reply."""
    request_like = {"full_text": False, "body": GOOD_REPLY, "subject": "x" * 20}
    assert "R10-no-salutation" in err_ids(rules.rule_no_salutation(request_like))
    assert "R19-reply-needs-salutation" not in err_ids(rules.run_reply(reply()))


def test_the_prefilled_form_text_is_rejected_wholesale():
    """What the form actually contains when you open it, verbatim.

    Salutation and closing are both there, so R19 is happy — and R04 still refuses it,
    because the middle of the message is the placeholder U+2026. This is the single most
    likely mistake on this path.
    """
    prefilled = ("Guten Tag,\n\n" + rules.PLACEHOLDER_MARKER +
                 "\n\nMit freundlichen Grüßen\nVorname Nachname")
    findings = rules.run_reply(reply(prefilled))
    assert "R04-placeholder" in err_ids(findings)
    assert "R19-reply-needs-salutation" not in ids(findings)


def test_an_email_address_in_a_reply_is_an_error():
    body = GOOD_REPLY.replace("Mit freundlichen", "Melden Sie sich bei a.b@example.org\n\n"
                                                  "Mit freundlichen")
    assert "R06-pii" in err_ids(rules.run_reply(reply(body)))


def test_an_iban_in_a_reply_is_an_error():
    body = GOOD_REPLY.replace("Mit freundlichen",
                              "DE02 1203 0000 0000 2020 51\n\nMit freundlichen")
    assert "R06-pii" in err_ids(rules.run_reply(reply(body)))


def test_an_overlong_reply_subject_is_an_error():
    findings = rules.run_reply(reply(subject="A" * (rules.MAX_SUBJECT_LENGTH + 1)))
    assert "R01-subject-length" in err_ids(findings)


def test_a_short_reply_subject_is_fine():
    """No lower bound: froide's reply subject is not the request subject."""
    assert "R01-subject-length" not in ids(rules.run_reply(reply(subject="AW: x")))


def test_the_reply_rule_set_does_not_contain_the_framing_rules():
    """R10 (no salutation) and R18 (self-contained) must never run on a reply."""
    registered = {rid for rid, _fn in rules.REPLY_RULES}
    assert "R10-no-salutation" not in registered
    assert "R18-full-text-self-contained" not in registered
    assert "R19-reply-needs-salutation" in registered


# ==========================================================================
# build_reply_draft
# ==========================================================================

def test_build_reply_draft_proposes_the_froide_subject(fake_client):
    result = server.build_reply_draft(379655, GOOD_REPLY)
    assert result["subject"] == (
        "AW: Antrag nach dem LTranspG – Schutzhütte Düngenheim [#379655]")
    assert fake_client.asked_for == 379655


def test_build_reply_draft_keeps_an_explicit_subject(fake_client):
    result = server.build_reply_draft(379655, GOOD_REPLY, subject="AW: Nachtrag")
    assert result["subject"] == "AW: Nachtrag"


def test_build_reply_draft_returns_the_form_url(fake_client):
    result = server.build_reply_draft(379655, GOOD_REPLY)
    assert result["send_url"] == (
        "https://fragdenstaat.de/anfrage/"
        "antrag-nach-dem-ltranspg-schutzhuette-duengenheim/#write-messages")


def test_build_reply_draft_derives_the_slug_from_the_url_when_needed(monkeypatch):
    record = dict(FakeRequestClient().record)
    record.pop("slug")
    client = FakeRequestClient(record)
    monkeypatch.setattr(server, "token_client", lambda: client)
    result = server.build_reply_draft(379655, GOOD_REPLY)
    assert "antrag-nach-dem-ltranspg-schutzhuette-duengenheim" in result["send_url"]


def test_build_reply_draft_refuses_when_no_slug_can_be_derived(monkeypatch):
    """Never guessed from the title: a wrong slug is a message to a stranger."""
    record = dict(FakeRequestClient().record)
    record.pop("slug")
    record["url"] = None
    monkeypatch.setattr(server, "token_client", lambda: FakeRequestClient(record))
    with pytest.raises(FdsError, match="slug"):
        server.build_reply_draft(379655, GOOD_REPLY)


def test_build_reply_draft_reports_findings(fake_client):
    result = server.build_reply_draft(379655, "Ohne alles.")
    assert result["passes"] is False
    assert "R19-reply-needs-salutation" in {f["rule"] for f in result["findings"]}


def test_build_reply_draft_always_warns_about_the_address_checkbox(fake_client):
    result = server.build_reply_draft(379655, GOOD_REPLY)
    assert "Adresse mitsenden" in result["address_warning"]
    assert any("Adresse mitsenden" in step for step in result["next_steps"])


def test_build_reply_draft_flags_a_public_request(fake_client):
    result = server.build_reply_draft(379655, GOOD_REPLY)
    assert result["request_public"] is True
    assert any("PUBLIC" in step for step in result["next_steps"])


def test_build_reply_draft_marks_the_authority_text_as_untrusted(fake_client):
    result = server.build_reply_draft(379655, GOOD_REPLY)
    assert "request_title" in result["untrusted_content"]["fields"]


def test_build_reply_draft_sends_nothing(fake_client):
    """The fake client has no write method at all — using one would raise."""
    result = server.build_reply_draft(379655, GOOD_REPLY)
    assert result["sent"] is False
    assert result["written"] is False


def test_build_reply_draft_writes_no_file_without_a_path(tmp_path, fake_client):
    server.build_reply_draft(379655, GOOD_REPLY)
    assert list(tmp_path.rglob("*.yaml")) == []


# ==========================================================================
# the reply draft file
# ==========================================================================

def test_build_reply_draft_writes_a_draft_file(tmp_path, fake_client):
    target = tmp_path / "reply.yaml"
    result = server.build_reply_draft(379655, GOOD_REPLY, path=str(target))
    assert result["written"] is True
    stored = drafts.load(target)
    assert stored["kind"] == "reply"
    assert stored["status"] == "draft"
    assert stored["body"] == GOOD_REPLY
    assert stored["request"]["id"] == 379655


def test_a_written_reply_draft_is_never_approved(tmp_path, fake_client):
    target = tmp_path / "reply.yaml"
    server.build_reply_draft(379655, GOOD_REPLY, path=str(target))
    stored = drafts.load(target)
    assert stored["status"] == "draft"
    assert stored["confirmation_token"] == drafts.CONFIRMATION_PLACEHOLDER
    assert stored["send_address"] is False


def test_a_reply_draft_path_must_be_a_yaml_file(tmp_path, fake_client):
    with pytest.raises(drafts.DraftError, match="must end in"):
        server.build_reply_draft(379655, GOOD_REPLY, path=str(tmp_path / "reply.txt"))


def test_a_reply_draft_honours_the_draft_dir_confinement(tmp_path, monkeypatch,
                                                         fake_client):
    allowed = tmp_path / "drafts"
    allowed.mkdir()
    monkeypatch.setenv("FDS_MCP_DRAFT_DIR", str(allowed))
    server.build_reply_draft(379655, GOOD_REPLY, path=str(allowed / "reply.yaml"))
    with pytest.raises(drafts.DraftError, match="outside FDS_MCP_DRAFT_DIR"):
        server.build_reply_draft(379655, GOOD_REPLY, path=str(tmp_path / "reply.yaml"))


def test_a_reply_draft_carries_its_own_header(tmp_path, fake_client):
    target = tmp_path / "reply.yaml"
    server.build_reply_draft(379655, GOOD_REPLY, path=str(target))
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# FragDenStaat reply draft")
    assert "NOTHING HAS BEEN SENT" in text


def test_a_reply_draft_can_be_rewritten_but_a_foreign_file_cannot(tmp_path, fake_client):
    target = tmp_path / "reply.yaml"
    server.build_reply_draft(379655, GOOD_REPLY, path=str(target))
    server.build_reply_draft(379655, GOOD_REPLY, path=str(target))     # no error
    foreign = tmp_path / "notes.yaml"
    foreign.write_text("some: unrelated file\n", encoding="utf-8")
    with pytest.raises(drafts.DraftError, match="Refusing to overwrite"):
        server.build_reply_draft(379655, GOOD_REPLY, path=str(foreign))


def test_reply_states_end_in_sent_not_submitted():
    draft = drafts.new_reply_draft(
        request_id=1, request_title="t", request_slug="s", subject="AW: t",
        body=GOOD_REPLY, send_url="https://fragdenstaat.de/anfrage/s/#write-messages")
    assert drafts.set_status(draft, "sent")["status"] == "sent"
    with pytest.raises(drafts.DraftError):
        drafts.set_status(draft, "submitted")


def test_the_reply_tool_is_yellow_and_says_it_sends_nothing():
    text = server.build_reply_draft.__doc__ or ""
    assert "YELLOW" in text
    assert "sends nothing" in text
