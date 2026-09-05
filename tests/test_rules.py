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


def test_r12_narrowing_route_is_not_an_error(valid_request):
    """The narrowing route exists for exactly this case, so R12 must not block it.

    Gate 2 rejects every ERROR before gate 3 ever runs. As long as R12 fires at ERROR
    for a draft that declares ``narrow_after_submit`` with ``full_text``, the route
    implemented in check_law_gate() is unreachable through submit_request().
    """
    valid_request["submit_via"] = "api"
    valid_request["full_text"] = True
    valid_request["body"] = ("Sehr geehrte Damen und Herren,\n\nnach dem LTranspG "
                             "beantrage ich Zugang.\n\nMit freundlichen Gruessen")
    valid_request["law"]["narrow_after_submit"] = True
    findings = R.run_offline(valid_request)
    assert "R12-api-cannot-set-law" in ids(findings)
    assert "R12-api-cannot-set-law" not in err_ids(findings)


def test_r12_narrowing_without_full_text_stays_an_error(valid_request):
    """Without full_text the meta act's letter_start reaches the authority anyway."""
    valid_request["submit_via"] = "api"
    valid_request["full_text"] = False
    valid_request["law"]["narrow_after_submit"] = True
    assert "R12-api-cannot-set-law" in err_ids(R.run_offline(valid_request))


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
    assert len(R.OFFLINE_RULES) == 18
    assert len(R.LIVE_RULES) == 6


# --- the required elements of the finished letter ------------------------
#
# letter_start / letter_end of law 16 (LTranspG Rheinland-Pfalz), fetched from
# https://fragdenstaat.de/api/v1/law/16/ on 2026-09-05. Kept verbatim as a fixture so
# that these tests state a fact about the act rather than about the network.

LETTER_START_16 = "Antrag nach dem LTranspG\n\nGuten Tag, \n\nbitte senden Sie mir Folgendes zu:"
LETTER_END_16 = (
    "Dies ist ein Antrag auf Auskunft bzw. Einsicht nach § 2 Abs. 2 "
    "Landestransparenzgesetz (LTranspG). \n\n"
    "Sollte diese Anfrage wider Erwarten keine einfache Anfrage sein, bitte ich Sie "
    "darum, mich vorab über den voraussichtlichen Verwaltungsaufwand sowie die "
    "voraussichtlichen Kosten für die Akteneinsicht bzw. Aktenauskunft zu informieren.\n\n"
    "Mit Verweis auf § 12 Abs. 3 Satz 1 LTranspG möchte ich Sie bitten, unverzüglich "
    "über den Antrag zu entscheiden. Soweit Umweltinformationen betroffen sind, "
    "verweise ich auf § 12 Abs. 3 Satz 2 Nr. 2 LTranspG und bitte Sie, mir die erbetenen "
    "Informationen baldmöglichst, spätestens bis zum Ablauf eines Monats nach "
    "Antragszugang zugänglich zu machen. \n\n"
    "Sollten Sie für diesen Antrag nicht zuständig sein, bitte ich Sie, ihn an die "
    "zuständige Behörde weiterzuleiten und mich darüber zu unterrichten. Ich "
    "widerspreche ausdrücklich der Weitergabe meiner Daten an Dritte. \n\n"
    "Ich bitte Sie um eine Antwort in elektronischer Form (E-Mail) und möchte Sie um "
    "eine Empfangsbestätigung bitten. Vielen Dank für Ihre Mühe! \n\n"
    "Mit freundlichen Grüßen"
)


def missing_elements(text):
    return {f.rule for f in R.check_required_elements(text, source="test")}


