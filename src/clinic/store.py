"""In-memory clinic store.

The store does only data-integrity checks (a slot can't hold two active bookings).
Authorization (who may see or change what) deliberately lives in agent/tools.py,
so there is exactly one place to audit it.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .clock import IST, Clock, FixedClock
from .models import Appointment, Change, Patient, Provider, Slot

DEFAULT_SEED = Path(__file__).with_name("seed.json")


class StoreError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def normalize_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def _slot_id(provider_id: str, start: datetime) -> str:
    return f"{provider_id}-{start:%Y%m%d-%H%M}"


class ClinicStore:
    def __init__(
        self,
        patients: Iterable[Patient],
        providers: Iterable[Provider],
        slots: Iterable[Slot],
        appointments: Iterable[Appointment],
    ) -> None:
        self.patients = {p.id: p for p in patients}
        self.providers = {p.id: p for p in providers}
        self.slots = {s.id: s for s in slots}
        self.appointments = {a.id: a for a in appointments}
        self._next_appt = 1 + max((int(a[1:]) for a in self.appointments), default=0)

    # ---------- construction ----------
    @classmethod
    def load_seed(cls, path: Path = DEFAULT_SEED, clock: Clock | None = None) -> "ClinicStore":
        clock = clock or FixedClock()
        raw = json.loads(Path(path).read_text())
        providers = [Provider(id=p["id"], name=p["name"], specialty=p["specialty"]) for p in raw["providers"]]
        full = {(f["provider_id"], date.fromisoformat(f["date"])) for f in raw.get("full_days", [])}
        step = timedelta(minutes=raw["slot_minutes"])
        slots: list[Slot] = []
        for p in raw["providers"]:
            for offset in range(raw["horizon_days"]):
                day = clock.today() + timedelta(days=offset)
                if day.weekday() not in p["weekdays"]:
                    continue
                for start_s, end_s in p["hours"]:
                    t = datetime.combine(day, datetime.strptime(start_s, "%H:%M").time(), IST)
                    end = datetime.combine(day, datetime.strptime(end_s, "%H:%M").time(), IST)
                    while t + step <= end:
                        slots.append(Slot(id=_slot_id(p["id"], t), provider_id=p["id"], start=t,
                                          end=t + step, blocked=(p["id"], day) in full))
                        t += step
        store = cls(
            patients=[Patient(**p) for p in raw["patients"]],
            providers=providers,
            slots=slots,
            appointments=[Appointment(**a) for a in raw["appointments"]],
        )
        for a in store.appointments.values():  # seed sanity: every appointment points at a real slot
            if a.slot_id not in store.slots:
                raise ValueError(f"seed appointment {a.id} references unknown slot {a.slot_id}")
        return store

    # ---------- reads ----------
    def find_patient(self, full_name: str, dob: date) -> Patient | None:
        key = normalize_name(full_name)
        for p in self.patients.values():
            if normalize_name(p.full_name) == key and p.dob == dob:
                return p
        return None

    def get_patient(self, patient_id: str) -> Patient | None:
        return self.patients.get(patient_id)

    def dependents_of(self, guardian_id: str) -> list[Patient]:
        return [p for p in self.patients.values() if p.guardian_id == guardian_id]

    def get_provider(self, provider_id: str) -> Provider | None:
        return self.providers.get(provider_id)

    def get_slot(self, slot_id: str) -> Slot | None:
        return self.slots.get(slot_id)

    def get_appointment(self, appt_id: str) -> Appointment | None:
        return self.appointments.get(appt_id)

    def is_slot_free(self, slot_id: str) -> bool:
        slot = self.slots.get(slot_id)
        if slot is None or slot.blocked:
            return False
        return not any(a.slot_id == slot_id and a.status == "booked" for a in self.appointments.values())

    def free_slots(self, start: datetime, end: datetime, specialty: str | None = None,
                   provider_id: str | None = None) -> list[Slot]:
        out = []
        for s in self.slots.values():
            if not (start <= s.start < end):
                continue
            if provider_id and s.provider_id != provider_id:
                continue
            if specialty and self.providers[s.provider_id].specialty != specialty:
                continue
            if self.is_slot_free(s.id):
                out.append(s)
        return sorted(out, key=lambda s: (s.start, s.provider_id))

    def appointments_for(self, patient_ids: Iterable[str], include_cancelled: bool = False) -> list[Appointment]:
        ids = set(patient_ids)
        appts = [a for a in self.appointments.values()
                 if a.patient_id in ids and (include_cancelled or a.status == "booked")]
        return sorted(appts, key=lambda a: self.slots[a.slot_id].start)

    # ---------- writes (integrity checks only) ----------
    def create_appointment(self, patient_id: str, slot_id: str) -> Appointment:
        if patient_id not in self.patients:
            raise StoreError("NOT_FOUND", f"unknown patient {patient_id}")
        if not self.is_slot_free(slot_id):
            raise StoreError("SLOT_UNAVAILABLE", f"slot {slot_id} is not free")
        appt = Appointment(id=f"A{self._next_appt:04d}", patient_id=patient_id, slot_id=slot_id)
        self._next_appt += 1
        self.appointments[appt.id] = appt
        return appt

    def cancel_appointment(self, appt_id: str) -> Appointment:
        appt = self._active(appt_id)
        appt.status = "cancelled"
        return appt

    def move_appointment(self, appt_id: str, new_slot_id: str) -> Appointment:
        appt = self._active(appt_id)
        if not self.is_slot_free(new_slot_id):
            raise StoreError("SLOT_UNAVAILABLE", f"slot {new_slot_id} is not free")
        appt.slot_id = new_slot_id
        return appt

    def block_slot(self, slot_id: str) -> None:
        """Used by fault injection: the slot becomes unavailable outside the agent's control."""
        self.slots[slot_id].blocked = True

    def _active(self, appt_id: str) -> Appointment:
        appt = self.appointments.get(appt_id)
        if appt is None:
            raise StoreError("NOT_FOUND", f"unknown appointment {appt_id}")
        if appt.status != "booked":
            raise StoreError("ALREADY_CANCELLED", f"appointment {appt_id} is not active")
        return appt

    # ---------- snapshot / diff (what the state grader uses) ----------
    def snapshot(self) -> dict[str, Any]:
        """Plain-JSON deep copy of everything that can change during a conversation."""
        return deepcopy({
            "appointments": {a.id: a.model_dump(mode="json") for a in self.appointments.values()},
            "slots": {s.id: {"blocked": s.blocked} for s in self.slots.values()},
        })

    @staticmethod
    def diff(before: dict[str, Any], after: dict[str, Any]) -> list[Change]:
        changes: list[Change] = []
        for section, entity in (("appointments", "appointment"), ("slots", "slot")):
            b, a = before[section], after[section]
            for key in sorted(set(b) | set(a)):
                if key not in b:
                    changes.append(Change(entity=entity, id=key, field="*", before=None, after=a[key]))
                elif key not in a:
                    changes.append(Change(entity=entity, id=key, field="*", before=b[key], after=None))
                else:
                    for f in sorted(set(b[key]) | set(a[key])):
                        if b[key].get(f) != a[key].get(f):
                            changes.append(Change(entity=entity, id=key, field=f,
                                                  before=b[key].get(f), after=a[key].get(f)))
        return changes
