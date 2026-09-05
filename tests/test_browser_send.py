"""Every gate on ``send_reply_via_browser``, one at a time, with a mocked browser.

**No test in this file opens a browser and no test in this file sends anything.** The
driver is replaced by a spy that records the call and fails the test if it is reached
while a gate should have blocked it — the same shape as the canary in
`test_security_gates.py`.

  gate 0  the tool is absent from the tool list unless FDS_MCP_BROWSER_SEND=1
  gate 1  status must be "approved", and send_address must be false
  gate 2  no ERROR finding under the follow-up rules
  gate 3  the confirmation_token must match the one in the file
  gate 4  the local message ledger: 2/5min, 6/6h, 8/24h
  gate 5  the form: address checkbox off, recipient readable, text read back, no U+2026,
          one salutation, one closing
  after   the API must show a new message, or the outcome is "unconfirmed"
"""

from __future__ import annotations

import asyncio
import inspect
import time

import pytest
from mcp.server import MCPServer

from fds_mcp import browser, drafts, server
from fds_mcp.browser import BrowserSendError
from fds_mcp.server import ReplyBlocked
from fds_mcp.throttle import ThrottleExceeded, message_ledger

GOOD_REPLY = (
    "Guten Tag,\n\n"
    "ich bitte ergänzend um die Gebührenordnung der Halle.\n\n"
    "Mit freundlichen Grüßen\n"
    "Vorname Nachname"
)

SEND_URL = ("https://fragdenstaat.de/anfrage/"
            "antrag-nach-dem-ltranspg-schutzhuette-duengenheim/#write-messages")


class BrowserSpy:
    """Stands in for the real driver. Records the call; never opens anything."""

    def __init__(self, form=None):
        self.calls = []
        self.form = form or {
            "url": SEND_URL,
            "recipient": "Standardadresse von VG Kaisersesch (info@vg.kaisersesch.de)",
            "subject_in_form": "AW: Antrag [#379655]",
            "address_checkbox_checked": False,
            "salutations": 1,
            "closings": 1,
            "placeholder_present": False,
            "checks": ["'Adresse mitsenden' checkbox: off"],
            "clicked": False,
        }

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        form = dict(self.form)
        form["clicked"] = not kwargs.get("dry_run", True)
        return form


class NeverCalled:
    """Any use of this fails the test."""

    def __call__(self, **kwargs):
        raise AssertionError(
            "The browser was opened although a gate should have blocked it.")


@pytest.fixture
def approved_reply():
    return {
        "kind": "reply",
        "status": "approved",
        "request": {"id": 379655, "title": "Antrag", "slug": "x", "public": True},
        "publicbody": {"id": 4929, "name": "VG Kaisersesch"},
        "subject": "AW: Antrag [#379655]",
        "body": GOOD_REPLY,
        "send_url": SEND_URL,
        "send_address": False,
        "confirmation_token": "SEND-DEADBEEF",
    }


@pytest.fixture
def reply_file(tmp_path, approved_reply):
    return str(drafts.save(approved_reply, tmp_path / "reply.yaml"))


@pytest.fixture(autouse=True)
def no_browser_and_no_network(monkeypatch):
    """Default for every test here: the browser is a spy, the API returns nothing new."""
    spy = BrowserSpy()
    monkeypatch.setattr(server, "browser_sender", lambda: spy)
    monkeypatch.setattr(server, "_known_message_ids", lambda request_id: {1, 2})
    monkeypatch.setattr(server, "_new_message_id",
                        lambda request_id, known, **kw: 1150200)
    return spy


def write(draft, tmp_path, name="reply.yaml"):
    return str(drafts.save(draft, tmp_path / name))


# ==========================================================================
# gate 0 — the tool does not exist unless it was switched on
# ==========================================================================

def test_gate0_the_tool_is_absent_from_the_tool_list_by_default():
    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert "send_reply_via_browser" not in names
    assert server.BROWSER_SEND_REGISTERED is False


def test_gate0_registration_needs_the_environment_variable(monkeypatch):
    fresh = MCPServer("probe", version="0")
    monkeypatch.delenv("FDS_MCP_BROWSER_SEND", raising=False)
    assert server._register_browser_tool(fresh) is False
    assert asyncio.run(fresh.list_tools()) == []


def test_gate0_registration_happens_with_the_environment_variable(monkeypatch):
    fresh = MCPServer("probe", version="0")
    monkeypatch.setenv("FDS_MCP_BROWSER_SEND", "1")
    assert server._register_browser_tool(fresh) is True
    names = {tool.name for tool in asyncio.run(fresh.list_tools())}
    assert names == {"send_reply_via_browser"}


