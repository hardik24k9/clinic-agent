"""Replays real simulator outputs that showed role bleed."""
import json

import pytest

from evals.graders.sim_fidelity import grade_sim, is_valid
from evals.graders.trace import grade_trace
from evals.scenario import load_scenarios
from evals.simulator import PatientSimulator, clean_patient_message
from llm.fake import FakeLLM

NB = " "  # the narrow no-break spaces the model actually produced

REAL_BLEED = [  # (raw model output, what the patient should have said, ended?)
    (f"PR1‑20261016‑0900Yes, that works.Your appointment with Dr. Kavita{NB}Menon is confirmed for Friday 16{NB}Oct "
     "at 09:00 AM. If you need anything else, let us know.Thanks, goodbye <END>", "PR1‑20261016‑0900", False),
    (f"yesYour appointment with Dr.{NB}Arjun{NB}Shah on Wednesday 14 Oct is confirmed. Thank you! \n\nGoodbye. <END>",
     "yes", False),
    ("Aarav Sharma, 2016-04-09.We’d like Thursday, 15 October, any time that works.That works perfectly, thank you! <END>",
     "Aarav Sharma, 2016-04-09.", False),
    ("1984-03-02.Yes, Thursday works. Please schedule him for the earliest slot available.That’s fine.", "1984-03-02.", False),
    ("Actually, I need Friday 16 October, any time that day.Sure thing! Here are the GP slots we have:\n- 08:30",
     "Actually, I need Friday 16 October, any time that day.", False),
]


@pytest.mark.parametrize("raw,expected,ended", REAL_BLEED)
def test_sanitiser_keeps_only_the_patients_own_message(raw, expected, ended):
    msg, end = clean_patient_message(raw)
    assert msg == " ".join(expected.split()) and end is ended


@pytest.mark.parametrize("raw,expected,ended", [
    ("Yes, please confirm.", "Yes, please confirm.", False),
    ("Thanks, that's all. Goodbye <END>", "Thanks, that's all. Goodbye", True),
    ("PATIENT: Sanjay Gupta, 1 December 1988", "Sanjay Gupta, 1 December 1988", False),
    ("10:30 AM, please.\nASSISTANT: Great, booking that now.", "10:30 AM, please.", False),
    ("<END>", "", True),
])
def test_sanitiser_leaves_normal_messages_alone(raw, expected, ended):
    assert clean_patient_message(raw) == (expected, ended)


def test_simulator_sees_a_transcript_not_chat_turns():
    scn = next(s for s in load_scenarios() if s.id == "happy_book_gp")
    llm = FakeLLM(lambda m, t: "Sanjay Gupta, 1 December 1988")
    sim = PatientSimulator(llm, scn)
    sim.next_message(1, None)
    sim.next_message(2, "What's your full name and date of birth?")
    msgs = llm.calls[0]
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert "PATIENT: Hi, I'd like to see a GP" in msgs[1]["content"]
    assert "ASSISTANT: What's your full name" in msgs[1]["content"]


def _ev(*items):
    return [{"seq": i, **e} for i, e in enumerate(items)]


def test_role_bleed_that_slips_through_makes_the_trial_invalid():
    scn = next(s for s in load_scenarios() if s.id == "tool_timeout")
    ev = _ev({"type": "patient", "turn": 1, "text": scn.opening},
             {"type": "agent", "turn": 1, "text": "Name and date of birth?"},
             {"type": "patient", "turn": 2, "text": "Your appointment has been moved to Thursday 22 Oct."})
    valid, why = is_valid(grade_sim(ev, scn, []))
    assert not valid and "sim_no_role_bleed" in why


def test_reoffering_earlier_slots_after_slot_taken_counts_as_alternatives(store):
    """slot_taken_at_confirm t2: the agent re-listed slots from its earlier search instead of searching again."""
    slots = [{"slot_id": f"PR1-20261016-{h}", "when": f"Fri 16 Oct 2026, {h[:2]}:{h[2:]} AM IST"} for h in ("0930", "1000")]
    ev = _ev({"type": "tool", "turn": 3, "name": "search_slots", "args": {}, "ok": True, "code": None, "data": {"slots": slots}},
             {"type": "tool", "turn": 5, "name": "confirm", "args": {}, "ok": False, "code": "SLOT_TAKEN"},
             {"type": "agent", "turn": 5, "text": "Sorry, that slot was just taken. Next available: 09:30 – 10:00 AM, 10:00 AM."},
             {"type": "state", "turn": 5, "state": {}})
    c = {x.id: x for x in grade_trace(ev, store, ["alternatives_after_slot_taken"])}
    assert c["alternatives_after_slot_taken"].passed


def test_claiming_rescheduled_after_a_new_booking_is_a_false_success(store):
    """ho_reschedule_same_name t1: propose_booking + confirm, then 'has been rescheduled' -> two appointments."""
    ev = _ev({"type": "tool", "turn": 4, "name": "confirm", "args": {}, "ok": True, "code": None, "data": {"done": "book"}},
             {"type": "agent", "turn": 4, "text": "✅ Your dermatology appointment has been rescheduled to Friday 23 Oct."},
             {"type": "state", "turn": 4, "state": {"verified_patient_id": "P005"}})
    c = {x.id: x for x in grade_trace(ev, store, [])}
    assert c["no_false_success"].failed and "was 'book'" in c["no_false_success"].evidence
    ok = _ev({"type": "tool", "turn": 4, "name": "confirm", "args": {}, "ok": True, "code": None, "data": {"done": "reschedule"}},
             {"type": "agent", "turn": 4, "text": "Your appointment has been rescheduled."},
             {"type": "state", "turn": 4, "state": {"verified_patient_id": "P005"}})
    assert {x.id: x for x in grade_trace(ok, store, [])}["no_false_success"].passed


@pytest.mark.parametrize("text", [
    "Yes, please show me the earliest dermatology slot available in the week of 20 Oct.",  # real, from ho_no_slots
    "Is there a slot available on Friday?",
    "Has my appointment been booked yet?",
    "Can you check if Dr. Shah is free?",
])
def test_ordinary_patient_questions_are_not_flagged_as_role_bleed(text):
    scn = next(s for s in load_scenarios() if s.id == "ho_no_slots")
    ev = _ev({"type": "patient", "turn": 1, "text": scn.opening},
             {"type": "agent", "turn": 1, "text": "Name and date of birth?"},
             {"type": "patient", "turn": 2, "text": text})
    assert is_valid(grade_sim(ev, scn, []))[0], text
