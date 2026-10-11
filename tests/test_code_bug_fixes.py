"""Code bugs surfaced by triage (category code_bug: fixed by a human in code, never by the loop).

1. verify_patient rejected '03-02-1984' -> the model retried the identical call until the step limit.
2. search_slots returned only the first 10 slots (all morning) -> afternoon slots were unreachable.
3. Wrong weekdays were the most common failure -> the prompt now carries a calendar table.
"""
from datetime import date

import pytest

from agent.prompt import render_calendar, render_system_prompt
from agent.playbook import load_playbook
from agent.tools import _parse_dob
from conftest import run, verified


@pytest.mark.parametrize("raw", ["1984-03-02", "2 March 1984", "2nd March 1984", "March 2, 1984", " 2 mar 1984 "])
def test_dob_formats_patients_type(raw):
    assert _parse_dob(raw) == date(1984, 3, 2)


@pytest.mark.parametrize("raw", ["25-03-1984", "25/3/1984", "25.03.1984", "25031984", "03-25-1984", "3/3/1984"])
def test_unambiguous_numeric_dobs_either_order(raw):
    assert _parse_dob(raw) in (date(1984, 3, 25), date(1984, 3, 3))


@pytest.mark.parametrize("raw", ["03-02-1984", "02/03/1984", "03021984"])
def test_ambiguous_numeric_dob_is_not_guessed(raw):
    assert _parse_dob(raw) is None


@pytest.mark.parametrize("raw", ["", "1984", "31-02-1984", "yesterday", "13/13/1984", None])
def test_unreadable_dob_is_rejected(raw):
    assert _parse_dob(raw) is None


def test_ambiguous_dob_asks_and_does_not_burn_an_attempt(ctx):
    """The exact tool_timeout t2 input: must not be guessed (3 Feb would be a failed attempt toward lockout)."""
    r = run(ctx, "verify_patient", full_name="Anita Rao", dob="03-02-1984")
    assert r.code == "INVALID_ARGUMENT" and "month name" in r.hint and ctx.state.failed_verifications == 0
    assert run(ctx, "verify_patient", full_name="Anita Rao", dob="2 March 1984").ok


def test_unreadable_dob_tells_model_not_to_retry(ctx):
    r = run(ctx, "verify_patient", full_name="Anita Rao", dob="sometime in 84")
    assert r.code == "INVALID_ARGUMENT" and "Do not retry" in r.hint
    assert ctx.state.failed_verifications == 0  # a parse error is not a failed identity attempt


def test_part_of_day_reaches_afternoon_slots(ctx):
    verified(ctx)
    first = run(ctx, "search_slots", date_from="2026-10-14", date_to="2026-10-14", specialty="general_medicine")
    assert first.data["more_available"] and "part_of_day" in first.hint
    pm = run(ctx, "search_slots", date_from="2026-10-14", date_to="2026-10-14", specialty="general_medicine",
             part_of_day="afternoon")
    assert pm.ok and pm.data["slots"]
    assert all(any(h in s["when"] for h in ("12:", "01:", "02:", "03:", "04:")) and "PM" in s["when"]
               for s in pm.data["slots"])
    # afternoon slots are now "offered", so they can be booked
    assert pm.data["slots"][0]["slot_id"] in ctx.state.offered_slots


def test_part_of_day_validates(ctx):
    verified(ctx)
    r = run(ctx, "search_slots", date_from="2026-10-14", date_to="2026-10-14", part_of_day="night")
    assert r.code == "INVALID_ARGUMENT"


def test_calendar_in_prompt_has_correct_weekdays(ctx):
    cal = render_calendar(date(2026, 10, 12))
    assert "Mon 19 Oct" in cal and "Fri 23 Oct" in cal and "Sun 01 Nov" in cal and "in 2026" in cal
    prompt = render_system_prompt(load_playbook(0), ctx.state, ctx.store, ctx.clock)
    assert "## Calendar" in prompt and "Wed 14 Oct" in prompt


def test_calendar_across_new_year_does_not_claim_one_year():
    cal = render_calendar(date(2026, 12, 20))
    assert "Fri 01 Jan" in cal and "All dates above are in" not in cal
