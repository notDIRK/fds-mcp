"""The rule set as a gate: every rule must fire on a deliberately broken draft.

Two levels:
  1. each rule is tested against a targeted mutation (the rule has to fire);
  2. the shipped example draft has to pass without ERROR findings.

Live tests (network) only run with ``pytest -m live``.
"""

import pytest

from fds_mcp import rules as R


def ids(findings):
    return {f.rule for f in findings}


def err_ids(findings):
    return {f.rule for f in R.errors(findings)}


# --- 1. every rule has to fire -------------------------------------------

def test_clean_draft_has_no_errors(valid_request):
    assert R.errors(R.run_offline(valid_request)) == []


def test_r01_subject_too_short(valid_request):
    valid_request["subject"] = "kurz"
    assert "R01-subject-length" in err_ids(R.run_offline(valid_request))


def test_r01_subject_too_long(valid_request):
    valid_request["subject"] = "A" * (R.MAX_SUBJECT_LENGTH + 1)
    assert "R01-subject-length" in err_ids(R.run_offline(valid_request))


def test_r01_subject_exactly_at_limit_is_ok(valid_request):
    valid_request["subject"] = "A" * R.MAX_SUBJECT_LENGTH
    assert "R01-subject-length" not in err_ids(R.run_offline(valid_request))


def test_r02_subject_without_sluggable_characters(valid_request):
    valid_request["subject"] = "??? !!! ??? !!!"
    assert "R02-subject-slug" in err_ids(R.run_offline(valid_request))


def test_r03_body_too_long(valid_request):
    valid_request["body"] = "x" * (R.MAX_BODY_LENGTH + 1)
    assert "R03-body-length" in err_ids(R.run_offline(valid_request))


def test_r03_body_close_to_limit_warns(valid_request):
    valid_request["body"] = "x" * int(R.MAX_BODY_LENGTH * 0.95)
    findings = R.run_offline(valid_request)
    assert "R03-body-length" in ids(findings)
    assert "R03-body-length" not in err_ids(findings)


def test_r04_placeholder_is_detected(valid_request):
    valid_request["body"] += "\n\nBitte Zeitraum " + R.PLACEHOLDER_MARKER + " ergaenzen."
    assert "R04-placeholder" in err_ids(R.run_offline(valid_request))


def test_r05_missing_authority(valid_request):
    valid_request["publicbody"] = {}
    assert "R05-publicbody-set" in err_ids(R.run_offline(valid_request))


def test_r05_unevidenced_responsibility_warns(valid_request):
    valid_request["publicbody"].pop("ermittelt_ueber")
    findings = R.run_offline(valid_request)
    assert "R05-publicbody-set" in ids(findings)
    assert "R05-publicbody-set" not in err_ids(findings)


def test_r06_email_in_body(valid_request):
    valid_request["body"] += "\nKontakt: max.mustermann@example.org"
    assert "R06-pii" in err_ids(R.run_offline(valid_request))


def test_r06_iban_in_body(valid_request):
    valid_request["body"] += "\nDE02 1203 0000 0000 2020 51"
    assert "R06-pii" in err_ids(R.run_offline(valid_request))


def test_r07_reference_without_colon(valid_request):
    valid_request["reference"] = "kaputt"
    assert "R07-reference-format" in err_ids(R.run_offline(valid_request))


def test_r07_correct_reference(valid_request):
    valid_request["reference"] = "campaign:example"
    assert "R07-reference-format" not in ids(R.run_offline(valid_request))


def test_r08_overlong_tag_warns(valid_request):
    valid_request["tags"] = ["t" * 150]
    assert "R08-tags" in ids(R.run_offline(valid_request))


def test_r09_public_must_be_explicit(valid_request):
    del valid_request["public"]
    assert "R09-public-explicit" in err_ids(R.run_offline(valid_request))


def test_r10_salutation_in_body_is_an_error(valid_request):
    valid_request["body"] = "Sehr geehrte Damen und Herren,\n\n" + valid_request["body"]
    assert "R10-no-salutation" in err_ids(R.run_offline(valid_request))


def test_r10_closing_formula_is_an_error(valid_request):
    valid_request["body"] += "\n\nMit freundlichen Gruessen"
    assert "R10-no-salutation" in err_ids(R.run_offline(valid_request))


def test_r10_does_not_apply_with_full_text(valid_request):
    valid_request["full_text"] = True
    valid_request["body"] = ("Sehr geehrte Damen und Herren,\n\nText.\n\n"
                             "Mit freundlichen Gruessen")
    assert "R10-no-salutation" not in ids(R.run_offline(valid_request))


def test_r11_without_legal_basis(valid_request):
    valid_request["law"] = {}
    assert "R11-law-consistency" in err_ids(R.run_offline(valid_request))


def test_r12_api_route_with_diverging_law_is_an_error(valid_request):
    valid_request["submit_via"] = "api"
    assert "R12-api-cannot-set-law" in err_ids(R.run_offline(valid_request))


