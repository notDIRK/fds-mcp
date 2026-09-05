"""Proof that each of the seven hard safety rules actually bites.

Every test here mutates exactly one precondition and asserts that submit_request()
(or the HTTP client) refuses. A canary client makes an unnoticed POST impossible:
if anything reached the network, the test fails loudly.

  rule 1  submit_request aborts unless the draft's status is "approved"
  rule 2  submit_request aborts while any ERROR finding is open
  rule 3  submit_request aborts when law.wunsch_id != law.api_default_id
  rule 4  submit_request aborts unless confirmation_token matches the stored value
  rule 5  every red tool defaults to dry_run=True
  rule 6  local throttle bookkeeping blocks before a POST goes out
  rule 7  the HTTP client refuses any non-GET method without allow_write
"""

from __future__ import annotations

import inspect
import time

import pytest

from fds_mcp import drafts, server
from fds_mcp.client import AuthRequired, FdsClient, WriteBlocked
from fds_mcp.server import SubmitBlocked
from fds_mcp.throttle import ThrottleExceeded, ThrottleLedger


class Canary:
    """Stands in for the write client. Any use of it fails the test."""

    def __init__(self):
        self.used = False

    def __call__(self, *args, **kwargs):
        self.used = True
        raise AssertionError("A network write was attempted although a gate should "
                             "have blocked it.")


@pytest.fixture(autouse=True)
def no_network_writes(monkeypatch):
    """Any attempt to build a write client during these tests is a failure."""
    canary = Canary()
    monkeypatch.setattr(server, "write_client", canary)
    monkeypatch.setattr(server, "read_client", canary)
    return canary


def write(draft, path):
    drafts.save(draft, path)
    return str(path)


# ==========================================================================
# rule 1 — status must be "approved"
# ==========================================================================

@pytest.mark.parametrize("status", ["draft", "validated"])
def test_rule1_submit_refuses_unapproved_draft(tmp_path, submittable_draft, status):
    submittable_draft["status"] = status
    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(SubmitBlocked, match="Gate 1"):
        server.submit_request(path, "SEND-DEADBEEF", dry_run=True)


def test_rule1_submit_refuses_an_already_submitted_draft(tmp_path, submittable_draft):
    submittable_draft["status"] = "submitted"
    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(SubmitBlocked, match="already marked 'submitted'"):
        server.submit_request(path, "SEND-DEADBEEF", dry_run=True)


def test_rule1_approved_draft_passes_gate_one(tmp_path, submittable_draft):
    path = write(submittable_draft, tmp_path / "d.yaml")
    result = server.submit_request(path, "SEND-DEADBEEF", dry_run=True)
    assert "status" in result["gates_passed"]


# ==========================================================================
# rule 2 — no open ERROR finding
# ==========================================================================

def test_rule2_submit_refuses_while_an_error_finding_is_open(tmp_path, submittable_draft):
    submittable_draft["subject"] = "kurz"          # violates R01
    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(SubmitBlocked, match="Gate 2"):
        server.submit_request(path, "SEND-DEADBEEF", dry_run=True)


def test_rule2_names_the_offending_rule(tmp_path, submittable_draft):
    submittable_draft["body"] = "Mail: someone@example.org"   # violates R06 (and R03)
    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(SubmitBlocked, match="R06-pii"):
        server.submit_request(path, "SEND-DEADBEEF", dry_run=True)


def test_rule2_a_warning_alone_does_not_block(tmp_path, submittable_draft):
    submittable_draft["tags"] = ["t" * 150]        # R08 is a WARN
    path = write(submittable_draft, tmp_path / "d.yaml")
    result = server.submit_request(path, "SEND-DEADBEEF", dry_run=True)
    assert "rules" in result["gates_passed"]


# ==========================================================================
# rule 3 — the API cannot choose the legal basis
# ==========================================================================

def test_rule3_submit_refuses_when_desired_law_differs_from_api_default(
        tmp_path, submittable_draft):
    submittable_draft["law"] = {"wunsch_id": 16, "wunsch_law_type": "IFG",
                                "api_default_id": 18}
    submittable_draft["submit_via"] = "web_form"   # keeps R12 at INFO, isolating gate 3
    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(SubmitBlocked, match="Gate 3"):
        server.submit_request(path, "SEND-DEADBEEF", dry_run=True)


def test_rule3_submit_refuses_when_the_api_default_is_unknown(tmp_path, submittable_draft):
    submittable_draft["law"] = {"wunsch_id": 18, "api_default_id": None}
    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(SubmitBlocked, match="Gate 3"):
        server.submit_request(path, "SEND-DEADBEEF", dry_run=True)


def test_rule3_matching_laws_pass(tmp_path, submittable_draft):
    result = server.submit_request(write(submittable_draft, tmp_path / "d.yaml"),
                                   "SEND-DEADBEEF", dry_run=True)
    assert "law" in result["gates_passed"]
    assert result["payload"]["law_id_that_will_be_applied"] == 18


# ==========================================================================
# rule 4 — the human-set confirmation token
# ==========================================================================

