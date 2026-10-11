"""Scenario spec (YAML) + validation against the seed, so a broken scenario is a spec_error, not an agent failure."""
from __future__ import annotations

from datetime import date as Date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from clinic.faults import FaultPlan
from clinic.store import ClinicStore

SCENARIO_DIR = Path(__file__).with_name("scenarios")


class DbExpect(BaseModel):
    outcome: Literal["booked", "cancelled", "moved", "none"]
    patient: str | None = None
    appointment: str | None = None
    specialty: str | None = None
    provider: str | None = None
    date: Date | None = None
    time: Literal["morning", "afternoon"] | None = None


class Expect(BaseModel):
    db: DbExpect
    trace: list[str] = Field(default_factory=list)
    judge: list[str] = Field(default_factory=list)


class Scenario(BaseModel):
    id: str
    split: Literal["train", "heldout"]
    category: Literal["happy_path", "safety", "identity", "robustness"]
    persona: str
    goal: str
    hidden_facts: dict[str, str] = Field(default_factory=dict)
    opening: str
    script: dict[int, str] = Field(default_factory=dict)  # patient turn -> fixed message
    faults: dict[str, str] = Field(default_factory=dict)
    max_turns: int = 10
    expect: Expect

    def validate_against(self, store: ClinicStore) -> list[str]:
        problems = []
        db = self.expect.db
        if db.patient and store.get_patient(db.patient) is None:
            problems.append(f"unknown patient {db.patient}")
        if db.appointment:
            appt = store.get_appointment(db.appointment)
            if appt is None:
                problems.append(f"unknown appointment {db.appointment}")
            elif db.patient and appt.patient_id != db.patient:
                problems.append(f"{db.appointment} does not belong to {db.patient}")
        if db.provider and store.get_provider(db.provider) is None:
            problems.append(f"unknown provider {db.provider}")
        try:
            FaultPlan(dict(self.faults))
        except ValueError as e:
            problems.append(str(e))
        if 1 in self.script:
            problems.append("turn 1 is the opening; script starts at turn 2")
        return problems


def load_scenarios(split: str = "all", only: list[str] | None = None) -> list[Scenario]:
    out = []
    for path in sorted(SCENARIO_DIR.glob("*/*.yaml")):
        s = Scenario.model_validate(yaml.safe_load(path.read_text()))
        if split != "all" and s.split != split:
            continue
        if only and s.id not in only:
            continue
        out.append(s)
    return out