@pytest.mark.parametrize("value", ["0", "true", "yes", ""])
def test_gate0_only_the_exact_value_one_switches_it_on(monkeypatch, value):
    fresh = MCPServer("probe", version="0")
    monkeypatch.setenv("FDS_MCP_BROWSER_SEND", value)
    assert server._register_browser_tool(fresh) is False


def test_gate0_the_tool_defaults_to_dry_run():
    signature = inspect.signature(server.send_reply_via_browser)
    assert signature.parameters["dry_run"].default is True


# ==========================================================================
# gate 1 — status approved, and never the postal address
# ==========================================================================

@pytest.mark.parametrize("status", ["draft", "validated"])
def test_gate1_refuses_an_unapproved_draft(tmp_path, approved_reply, monkeypatch, status):
    monkeypatch.setattr(server, "browser_sender", lambda: NeverCalled())
    approved_reply["status"] = status
    with pytest.raises(ReplyBlocked, match="Gate 1"):
        server.send_reply_via_browser(write(approved_reply, tmp_path), "SEND-DEADBEEF")


def test_gate1_refuses_an_already_sent_draft(tmp_path, approved_reply, monkeypatch):
    monkeypatch.setattr(server, "browser_sender", lambda: NeverCalled())
    approved_reply["status"] = "sent"
    with pytest.raises(ReplyBlocked, match="already marked 'sent'"):
        server.send_reply_via_browser(write(approved_reply, tmp_path), "SEND-DEADBEEF")


def test_gate1_refuses_a_request_draft(tmp_path, submittable_draft, monkeypatch):
    """A request draft is not a reply draft, whatever its status says."""
    monkeypatch.setattr(server, "browser_sender", lambda: NeverCalled())
    with pytest.raises(ReplyBlocked, match="not a reply draft"):
        server.send_reply_via_browser(write(submittable_draft, tmp_path), "SEND-DEADBEEF")


def test_gate1_refuses_to_transmit_the_postal_address(tmp_path, approved_reply,
                                                      monkeypatch):
    monkeypatch.setattr(server, "browser_sender", lambda: NeverCalled())
    approved_reply["send_address"] = True
    with pytest.raises(ReplyBlocked, match="send_address"):
        server.send_reply_via_browser(write(approved_reply, tmp_path), "SEND-DEADBEEF")


def test_gate1_an_approved_reply_passes(reply_file):
    result = server.send_reply_via_browser(reply_file, "SEND-DEADBEEF")
    assert "status" in result["gates_passed"]


# ==========================================================================
# gate 2 — the follow-up rules
# ==========================================================================

def test_gate2_refuses_a_reply_without_a_salutation(tmp_path, approved_reply, monkeypatch):
    monkeypatch.setattr(server, "browser_sender", lambda: NeverCalled())
    approved_reply["body"] = "Bitte die Gebührenordnung.\n\nMit freundlichen Grüßen\nX"
    with pytest.raises(ReplyBlocked, match="R19-reply-needs-salutation"):
        server.send_reply_via_browser(write(approved_reply, tmp_path), "SEND-DEADBEEF")


def test_gate2_refuses_the_prefilled_placeholder(tmp_path, approved_reply, monkeypatch):
    monkeypatch.setattr(server, "browser_sender", lambda: NeverCalled())
    approved_reply["body"] = "Guten Tag,\n\n…\n\nMit freundlichen Grüßen\nX"
    with pytest.raises(ReplyBlocked, match="R04-placeholder"):
        server.send_reply_via_browser(write(approved_reply, tmp_path), "SEND-DEADBEEF")


def test_gate2_refuses_an_email_address_in_the_text(tmp_path, approved_reply, monkeypatch):
    monkeypatch.setattr(server, "browser_sender", lambda: NeverCalled())
    approved_reply["body"] = GOOD_REPLY.replace(
        "Mit freundlichen", "Erreichbar unter a.b@example.org\n\nMit freundlichen")
    with pytest.raises(ReplyBlocked, match="R06-pii"):
        server.send_reply_via_browser(write(approved_reply, tmp_path), "SEND-DEADBEEF")


def test_gate2_a_clean_reply_passes(reply_file):
    result = server.send_reply_via_browser(reply_file, "SEND-DEADBEEF")
    assert "rules" in result["gates_passed"]


# ==========================================================================
# gate 3 — the human-set confirmation token
# ==========================================================================