def test_r12_web_form_route_is_only_a_hint(valid_request):
    valid_request["submit_via"] = "web_form"
    findings = R.run_offline(valid_request)
    assert "R12-api-cannot-set-law" in ids(findings)
    assert "R12-api-cannot-set-law" not in err_ids(findings)


def test_r13_unknown_status(valid_request):
    valid_request["status"] = "irgendwas"
    assert "R13-status-gate" in err_ids(R.run_offline(valid_request))


def test_r14_disallowed_attachment(valid_request):
    valid_request["attachments"] = [
        {"name": "brief.docx", "content_type": "application/msword"}]
    assert "R14-attachments" in err_ids(R.run_offline(valid_request))


def test_r14_pdf_is_fine(valid_request):
    valid_request["attachments"] = [
        {"name": "brief.pdf", "content_type": "application/pdf"}]
    assert "R14-attachments" not in ids(R.run_offline(valid_request))


def test_r15_unknown_submit_route(valid_request):
    valid_request["submit_via"] = "brieftaube"
    assert "R15-submit-via" in err_ids(R.run_offline(valid_request))


def test_r16_overlong_prefill_url_warns(valid_request):
    valid_request["body"] = "Angaben zur Benutzungsordnung " + "Angaben " * 700
    findings = R.run_offline(valid_request)
    assert "R16-prefill-url-length" in ids(findings)
    assert "R16-prefill-url-length" not in err_ids(findings)


def test_r16_short_draft_fits(valid_request):
    assert "R16-prefill-url-length" not in ids(R.run_offline(valid_request))


def test_r16_does_not_apply_to_the_api_route(valid_request):
    valid_request["submit_via"] = "api"
    valid_request["body"] = "x" * 4000
    assert "R16-prefill-url-length" not in ids(R.run_offline(valid_request))


def test_r17_duplicated_cost_clause_warns(valid_request):
    valid_request["body"] += ("\n\nBitte informieren Sie mich vorab ueber den "
                              "Verwaltungsaufwand und die voraussichtliche Kosten.")
    findings = R.run_offline(valid_request)
    assert "R17-boilerplate-duplication" in ids(findings)
    assert "R17-boilerplate-duplication" not in err_ids(findings)


def test_every_registered_rule_has_a_unique_id():
    registered = [rid for rid, _fn in R.OFFLINE_RULES] + [rid for rid, _fn in R.LIVE_RULES]
    assert len(registered) == len(set(registered))
    assert len(R.OFFLINE_RULES) == 17
    assert len(R.LIVE_RULES) == 5


# --- 2. the shipped example draft ----------------------------------------

def test_example_draft_has_no_errors(example_draft):
    findings = R.run_offline(example_draft)
    assert R.errors(findings) == [], "\n".join(str(f) for f in R.errors(findings))


def test_example_draft_is_not_submitted(example_draft):
    assert example_draft["status"] != "submitted"


def test_example_draft_goes_through_the_web_form(example_draft):
    """LTranspG (16) is not the API default (18) — the API route would be wrong."""
    assert example_draft["submit_via"] == "web_form"


def test_example_draft_has_no_salutation(example_draft):
    assert "R10-no-salutation" not in ids(R.run_offline(example_draft))


# --- 3. applicable_law reproduces froide's ordering -----------------------

def test_applicable_law_prefers_meta_then_priority():
    laws = [
        {"id": 16, "name": "LTranspG", "meta": False, "priority": 3},
        {"id": 18, "name": "LTranspG, VIG", "meta": True, "priority": 1},
        {"id": 150, "name": "LTranspG UIG", "meta": False, "priority": 4},
    ]
    assert R.applicable_law(laws)["id"] == 18


def test_applicable_law_falls_back_to_priority_without_meta():
    laws = [
        {"id": 16, "meta": False, "priority": 3},
        {"id": 150, "meta": False, "priority": 4},
    ]
    assert R.applicable_law(laws)["id"] == 150


# --- 4. live against the API ---------------------------------------------

@pytest.mark.live
def test_live_rules_on_the_example_draft(example_draft, client):
    findings = R.run_live(example_draft, client)
    assert R.errors(findings) == [], "\n".join(str(f) for f in R.errors(findings))


@pytest.mark.live
def test_live_ortsgemeinde_duengenheim_does_not_exist(client):
    """Evidences the responsibility assumption instead of guessing it."""
    assert client.search_authorities("Ortsgemeinde Düngenheim") == []


@pytest.mark.live
def test_live_duengenheim_belongs_to_vg_kaisersesch(client):
    regions = client.find_georegions("Düngenheim")
    assert len(regions) == 1
    assert regions[0]["part_of"].rstrip("/").endswith("/1899")

    pb = client.get_authority(4929)
    assert any(str(r).rstrip("/").endswith("/1899") for r in pb["regions"])


@pytest.mark.live
def test_live_api_default_law_of_4929_is_the_meta_law(client):
    pb = client.get_authority(4929)
    laws = [entry for entry in pb["laws"] if isinstance(entry, dict)]
    assert R.applicable_law(laws)["id"] == 18
