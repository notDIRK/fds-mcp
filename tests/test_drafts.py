"""Draft store: YAML round-trip, state machine, confirmation token, sidecar files."""

from __future__ import annotations

import pytest

from fds_mcp import drafts, rules
from fds_mcp.drafts import DraftError


def test_new_draft_starts_in_draft_status():
    draft = drafts.new_draft(subject="Antrag nach dem LTranspG - Beispiel",
                             body="Angaben zur Benutzungsordnung der Gemeindehalle.",
                             publicbody_id=4929, law_wunsch_id=16,
                             law_api_default_id=18, law_wunsch_law_type="IFG")
    assert draft["status"] == "draft"


def test_new_draft_uses_the_placeholder_token():
    draft = drafts.new_draft(subject="Antrag nach dem LTranspG - Beispiel",
                             body="Angaben zur Benutzungsordnung der Gemeindehalle.",
                             publicbody_id=4929, law_wunsch_id=18)
    assert draft["confirmation_token"] == drafts.CONFIRMATION_PLACEHOLDER
    assert drafts.confirmation_token(draft) == ""


def test_new_draft_rejects_an_unknown_submit_route():
    with pytest.raises(DraftError):
        drafts.new_draft(subject="Antrag nach dem LTranspG - Beispiel",
                         body="Angaben zur Benutzungsordnung.",
                         publicbody_id=4929, law_wunsch_id=18, submit_via="brieftaube")


def test_a_fresh_draft_passes_the_offline_rules():
    draft = drafts.new_draft(
        subject="Antrag nach dem LTranspG - Benutzungsordnung Gemeindehalle",
        body="Angaben zur Benutzungsordnung der Gemeindehalle, geltende Fassung "
             "sowie alle Aenderungen seit 2025.",
        publicbody_id=4929, law_wunsch_id=18, law_api_default_id=18,
        ermittelt_ueber="/api/v1/publicbody/search/?q=Kaisersesch")
    assert rules.errors(rules.run_offline(draft)) == []


def test_save_and_load_round_trip(tmp_path):
    draft = drafts.new_draft(subject="Antrag nach dem LTranspG - Beispiel",
                             body="Angaben zur Benutzungsordnung.",
                             publicbody_id=4929, law_wunsch_id=18)
    path = drafts.save(draft, tmp_path / "sub" / "d.yaml")
    assert path.exists()
    assert drafts.load(path)["subject"] == draft["subject"]


def test_dump_carries_the_warning_header():
    draft = drafts.new_draft(subject="Antrag nach dem LTranspG - Beispiel",
                             body="Angaben zur Benutzungsordnung.",
                             publicbody_id=4929, law_wunsch_id=18)
    text = drafts.dump(draft)
    assert "NOTHING HAS BEEN SENT" in text
    assert 'status is "approved"' in text


def test_load_rejects_a_missing_file(tmp_path):
    with pytest.raises(DraftError):
        drafts.load(tmp_path / "nope.yaml")


def test_load_rejects_a_non_mapping(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(DraftError):
        drafts.load(path)


def test_set_status_rejects_an_unknown_state():
    with pytest.raises(DraftError):
        drafts.set_status({}, "halbfertig")


@pytest.mark.parametrize("status", rules.DRAFT_STATES)
def test_set_status_accepts_every_documented_state(status):
    assert drafts.set_status({}, status)["status"] == status


def test_confirmation_token_strips_whitespace():
    assert drafts.confirmation_token({"confirmation_token": "  SEND-1  "}) == "SEND-1"


def test_suggested_token_is_random_and_not_the_placeholder():
    a, b = drafts.suggest_confirmation_token(), drafts.suggest_confirmation_token()
    assert a != b
    assert drafts.CONFIRMATION_PLACEHOLDER not in a


def test_body_file_sits_next_to_the_draft(tmp_path):
    assert drafts.body_file(tmp_path / "x.yaml").name == "x.body.txt"


def test_prefill_too_long_uses_the_measured_limit():
    assert drafts.prefill_too_long("x" * (rules.MAX_PREFILL_URL_LENGTH + 1))
    assert not drafts.prefill_too_long("x" * rules.MAX_PREFILL_URL_LENGTH)