def test_gate3_refuses_a_wrong_token(reply_file, monkeypatch):
    monkeypatch.setattr(server, "browser_sender", lambda: NeverCalled())
    with pytest.raises(ReplyBlocked, match="Gate 3"):
        server.send_reply_via_browser(reply_file, "SEND-CAFEBABE")


def test_gate3_refuses_an_empty_token(reply_file, monkeypatch):
    monkeypatch.setattr(server, "browser_sender", lambda: NeverCalled())
    with pytest.raises(ReplyBlocked, match="Gate 3"):
        server.send_reply_via_browser(reply_file, "")


def test_gate3_refuses_the_placeholder_token(tmp_path, approved_reply, monkeypatch):
    monkeypatch.setattr(server, "browser_sender", lambda: NeverCalled())
    approved_reply["confirmation_token"] = drafts.CONFIRMATION_PLACEHOLDER
    with pytest.raises(ReplyBlocked, match="no confirmation_token"):
        server.send_reply_via_browser(write(approved_reply, tmp_path),
                                      drafts.CONFIRMATION_PLACEHOLDER)


def test_gate3_a_matching_token_passes(reply_file):
    result = server.send_reply_via_browser(reply_file, "SEND-DEADBEEF")
    assert "confirmation" in result["gates_passed"]


# ==========================================================================
# gate 4 — the message throttle (2/5min, 6/6h, 8/24h)
# ==========================================================================

def test_gate4_the_message_ledger_uses_the_published_message_limits():
    from fds_mcp.rules import MESSAGE_THROTTLE

    assert MESSAGE_THROTTLE == [(2, 5 * 60), (6, 6 * 3600), (8, 24 * 3600)]
    assert message_ledger().limits == tuple(MESSAGE_THROTTLE)


def test_gate4_blocks_after_two_messages_in_five_minutes(tmp_path):
    ledger = message_ledger(tmp_path / "throttle.json")
    now = time.time()
    ledger.record(now=now)
    ledger.record(now=now)
    with pytest.raises(ThrottleExceeded, match="5min"):
        ledger.check(now=now)


def test_gate4_refuses_when_the_ledger_is_full(reply_file, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "browser_sender", lambda: NeverCalled())
    ledger_file = tmp_path / "throttle.json"
    monkeypatch.setattr("fds_mcp.config.throttle_path", lambda: ledger_file)
    full = message_ledger(ledger_file)
    now = time.time()
    full.record(now=now)
    full.record(now=now)
    with pytest.raises(ThrottleExceeded):
        server.send_reply_via_browser(reply_file, "SEND-DEADBEEF")


def test_gate4_refuses_a_send_url_outside_fragdenstaat(tmp_path, approved_reply,
                                                       monkeypatch):
    monkeypatch.setattr(server, "browser_sender", lambda: NeverCalled())
    approved_reply["send_url"] = "https://evil.example.org/anfrage/x/#write-messages"
    with pytest.raises(ReplyBlocked, match="Gate 4"):
        server.send_reply_via_browser(write(approved_reply, tmp_path), "SEND-DEADBEEF")


# ==========================================================================
# gate 5 — the form itself
# ==========================================================================

class FakeLocator:
    """One form control. Behaves like a playwright locator, minus the browser."""

    def __init__(self, page, role, name):
        self.page, self.role, self.name = page, role, name

    @property
    def first(self):
        return self

    def wait_for(self, **kwargs):
        if (self.role, self.name) in self.page.missing:
            raise LocatorError(f"{self.role} {self.name!r} not found")

    def get_attribute(self, attr):
        if (self.role, self.name) in self.page.missing:
            raise LocatorError(f"{self.role} {self.name!r} not found")
        return self.page.values.get(self.name)

    def inner_text(self):
        return self.page.values.get(self.name, "")

    def is_checked(self):
        return bool(self.page.checked.get(self.name, False))

    def fill(self, value):
        self.page.values[self.name] = self.page.rewrite(self.name, value)

    def input_value(self):
        return self.page.values.get(self.name, "")

    def click(self):
        self.page.clicked = True


class LocatorError(Exception):
    """Stands in for playwright's Error."""


