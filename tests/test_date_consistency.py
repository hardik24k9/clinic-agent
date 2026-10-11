"""Deterministic date-consistency check. Examples are taken from real eval transcripts."""
from datetime import date

import pytest

from evals.graders.dates import date_problems, find_date_mentions
from evals.graders.trace import DEFAULT_CHECKS, grade_trace

NB = " "


def _ev(*items):
    return [{"seq": i, **e} for i, e in enumerate(items)]


def _search(turn, *whens, ok=True):
    slots = [{"slot_id": f"PR1-{w[1]}-0900", "when": w[0]} for w in whens]
    return {"type": "tool", "turn": turn, "name": "search_slots", "args": {}, "ok": ok, "code": None,
            "data": {"slots": slots}}


SAT17 = ("Sat 17 Oct 2026, 10:00 AM IST", "20261017")
TUE13 = ("Tue 13 Oct 2026, 09:00 AM IST", "20261013")
FRI16 = ("Fri 16 Oct 2026, 09:00 AM IST", "20261016")


# ---------------------------------------------------------------- the exact baseline failures
def test_next_friday_t1_friday_17_oct_is_a_saturday(store):
    """next_friday_ambiguity t1: searched 2026-10-17 for Dr. Shah, called it Friday."""
    ev = _ev(_search(2, SAT17),
             {"type": "agent", "turn": 2, "text": f"Here are the morning slots with Dr.{NB}Arjun Shah on Friday, 17{NB}Oct 2026:"})
    [p] = date_problems(ev)
    assert "Friday, 17 Oct 2026" in p and "2026-10-17 is a Saturday" in p and "tool result says 'Sat 17 Oct 2026'" in p


def test_slot_taken_t2_friday_17_oct_without_a_matching_search(store):
    """slot_taken_at_confirm t2: searched Tue 13 Oct, then said it would look at 'Friday, 17 Oct 2026'."""
    ev = _ev(_search(2, TUE13),
             {"type": "agent", "turn": 2, "text": "Let’s look for GP (general medicine) slots on Friday, 17 Oct 2026. "
                                                  "I’ll fetch the available slots for that day."})
    assert any("2026-10-17 is a Saturday" in p for p in date_problems(ev))


def test_wrong_dob_t2_friday_13_oct_is_a_tuesday_even_in_a_passing_trial(store):
    """wrong_dob_then_corrected t2 PASSED in the baseline; the check must still fail it."""
    ev = _ev(_search(3, TUE13),
             {"type": "agent", "turn": 3,
              "text": "Here are the general‑medicine slots available on Friday (13 Oct 2026) in the morning:"},
             {"type": "state", "turn": 3, "state": {"verified_patient_id": "P003"}})
    c = {x.id: x for x in grade_trace(ev, store, [])}["date_consistency"]
    assert c.failed and c.blocking and c.severity == "major"
    assert "Friday (13 Oct 2026" in c.evidence and "Tuesday" in c.evidence and "Tue 13 Oct 2026" in c.evidence


# ---------------------------------------------------------------- correct statements pass
@pytest.mark.parametrize("text", [
    f"Here are the slots on Friday, 16{NB}Oct 2026:",
    "Proposed: Fri 16 Oct 2026, 09:00 AM IST with Dr. Kavita Menon.",
    "Your appointment is on Tuesday 20 October.",
    "Moving it from Tue 20 Oct 2026 to Thu 22 Oct 2026.",
    "Do you mean Friday 16 Oct or Friday 23 Oct?",
    "I can do 16 October (Friday) or 23 October (Friday).",
    "That's Thursday the 22nd.",
    "No slots on Tuesday (13 Oct) or Thursday (15 Oct).",          # real ho_no_slots wording
    "Which day suits you - Monday to Friday?",                       # no date: nothing to check
])
def test_correct_or_dateless_statements_pass(text):
    assert date_problems(_ev({"type": "agent", "turn": 1, "text": text})) == []


@pytest.mark.parametrize("text,stated,actual", [
    ("Booked for Thursday, 16 Oct.", "thursday", date(2026, 10, 16)),
    ("See you on Wed, October 23.", "wednesday", date(2026, 10, 23)),
    ("Slot: 2026-10-17 (Friday)", "friday", date(2026, 10, 17)),
    ("23 October (Thursday) works.", "thursday", date(2026, 10, 23)),
    ("That's Friday the 22nd.", "friday", date(2026, 10, 22)),
])
def test_other_formats_are_parsed_and_wrong_weekdays_flagged(text, stated, actual):
    [m] = find_date_mentions(text)
    assert m.on == actual
    assert len(date_problems(_ev({"type": "agent", "turn": 1, "text": text}))) == 1


# ---------------------------------------------------------------- weekday without a date vs the tool's results
def test_weekday_only_claim_that_contradicts_the_search_just_made():
    ev = _ev(_search(3, TUE13), {"type": "agent", "turn": 3, "text": "Here are the free slots for Friday:\n- 09:00 AM"})
    [p] = date_problems(ev)
    assert "slots for Friday" in p and "Tue 13 Oct 2026" in p


def test_weekday_only_claim_matching_the_search_passes_and_needs_a_search():
    ok = _ev(_search(3, FRI16), {"type": "agent", "turn": 3, "text": "Here are the free slots for Friday:"})
    assert date_problems(ok) == []
    no_search = _ev({"type": "agent", "turn": 1, "text": "I'll look for slots on Friday."})
    assert date_problems(no_search) == []


def test_invalid_dates_are_ignored_not_crashing():
    assert date_problems(_ev({"type": "agent", "turn": 1, "text": "Friday, 31 Sep is not a date."})) == []


def test_check_runs_on_every_scenario_by_default():
    assert "date_consistency" in DEFAULT_CHECKS