def test_the_letter_frame_covers_everything_except_the_cost_cap():
    """The exact gap a real request fell through.

    Law 16's letter_end asks to be told the costs in advance but never names a ceiling
    and never falls back to free inspection on the premises. Reading the body alone
    would have shown nothing, because the body was not where the omission was.
    """
    whole = R.effective_text({"body": "Bitte die Benutzungsordnung.", "full_text": False},
                             LETTER_START_16, LETTER_END_16, "Firstname Lastname")
    missing = missing_elements(whole)
    assert "B-legal-basis" not in missing
    assert "B-cost-pre-notification" not in missing
    assert "B-deadline" not in missing
    assert "B-forwarding" not in missing
    assert "B-electronic-reply" not in missing
    assert "B-cost-cap" in missing, "the act's own frame contains no cost ceiling"


def test_a_cost_paragraph_closes_the_gap():
    body = ("Bitte die Benutzungsordnung.\n\n"
            "Zu den Kosten bitte ich um gebührenfreie Bearbeitung. Eine Bearbeitung, "
            "die Kosten über 50 Euro auslöst, bitte ich ohne meine ausdrückliche "
            "vorherige Zustimmung zu unterlassen.")
    whole = R.effective_text({"body": body, "full_text": False},
                             LETTER_START_16, LETTER_END_16, "Firstname Lastname")
    assert "B-cost-cap" not in missing_elements(whole)


def test_effective_text_adds_no_frame_with_full_text():
    text = R.effective_text({"body": "Nur mein Text.", "full_text": True},
                            LETTER_START_16, LETTER_END_16, "Firstname Lastname")
    assert "Guten Tag" not in text and "§ 2 Abs. 2" not in text
    assert text == "Nur mein Text.\nFirstname Lastname"


def test_r18_full_text_without_salutation_or_closing_is_an_error(valid_request):
    valid_request["full_text"] = True
    valid_request["body"] = "Bitte senden Sie mir die Benutzungsordnung."
    assert "R18-full-text-self-contained" in err_ids(R.run_offline(valid_request))


def test_r18_does_not_apply_without_full_text(valid_request):
    assert "R18-full-text-self-contained" not in ids(R.run_offline(valid_request))


def test_r18_accepts_a_complete_self_contained_text(valid_request):
    valid_request["full_text"] = True
    valid_request["body"] = (
        "Sehr geehrte Damen und Herren,\n\n"
        "hiermit beantrage ich nach dem Landestransparenzgesetz (LTranspG) Zugang zu "
        "der Benutzungsordnung.\n\n"
        "Ich bitte um gebührenfreie Bearbeitung; eine Bearbeitung über 50 Euro bitte "
        "ich ohne meine vorherige Zustimmung zu unterlassen. Bitte informieren Sie mich "
        "vorab über den Verwaltungsaufwand und die voraussichtlichen Kosten.\n\n"
        "Ich bitte gemäß § 12 Abs. 3 LTranspG um unverzügliche Entscheidung, spätestens "
        "binnen eines Monats.\n\n"
        "Sollten Sie nicht zuständig sein, bitte ich, den Antrag weiterzuleiten.\n\n"
        "Ich bitte um Antwort in elektronischer Form.\n\n"
        "Mit freundlichen Grüßen")
    findings = R.run_offline(valid_request)
    assert R.errors(findings) == [], "\n".join(str(f) for f in findings)


def test_a_missing_legal_basis_is_the_only_error_level_element():
    errors = {key for key, (level, _d, _p) in R.REQUIRED_ELEMENTS.items()
              if level is R.Level.ERROR}
    assert errors == {"legal-basis"}


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
def test_live_l06_reports_the_missing_cost_cap_for_the_example_draft(example_draft, client):
    """Against the live act, not against the fixture above — the frame may change."""
    findings = R.live_required_elements(example_draft, client)
    assert R.errors(findings) == [], "\n".join(str(f) for f in findings)
    assert "B-cost-cap" in {f.rule for f in findings}


@pytest.mark.live
def test_live_api_default_law_of_4929_is_the_meta_law(client):
    pb = client.get_authority(4929)
    laws = [entry for entry in pb["laws"] if isinstance(entry, dict)]
    assert R.applicable_law(laws)["id"] == 18