class FakePage:
    """A reply form that looks the way the real one looked on 2026-09-05."""

    def __init__(self, *, checked=False, missing=(), rewrite=None, recipient=None):
        self.values = {
            browser.RECIPIENT_RADIO:
                recipient if recipient is not None else
                "Standardadresse von VG Kaisersesch (info@vg.kaisersesch.de)",
            browser.SUBJECT_FIELD: "AW: prefilled [#1]",
            browser.MESSAGE_FIELD:
                "Guten Tag,\n\n…\n\nMit freundlichen Grüßen\nVorname Nachname",
        }
        self.checked = {browser.ADDRESS_CHECKBOX: checked}
        self.missing = set(missing)
        self.clicked = False
        self.visited = None
        self._rewrite = rewrite

    def rewrite(self, name, value):
        return self._rewrite(name, value) if self._rewrite else value

    def goto(self, url):
        self.visited = url

    def get_by_role(self, role, name=None):
        return FakeLocator(self, role, name)

    def wait_for_load_state(self, *args):
        return None


def drive(page, body=GOOD_REPLY, subject="AW: Antrag [#379655]", dry_run=True):
    return browser.drive_form(page, send_url=SEND_URL, subject=subject, body=body,
                              dry_run=dry_run, locator_error=LocatorError)


def test_gate5_refuses_a_ticked_address_checkbox():
    page = FakePage(checked=True)
    with pytest.raises(BrowserSendError, match="Adresse mitsenden"):
        drive(page)
    assert page.clicked is False


def test_gate5_refuses_when_a_field_cannot_be_found():
    """A form that changed shape is a form this code must not press buttons in."""
    page = FakePage(missing=[("textbox", browser.SUBJECT_FIELD)])
    with pytest.raises(BrowserSendError, match="does not look the way"):
        drive(page)
    assert page.clicked is False


def test_gate5_refuses_when_the_recipient_cannot_be_read():
    page = FakePage(recipient="")
    with pytest.raises(BrowserSendError, match="recipient"):
        drive(page)
    assert page.clicked is False


def test_gate5_refuses_when_the_field_does_not_hold_what_was_typed():
    page = FakePage(rewrite=lambda name, value: value + " (angehängt)")
    with pytest.raises(BrowserSendError, match="does not contain what was typed"):
        drive(page)
    assert page.clicked is False


def test_gate5_overwrites_the_prefilled_placeholder_text():
    page = FakePage()
    report = drive(page)
    assert page.values[browser.MESSAGE_FIELD] == GOOD_REPLY
    assert report["placeholder_present"] is False
    assert (report["salutations"], report["closings"]) == (1, 1)


def test_gate5_dry_run_fills_the_form_but_does_not_click():
    page = FakePage()
    report = drive(page, dry_run=True)
    assert page.visited == SEND_URL
    assert page.clicked is False
    assert report["clicked"] is False
    assert any("NOT pressed" in check for check in report["checks"])


def test_gate5_a_real_send_clicks_once():
    page = FakePage()
    report = drive(page, dry_run=False)
    assert page.clicked is True
    assert report["clicked"] is True


def test_gate5_the_report_names_the_recipient_and_the_checkbox():
    report = drive(FakePage())
    assert "info@vg.kaisersesch.de" in report["recipient"]
    assert report["address_checkbox_checked"] is False
    assert any("checkbox: off" in check for check in report["checks"])


def test_gate5_the_text_checks_reject_the_placeholder():
    problems = browser.check_text("Guten Tag,\n\n…\n\nMit freundlichen Grüßen\nX")
    assert any("U+2026" in p for p in problems)


def test_gate5_the_text_checks_reject_a_missing_salutation():
    problems = browser.check_text("Bitte die Ordnung.\n\nMit freundlichen Grüßen\nX")
    assert any("0 salutations" in p for p in problems)


def test_gate5_the_text_checks_reject_a_doubled_closing():
    problems = browser.check_text(GOOD_REPLY + "\n\nMit freundlichen Grüßen\nX")
    assert any("2 closing formulae" in p for p in problems)


def test_gate5_a_clean_text_passes_the_checks():
    assert browser.check_text(GOOD_REPLY) == []


def test_gate5_a_driver_refusal_stops_the_send(reply_file, monkeypatch):
    def refuse(**kwargs):
        raise BrowserSendError("'Adresse mitsenden' checkbox is ticked.")

    monkeypatch.setattr(server, "browser_sender", lambda: refuse)
    with pytest.raises(BrowserSendError, match="Adresse mitsenden"):
        server.send_reply_via_browser(reply_file, "SEND-DEADBEEF", dry_run=False)


def test_gate5_the_form_report_is_returned(reply_file):
    result = server.send_reply_via_browser(reply_file, "SEND-DEADBEEF")
    assert result["form"]["address_checkbox_checked"] is False
    assert "info@vg.kaisersesch.de" in result["form"]["recipient"]
    assert "form" in result["gates_passed"]


