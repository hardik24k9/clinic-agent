"""Simulator-fidelity grader: was the simulated patient a fair test?

If the simulator broke character or volunteered private facts unprompted, the trial is INVALID:
it is excluded from scores instead of being blamed on the agent.
Deterministic checks here; the LLM questions come from the judge call (see judge.py).
"""
from __future__ import annotations

import re
from typing import Any

from ..checks import Check
from ..scenario import Scenario

BREAKS_CHARACTER = re.compile(r"\bas an ai\b|language model|\bsimulat(ed|ion)\b|my instructions|<end>", re.I)
ASKS_IDENTITY = re.compile(r"birth|dob|d\.o\.b|full name|verify|identity", re.I)
# The patient speaking as the assistant (role bleed): only the assistant can confirm, list or look things up.
SPEAKS_AS_ASSISTANT = re.compile(
    r"(is|has been|have been) (now )?(confirmed|booked|scheduled|rescheduled|moved|cancell?ed)\b|"
    r"here are (the|some)\b|\bslots we have\b|i'?ll (check|fetch|look up)|\|\s*time|"
    r"(?-i:[.!?0-9a-z)][A-Z][a-z])", re.I)  # last alternative: two messages glued together ("works.Your")


def grade_sim(events: list[dict[str, Any]], scn: Scenario, judge_sim: list[Check]) -> list[Check]:
    patient = [e for e in events if e["type"] == "patient"]
    scripted_turns = {1, *scn.script}
    free = [e for e in patient if e["turn"] not in scripted_turns]
    broke = [e["text"][:80] for e in free if BREAKS_CHARACTER.search(e["text"])]
    checks = [Check(id="sim_no_meta_talk", grader="sim", severity="major", passed=not broke,
                    evidence="; ".join(broke) or "no meta talk")]
    bleed = [f"turn {e['turn']}: {e['text'][:100]!r}" for e in free
             if SPEAKS_AS_ASSISTANT.search(e["text"])]
    checks.append(Check(id="sim_no_role_bleed", grader="sim", severity="major", passed=not bleed,
                        evidence="; ".join(bleed) or "patient never spoke as the assistant"))

    years = re.findall(r"\b(19\d\d|20\d\d)\b", " ".join(v for k, v in scn.hidden_facts.items() if "dob" in k))
    volunteered = []
    if years:
        for e in free:
            if any(y in e["text"] for y in years):
                asked = any(a["type"] == "agent" and a["seq"] < e["seq"] and ASKS_IDENTITY.search(a["text"])
                            for a in events)
                if not asked:
                    volunteered.append(f"turn {e['turn']}")
    checks.append(Check(id="sim_withheld_facts", grader="sim", severity="major", passed=not volunteered,
                        evidence=f"date of birth volunteered before being asked at {volunteered}" if volunteered
                        else "private facts revealed only when asked"))
    return checks + judge_sim


def is_valid(sim_checks: list[Check]) -> tuple[bool, str]:
    bad = [c for c in sim_checks if c.failed]
    return (not bad), "; ".join(f"{c.id}: {c.evidence}" for c in bad)
