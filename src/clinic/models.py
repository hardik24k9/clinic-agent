"""Domain models for the mock clinic. Synthetic data only."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class Patient(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    full_name: str
    dob: date
    phone: str
    guardian_id: str | None = None  # set on a minor: links the child to a parent


class Provider(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    specialty: str


class Slot(BaseModel):
    id: str
    provider_id: str
    start: datetime
    end: datetime
    blocked: bool = False  # unavailable for reasons outside the agent (full day, leave, taken elsewhere)


AppointmentStatus = Literal["booked", "cancelled"]


class Appointment(BaseModel):
    id: str
    patient_id: str
    slot_id: str
    status: AppointmentStatus = "booked"


class Change(BaseModel):
    """One field-level difference between two store snapshots."""
    model_config = ConfigDict(frozen=True)
    entity: Literal["appointment", "slot"]
    id: str
    field: str            # "*" means the whole entity was created or removed
    before: Any = None
    after: Any = None