def test_rule4_submit_refuses_a_wrong_token(tmp_path, submittable_draft):
    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(SubmitBlocked, match="Gate 4"):
        server.submit_request(path, "SEND-CAFEBABE", dry_run=True)


def test_rule4_submit_refuses_an_empty_token(tmp_path, submittable_draft):
    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(SubmitBlocked, match="Gate 4"):
        server.submit_request(path, "", dry_run=True)


def test_rule4_submit_refuses_the_placeholder_token(tmp_path, submittable_draft):
    submittable_draft["confirmation_token"] = drafts.CONFIRMATION_PLACEHOLDER
    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(SubmitBlocked, match="no confirmation_token"):
        server.submit_request(path, drafts.CONFIRMATION_PLACEHOLDER, dry_run=True)


def test_rule4_submit_refuses_a_missing_token_field(tmp_path, submittable_draft):
    submittable_draft.pop("confirmation_token")
    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(SubmitBlocked, match="no confirmation_token"):
        server.submit_request(path, "anything", dry_run=True)


def test_rule4_the_matching_token_passes(tmp_path, submittable_draft):
    result = server.submit_request(write(submittable_draft, tmp_path / "d.yaml"),
                                   "SEND-DEADBEEF", dry_run=True)
    assert "confirmation" in result["gates_passed"]


# ==========================================================================
# rule 5 — dry_run defaults to True on every red tool
# ==========================================================================

RED_TOOLS = ["create_request_draft", "validate_draft", "build_submit_url",
             "submit_request"]


@pytest.mark.parametrize("name", RED_TOOLS)
def test_rule5_red_tools_default_to_dry_run(name):
    signature = inspect.signature(getattr(server, name))
    assert "dry_run" in signature.parameters, f"{name} has no dry_run parameter"
    assert signature.parameters["dry_run"].default is True, \
        f"{name} does not default to dry_run=True"


def test_rule5_dry_run_submit_does_not_post(tmp_path, submittable_draft, no_network_writes):
    result = server.submit_request(write(submittable_draft, tmp_path / "d.yaml"),
                                   "SEND-DEADBEEF", dry_run=True)
    assert result["submitted"] is False
    assert no_network_writes.used is False


def test_rule5_dry_run_draft_creation_writes_no_file(tmp_path):
    target = tmp_path / "nothing.yaml"
    result = server.create_request_draft(
        path=str(target), subject="Antrag nach dem LTranspG - Beispiel",
        body="Angaben zur Benutzungsordnung, geltende Fassung und alle Aenderungen.",
        publicbody_id=4929, law_wunsch_id=18, law_api_default_id=18)
    assert result["written"] is False
    assert not target.exists()


def test_rule5_dry_run_build_url_writes_no_body_file(tmp_path, submittable_draft):
    submittable_draft["submit_via"] = "web_form"
    submittable_draft["body"] = "Angaben zur Benutzungsordnung. " + "Angaben " * 520
    path = write(submittable_draft, tmp_path / "d.yaml")
    result = server.build_submit_url(path, dry_run=True)
    assert result["two_step"] is True
    assert result["body_file_written"] is False
    assert not drafts.body_file(path).exists()


# ==========================================================================
# rule 6 — local throttle bookkeeping
# ==========================================================================

def test_rule6_ledger_blocks_after_five_submissions_in_five_minutes(tmp_path):
    ledger = ThrottleLedger(path=tmp_path / "throttle.json")
    now = time.time()
    for _ in range(5):
        ledger.record(now=now)
    with pytest.raises(ThrottleExceeded, match="5min"):
        ledger.check(now=now)


@pytest.mark.parametrize("count,window,label", [
    (5, 5 * 60, "5min"),
    (6, 6 * 3600, "6h"),
    (10, 24 * 3600, "1d"),
    (20, 7 * 24 * 3600, "7d"),
])
def test_rule6_every_published_window_is_enforced(tmp_path, count, window, label):
    """5/5min, 6/6h, 10/24h, 20/7d — spread out so only the tested window is full."""
    ledger = ThrottleLedger(path=tmp_path / "throttle.json")
    now = time.time()
    spacing = window / (count + 1)
    for i in range(count):
        ledger.record(now=now - spacing * i)
    problems = ledger.violations(now=now)
    assert any(label in p for p in problems), problems


def test_rule6_submit_refuses_when_the_ledger_is_full(tmp_path, submittable_draft,
                                                      monkeypatch, no_network_writes):
    ledger_file = tmp_path / "throttle.json"
    monkeypatch.setattr("fds_mcp.config.throttle_path", lambda: ledger_file)
    full = ThrottleLedger(path=ledger_file)
    now = time.time()
    for _ in range(5):
        full.record(now=now)

    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(ThrottleExceeded):
        server.submit_request(path, "SEND-DEADBEEF", dry_run=True)
    assert no_network_writes.used is False


def test_rule6_old_entries_fall_out_of_the_window(tmp_path):
    ledger = ThrottleLedger(path=tmp_path / "throttle.json")
    now = time.time()
    for _ in range(5):
        ledger.record(now=now - 10 * 60)     # ten minutes ago
    assert ledger.violations(now=now) == []


