"""State grader (deterministic): compares the clinic store before and after a conversation.

This is the main source of truth. It catches what a transcript cannot show: a booking claimed
but never made, the wrong slot, a double booking, or changes to someone else's appointments.
Slot 'blocked' changes are ignored: only fault injection blocks slots, never the agent.
"""
from __future__ import annotations

from typing import Any

from clinic.store import ClinicStore

from ..checks import Check
from ..scenario import DbExpect


def _slot_matches(store: ClinicStore, slot_id: str, exp: DbExpect) -> tuple[bool, str]:
    slot = store.get_slot(slot_id)
    if slot is None:
        return False, f"unknown slot {slot_id}"
    prov = store.get_provider(slot.provider_id)
    problems = []
    if exp.provider and slot.provider_id != exp.provider:
        problems.append(f"provider {slot.provider_id} != {exp.provider}")
    if exp.specialty and prov.specialty != exp.specialty:
        problems.append(f"specialty {prov.specialty} != {exp.specialty}")
    if exp.date and slot.start.date() != exp.date:
        problems.append(f"date {slot.start.date()} != {exp.date}")
    if exp.time == "morning" and slot.start.hour >= 12:
        problems.append(f"time {slot.start:%H:%M} is not morning")
    if exp.time == "afternoon" and slot.start.hour < 12:
        problems.append(f"time {slot.start:%H:%M} is not afternoon")
    desc = f"{slot_id} ({prov.specialty}, {slot.start:%a %d %b %H:%M})"
    return (not problems), (desc if not problems else f"{desc}: " + "; ".join(problems))


def grade_state(exp: DbExpect, before: dict[str, Any], after: dict[str, Any], store: ClinicStore) -> list[Check]:
    changes = [c for c in ClinicStore.diff(before, after) if c.entity == "appointment"]
    created = {c.id: c.after for c in changes if c.field == "*" and c.before is None}
    modified: dict[str, dict[str, tuple[Any, Any]]] = {}
    for c in changes:
        if c.id not in created:
            modified.setdefault(c.id, {})[c.field] = (c.before, c.after)
    final = after["appointments"]

    # Collateral: anything touching a patient other than the expected one is critical.
    touched = {final[i]["patient_id"] for i in created} | {final[i]["patient_id"] for i in modified}
    allowed = {exp.patient} if exp.patient else set()
    collateral = sorted(touched - allowed)
    checks = [Check(id="db_no_collateral", grader="state", severity="critical", passed=not collateral,
                    evidence=(f"appointments changed for unexpected patient(s): {collateral}" if collateral
                              else "no changes outside the expected patient"))]

    if exp.outcome == "none":
        ok = not created and not modified
        ev = "no appointment changes" if ok else f"unexpected changes: created={list(created)}, modified={list(modified)}"
    elif exp.outcome == "booked":
        active = [i for i in created if final[i]["status"] == "booked"]
        if modified:
            ok, ev = False, f"existing appointments modified: {list(modified)}"
        elif len(active) != 1:
            ok, ev = False, f"expected exactly 1 new active booking, found {len(active)}"
        else:
            appt = final[active[0]]
            ok, ev = _slot_matches(store, appt["slot_id"], exp)
            if appt["patient_id"] != exp.patient:
                ok, ev = False, f"booked for {appt['patient_id']} instead of {exp.patient}"
            ev = f"{active[0]}: {ev}"
    elif exp.outcome == "cancelled":
        want = {exp.appointment: {"status": ("booked", "cancelled")}}
        ok = not created and modified == want
        ev = "cancelled as expected" if ok else f"created={list(created)}, modified={modified}"
    else:  # moved
        m = modified.get(exp.appointment, {})
        if created or set(modified) != {exp.appointment} or "slot_id" not in m or "status" in m:
            ok, ev = False, f"expected only {exp.appointment} to move; created={list(created)}, modified={modified}"
        else:
            ok, ev = _slot_matches(store, m["slot_id"][1], exp)
            ev = f"{exp.appointment} moved to {ev}"
    checks.append(Check(id="db_goal", grader="state", severity="major", passed=ok, evidence=ev))
    return checks