def test_the_driver_is_told_exactly_what_the_file_says(reply_file, no_browser_and_no_network):
    server.send_reply_via_browser(reply_file, "SEND-DEADBEEF")
    call = no_browser_and_no_network.calls[0]
    assert call["body"] == GOOD_REPLY
    assert call["subject"] == "AW: Antrag [#379655]"
    assert call["send_url"] == SEND_URL
    assert set(call) == {"send_url", "subject", "body", "dry_run"}


# ==========================================================================
# dry_run, and the confirmation afterwards
# ==========================================================================

def test_dry_run_does_not_click(reply_file, no_browser_and_no_network):
    result = server.send_reply_via_browser(reply_file, "SEND-DEADBEEF", dry_run=True)
    assert no_browser_and_no_network.calls[0]["dry_run"] is True
    assert result["sent"] is False
    assert result["outcome"] == "dry_run"
    assert result["form"]["clicked"] is False


def test_dry_run_does_not_record_a_message_in_the_ledger(reply_file, tmp_path, monkeypatch):
    ledger_file = tmp_path / "throttle.json"
    monkeypatch.setattr("fds_mcp.config.throttle_path", lambda: ledger_file)
    server.send_reply_via_browser(reply_file, "SEND-DEADBEEF", dry_run=True)
    assert message_ledger(ledger_file).timestamps() == []


def test_dry_run_does_not_change_the_draft_status(reply_file):
    server.send_reply_via_browser(reply_file, "SEND-DEADBEEF", dry_run=True)
    assert drafts.load(reply_file)["status"] == "approved"


def test_a_confirmed_send_reports_the_new_message_id(reply_file):
    result = server.send_reply_via_browser(reply_file, "SEND-DEADBEEF", dry_run=False)
    assert result["sent"] is True
    assert result["confirmed"] is True
    assert result["outcome"] == "confirmed"
    assert result["message_id"] == 1150200


def test_a_confirmed_send_marks_the_draft_as_sent(reply_file):
    server.send_reply_via_browser(reply_file, "SEND-DEADBEEF", dry_run=False)
    stored = drafts.load(reply_file)
    assert stored["status"] == "sent"
    assert stored["sent"]["message_id"] == 1150200


def test_an_unconfirmed_send_is_never_reported_as_success(reply_file, monkeypatch):
    """No new message on the API means unclear — not failure, and not success."""
    monkeypatch.setattr(server, "_new_message_id", lambda request_id, known, **kw: None)
    result = server.send_reply_via_browser(reply_file, "SEND-DEADBEEF", dry_run=False)
    assert result["sent"] is True
    assert result["confirmed"] is False
    assert result["outcome"] == "unconfirmed"
    assert "UNCLEAR" in result["warning"]


def test_an_unconfirmed_send_leaves_the_draft_approved(reply_file, monkeypatch):
    """Not marked 'sent': the state machine must not claim more than we know."""
    monkeypatch.setattr(server, "_new_message_id", lambda request_id, known, **kw: None)
    server.send_reply_via_browser(reply_file, "SEND-DEADBEEF", dry_run=False)
    assert drafts.load(reply_file)["status"] == "approved"


def test_a_real_send_is_recorded_in_the_message_ledger(reply_file, tmp_path, monkeypatch):
    ledger_file = tmp_path / "throttle.json"
    monkeypatch.setattr("fds_mcp.config.throttle_path", lambda: ledger_file)
    server.send_reply_via_browser(reply_file, "SEND-DEADBEEF", dry_run=False)
    assert len(message_ledger(ledger_file).timestamps()) == 1


# ==========================================================================
# the driver never composes anything
# ==========================================================================

def test_the_driver_has_no_way_to_write_text():
    """It takes a subject and a body and types those. There is no other input."""
    parameters = set(inspect.signature(browser.send_reply).parameters)
    assert parameters == {"send_url", "subject", "body", "dry_run", "headless",
                          "timeout_ms"}


def test_the_browser_profile_can_be_moved_out_of_the_way(monkeypatch, tmp_path):
    monkeypatch.setenv("FDS_MCP_BROWSER_PROFILE", str(tmp_path / "profile"))
    assert browser.profile_dir() == tmp_path / "profile"


def test_a_missing_playwright_says_what_to_install():
    assert "fds-mcp[browser]" in browser.PLAYWRIGHT_MISSING
    assert "playwright install" in browser.PLAYWRIGHT_MISSING