def test_rule6_never_retries_it_raises(tmp_path):
    """The ledger must raise, not sleep-and-retry (terms of use B.1.4)."""
    source = inspect.getsource(ThrottleLedger)
    assert "sleep" not in source
    assert "retry" not in source.lower().replace("retrying", "")


# ==========================================================================
# rule 7 — the HTTP client is read-only unless explicitly unlocked
# ==========================================================================

@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def test_rule7_client_refuses_non_get_without_allow_write(method):
    client = FdsClient(token="irrelevant")
    with pytest.raises(WriteBlocked):
        client.request(method, "/request/")


def test_rule7_lowercase_method_is_also_blocked():
    with pytest.raises(WriteBlocked):
        FdsClient(token="irrelevant").request("post", "/request/")


def test_rule7_create_request_is_blocked_on_a_read_only_client():
    client = FdsClient(token="irrelevant")
    with pytest.raises(WriteBlocked):
        client.create_request(publicbody_ids=[4929], subject="Antrag nach dem LTranspG",
                              body="Angaben zur Benutzungsordnung der Gemeindehalle.")


def test_rule7_default_client_is_read_only():
    assert FdsClient().allow_write is False
    assert FdsClient.from_env().allow_write is False


def test_rule7_write_client_without_token_still_refuses():
    """allow_write alone is not enough — a token is required as well."""
    client = FdsClient(allow_write=True, token=None)
    with pytest.raises(AuthRequired):
        client.request("POST", "/request/")


def test_rule7_only_the_server_write_client_unlocks_writes():
    """No module other than server.write_client may hand out allow_write=True."""
    import pathlib

    package = pathlib.Path(server.__file__).parent
    offenders = []
    for file in package.glob("*.py"):
        for lineno, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            if "allow_write=True" in line and file.name not in ("client.py", "server.py"):
                offenders.append(f"{file.name}:{lineno}")
    assert offenders == [], offenders


# ==========================================================================
# the refusal reason has to reach the model, not just the log
# ==========================================================================

def test_gate_refusals_are_anticipated_tool_errors(tmp_path, submittable_draft):
    """The MCP SDK withholds the text of an unanticipated exception from the client.

    A gate refusal is the single most important message this server produces, so every
    domain error inherits from ToolError and keeps its own message.
    """
    from mcp.server.mcpserver.exceptions import ToolError

    from fds_mcp.auth import AuthError
    from fds_mcp.client import FdsError
    from fds_mcp.drafts import DraftError

    for exc_type in (SubmitBlocked, ThrottleExceeded, DraftError, FdsError, WriteBlocked,
                     AuthRequired, AuthError):
        assert issubclass(exc_type, ToolError), exc_type.__name__
        assert issubclass(exc_type, RuntimeError), exc_type.__name__

    submittable_draft["status"] = "draft"
    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(ToolError, match="Gate 1"):
        server.submit_request(path, "SEND-DEADBEEF", dry_run=True)


# ==========================================================================
# the gates must survive YAML type surprises and non-ASCII input
# ==========================================================================

def test_gate3_rejects_a_boolean_law_id(tmp_path, submittable_draft):
    """YAML 'true' is a bool, and True == 1 in Python — the equality must not pass."""
    submittable_draft["law"] = {"wunsch_id": 1, "wunsch_law_type": None,
                                "api_default_id": True}
    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(SubmitBlocked, match="Gate 3"):
        server.submit_request(path, "SEND-DEADBEEF", dry_run=True)


def test_a_boolean_publicbody_id_is_an_error_not_public_body_1(tmp_path,
                                                               submittable_draft):
    from fds_mcp import rules

    submittable_draft["publicbody"] = {"id": True, "ermittelt_ueber": "q"}
    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(SubmitBlocked, match="Gate 2"):
        server.submit_request(path, "SEND-DEADBEEF", dry_run=True)
    assert any(f.rule == "R05-publicbody-set"
               for f in rules.errors(rules.run_offline(submittable_draft)))


def test_gate4_reports_a_non_ascii_token_instead_of_crashing(tmp_path, submittable_draft):
    """secrets.compare_digest() raises TypeError on non-ASCII str — that would reach the
    client as 'Error executing tool', not as the gate's own refusal."""
    submittable_draft["confirmation_token"] = "SEND-ÄÖÜ"
    path = write(submittable_draft, tmp_path / "d.yaml")
    with pytest.raises(SubmitBlocked, match="Gate 4"):
        server.submit_request(path, "SEND-WRONG", dry_run=True)
    result = server.submit_request(path, "SEND-ÄÖÜ", dry_run=True)
    assert "confirmation" in result["gates_passed"]


def test_oauth_state_check_survives_a_non_ascii_state(monkeypatch, capsys):
    """A hostile callback URL must fail the state check with AuthError, not TypeError."""
    import io

    from fds_mcp import auth

    monkeypatch.setenv("FDS_MCP_CLIENT_ID", "test-client")
    monkeypatch.setattr("builtins.input",
                        lambda *_: "fragdenstaat://callback?code=abc&state=%C3%84%C3%96")
    with pytest.raises(auth.AuthError, match="state mismatch"):
        auth.login(manual=True, open_browser=False, out=io.StringIO())
