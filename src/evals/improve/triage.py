"""Triage: decide WHICH LAYER should fix each failure, before anyone writes a prompt rule.

  simulator_error  the simulated patient went off-script (trial invalid) - fix the scenario/simulator
  judge_error      the judge's evidence quote isn't in the transcript - fix the judge prompt
  code_bug         a guarantee that code should enforce was violated, or a tool crashed - fix Python
  spec_error       the scenario contradicts the seed data (caught at load time)
  prompt_fixable   agent behaviour within the rules - the only category sent to the reflector

Patching a code bug with a prompt rule would hide the bug, so only prompt_fixable goes forward.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ..harness import SuiteResult, TrialResult

Category = Literal["prompt_fixable", "code_bug", "simulator_error", "judge_error", "spec_error"]

# Checks whose failure means a code-level guarantee broke (the tools should make these impossible).
CODE_ENFORCED = {"db_no_collateral", "no_write_after_emergency", "no_other_patient_data"}
# ...except db_no_collateral when every touched patient WAS authorized in the session (verified patient, the
# dependent being acted for, or a linked dependent): the tools allowed it correctly and the agent chose the wrong
# authorized patient (e.g. booked the parent instead of the child). That is agent behaviour -> prompt_fixable.
# The check itself keeps its critical severity either way; only the routing changes.
_PATIENT_ID = re.compile(r"\bP\d{3}\b")


class TriageItem(BaseModel):
    scenario_id: str
    split: str
    trial: int
    check_id: str
    grader: str
    severity: str
    category: Category
    evidence: str


def _has_internal_error(run_dir: Path, trial: TrialResult) -> bool:
    path = run_dir / trial.trace_file
    if not path.exists():
        return False
    for line in path.read_text().splitlines():
        e = json.loads(line)
        if e.get("type") == "tool" and e.get("code") == "INTERNAL_ERROR":
            return True
    return False


def _final_state(run_dir: Path, trial: TrialResult) -> dict:
    path = run_dir / trial.trace_file
    state: dict = {}
    if path.exists():
        for line in path.read_text().splitlines():
            e = json.loads(line)
            if e.get("type") == "state":
                state = e.get("state") or {}
    return state


def session_authorized(run_dir: Path, trial: TrialResult) -> set[str]:
    """Patients the session was allowed to act for: the verified patient, acting_for, and linked dependents."""
    from clinic.clock import FixedClock
    from clinic.store import ClinicStore

    state = _final_state(run_dir, trial)
    me = state.get("verified_patient_id")
    if not me:
        return set()
    store = ClinicStore.load_seed(clock=FixedClock())
    return {me} | ({state["acting_for"]} if state.get("acting_for") else set()) | {p.id for p in store.dependents_of(me)}


def triage_trial(trial: TrialResult, run_dir: Path) -> list[TriageItem]:
    def item(check_id, grader, severity, cat, ev):
        return TriageItem(scenario_id=trial.scenario_id, split=trial.split, trial=trial.trial, check_id=check_id,
                          grader=grader, severity=severity, category=cat, evidence=ev[:300])
    if not trial.valid:
        return [item("simulator", "sim", "major", "simulator_error", trial.invalid_reason)]
    out = []
    crashed = _has_internal_error(run_dir, trial)
    for c in trial.checks:
        if not c.blocking:
            continue
        if c.grader == "judge" and not c.evidence_verified:
            cat = "judge_error"
        elif crashed:
            cat = "code_bug"
        elif c.id == "db_no_collateral":
            touched = set(_PATIENT_ID.findall(c.evidence))
            authorized = session_authorized(run_dir, trial)
            cat = "prompt_fixable" if touched and touched <= authorized else "code_bug"
        elif c.id in CODE_ENFORCED:
            cat = "code_bug"
        else:
            cat = "prompt_fixable"
        out.append(item(c.id, c.grader, c.severity, cat, c.evidence))
    return out


def triage_suite(suite: SuiteResult, run_dir: Path) -> list[TriageItem]:
    return [i for r in suite.scenarios.values() for t in r.trials for i in triage_trial(t, run_dir)]


def summarize(items: list[TriageItem]) -> dict[str, int]:
    return dict(Counter(i.category for i in items))
