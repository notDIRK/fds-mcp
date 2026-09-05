"""Gate 3, second route: file under the meta act, then narrow to the specific one.

Measured 2026-09-05 against fragdenstaat.de:
  * ``law`` is writable via PATCH — patching a non-existent law URI returns 400 with a
    ``law`` key, which a read-only field would not do.
  * ``full_text: true`` reduces the mail to body + name; no statute is cited outside
    ``letter_start``/``letter_end``.
froide has a first-class feature for the second step: ``ConcreteLawForm`` exists to narrow
a request filed under a meta act down to one of its ``combined`` acts.
"""
import pytest

from fds_mcp import server
from fds_mcp.server import SubmitBlocked

META = {"id": 18, "meta": True, "priority": 3, "max_response_time": 1,
        "max_response_time_unit": "month_de",
        "combined": ["https://fragdenstaat.de/api/v1/law/16/",
                     "https://fragdenstaat.de/api/v1/law/3/"]}
SPECIFIC = {"id": 16, "meta": False, "priority": 3, "max_response_time": 1,
            "max_response_time_unit": "month_de", "combined": []}
# act 3 (VIG) IS in the meta act's combined set, but with a different deadline —
# that isolates the due_date check from the combined check.
IN_COMBINED_OTHER_DEADLINE = dict(SPECIFIC, id=3, max_response_time=5,
                                 max_response_time_unit="working_day")


class FakeClient:
    """Read client that serves the two laws above."""

    def __init__(self, laws=None):
        self.laws = {law["id"]: law for law in (laws or [META, SPECIFIC])}
        self.patched = []

    def get_law(self, law_id):
        return self.laws[law_id]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def narrowing_draft(**over):
    d = {
        "status": "approved",
        "confirmation_token": "ich-sende-das-wirklich",
        "public": True,
        "full_text": True,
        "subject": "Antrag nach dem LTranspG - Benutzungsordnung",
        "body": ("Sehr geehrte Damen und Herren,\n\n"
                 "hiermit beantrage ich nach dem Landestransparenzgesetz (LTranspG) "
                 "Zugang zu der Benutzungsordnung.\n\n"
                 "Ich bitte um gebuehrenfreie Bearbeitung; eine Bearbeitung ueber "
                 "50 Euro bitte ich ohne meine vorherige Zustimmung zu unterlassen. "
                 "Bitte informieren Sie mich vorab ueber den Verwaltungsaufwand und "
                 "die voraussichtlichen Kosten.\n\n"
                 "Ich bitte gemaess Paragraf 12 Abs. 3 LTranspG um unverzuegliche "
                 "Entscheidung, spaetestens binnen eines Monats.\n\n"
                 "Sollten Sie nicht zustaendig sein, bitte ich, den Antrag "
                 "weiterzuleiten.\n\n"
                 "Ich bitte um Antwort in elektronischer Form.\n\n"
                 "Mit freundlichen Gruessen"),
        "publicbody": {"id": 4929, "name": "Verbandsgemeindeverwaltung Kaisersesch",
                       "ermittelt_ueber": "/api/v1/publicbody/search/?q=Kaisersesch"},
        "law": {"wunsch_id": 16, "api_default_id": 18, "narrow_after_submit": True},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(d.get(k), dict):
            d[k] = {**d[k], **v}
        else:
            d[k] = v
    return d


def check(draft, client=None):
    return server.check_law_gate(draft, client or FakeClient())


# --- the route is allowed under all four conditions ----------------------

def test_narrowing_is_allowed_when_every_condition_holds():
    assert check(narrowing_draft())["route"] == "narrow_after_submit"


def test_equal_law_ids_still_take_the_direct_route():
    d = narrowing_draft(law={"wunsch_id": 18, "narrow_after_submit": False})
    assert check(d)["route"] == "direct"


# --- each condition on its own blocks ------------------------------------

def test_without_full_text_the_meta_act_wording_would_go_out():
    with pytest.raises(SubmitBlocked, match="full_text"):
        check(narrowing_draft(full_text=False))


def test_without_the_explicit_opt_in_the_route_stays_closed():
    with pytest.raises(SubmitBlocked, match="narrow_after_submit"):
        check(narrowing_draft(law={"narrow_after_submit": False}))


def test_a_law_outside_the_combined_set_is_refused():
    d = narrowing_draft(law={"wunsch_id": 150})
    with pytest.raises(SubmitBlocked, match="combined"):
        check(d, FakeClient([META, dict(SPECIFIC, id=150)]))


def test_a_different_response_time_is_refused_because_due_date_is_not_recalculated():
    """Act 3 is inside the combined set, so only the deadline check can reject it."""
    d = narrowing_draft(law={"wunsch_id": 3})
    with pytest.raises(SubmitBlocked, match="due_date"):
        check(d, FakeClient([META, IN_COMBINED_OTHER_DEADLINE]))


def test_an_act_outside_combined_is_refused_before_the_deadline_is_even_checked():
    d = narrowing_draft(law={"wunsch_id": 158})
    with pytest.raises(SubmitBlocked, match="combined"):
        check(d, FakeClient([META, dict(SPECIFIC, id=158)]))


def test_booleans_are_still_refused_as_law_ids():
    with pytest.raises(SubmitBlocked, match="boolean"):
        check(narrowing_draft(law={"wunsch_id": True}))


# --- R18 keeps guarding the self-contained text ---------------------------

def test_full_text_without_salutation_is_an_error_finding():
    from fds_mcp import rules
    d = narrowing_draft(body="Bitte senden Sie mir die Benutzungsordnung.")
    ids = {f.rule for f in rules.errors(rules.run_offline(d))}
    assert any(r.startswith("R18") for r in ids)
