"""Agent tools: the ONLY place authorization is enforced.

Every tool treats the LLM as an untrusted caller. The model may *request* an action;
these functions decide whether it is allowed, using SessionState and the store - never
the conversation text. Guard order (first failure wins):

    ESCALATED -> EMERGENCY_LOCK (writes) -> LOCKED / NOT_VERIFIED -> ownership / linkage
    -> offered-slot / dates / token / new-patient-message -> injected fault -> act

Tools never raise into the agent loop: every outcome is a structured ToolResult the model
can recover from ({ok:false, code, hint}).
"""
from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from pydantic import BaseModel

from clinic.clock import IST, Clock, FixedClock
from clinic.faults import SLOT_TAKEN, TIMEOUT, FaultPlan
from clinic.models import Appointment, Slot
from clinic.store import ClinicStore, StoreError

from .state import PendingAction, SessionState

MAX_FAILED_VERIFICATIONS = 3
MAX_SEARCH_DAYS_AHEAD = 60
MAX_SLOTS_RETURNED = 10


# ---------------------------------------------------------------- result + context
class ToolResult(BaseModel):
    ok: bool
    code: str | None = None
    hint: str | None = None
    data: dict[str, Any] | None = None


def _ok(data: dict[str, Any] | None = None, hint: str | None = None) -> ToolResult:
    return ToolResult(ok=True, data=data, hint=hint)


def _err(code: str, hint: str) -> ToolResult:
    return ToolResult(ok=False, code=code, hint=hint)


@dataclass
class ToolContext:
    store: ClinicStore
    state: SessionState = field(default_factory=SessionState)
    clock: Clock = field(default_factory=FixedClock)
    faults: FaultPlan = field(default_factory=FaultPlan)


# ---------------------------------------------------------------- shared guards
def _gate(ctx: ToolContext, *, write: bool = False, needs_verified: bool = True) -> ToolResult | None:
    s = ctx.state
    if s.escalated:
        return _err("ESCALATED", "This conversation has been handed to clinic staff. Do not take further actions.")
    if write and s.emergency:
        return _err("EMERGENCY_LOCK", "A possible emergency was detected. Bookings and changes are disabled. "
                                      "Give emergency guidance and escalate to a human.")
    if needs_verified:
        if s.locked:
            return _err("LOCKED", "Identity verification is locked after repeated failures. Offer to escalate to staff.")
        if s.verified_patient_id is None:
            return _err("NOT_VERIFIED", "Verify the patient's full name and date of birth first.")
    return None


def _allowed_patient_ids(ctx: ToolContext) -> set[str]:
    """The verified patient plus their linked dependents. Nobody else, ever."""
    me = ctx.state.verified_patient_id
    if me is None:
        return set()
    return {me} | {p.id for p in ctx.store.dependents_of(me)}


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _parse_dob(value: str) -> date | None:
    """Dates of birth as patients actually type them: 1984-03-02, 25-03-1984, 25/3/1984, 25031984,
    2 March 1984, March 2 1984. A numeric date whose day and month could be swapped (both <= 12, e.g.
    03-02-1984) is AMBIGUOUS and returns None: guessing wrong would burn one of the patient's 3 identity
    attempts, so the agent must ask. This only parses; verification still needs exact name AND date."""
    if not isinstance(value, str):
        return None
    v = value.strip().lower().replace(",", " ")
    if (d := _parse_date(v)) is not None:
        return d
    try:
        m = re.fullmatch(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4})", v) or re.fullmatch(r"(\d{2})(\d{2})(\d{4})", v)
        if m:
            a, b, y = int(m[1]), int(m[2]), int(m[3])
            if a <= 12 and b <= 12 and a != b:
                return None  # ambiguous day/month
            return date(y, b, a) if a > 12 or a == b else date(y, a, b)
        if m := re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\s+(\d{4})", v):
            if (mon := _MONTHS.get(m[2][:3])) is not None:
                return date(int(m[3]), mon, int(m[1]))
        if m := re.fullmatch(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\d{4})", v):
            if (mon := _MONTHS.get(m[1][:3])) is not None:
                return date(int(m[3]), mon, int(m[2]))
    except ValueError:
        return None
    return None


def _slot_view(ctx: ToolContext, slot: Slot) -> dict[str, Any]:
    prov = ctx.store.get_provider(slot.provider_id)
    return {
        "slot_id": slot.id,
        "when": f"{slot.start:%a %d %b %Y, %I:%M %p} IST",
        "provider": prov.name,
        "specialty": prov.specialty,
    }


def _appt_view(ctx: ToolContext, appt: Appointment) -> dict[str, Any]:
    patient = ctx.store.get_patient(appt.patient_id)
    return {"appointment_id": appt.id, "patient": patient.full_name, "status": appt.status,
            **_slot_view(ctx, ctx.store.get_slot(appt.slot_id))}


def _new_pending(ctx: ToolContext, **kw: Any) -> PendingAction:
    pending = PendingAction(token=ctx.state.new_token(), proposed_at_turn=ctx.state.turn, **kw)
    ctx.state.pending_action = pending  # a new proposal replaces any older one
    return pending


def _drop_offer(ctx: ToolContext, slot_id: str) -> None:
    if slot_id in ctx.state.offered_slots:
        ctx.state.offered_slots.remove(slot_id)


# ---------------------------------------------------------------- identity
def verify_patient(ctx: ToolContext, full_name: str, dob: str) -> ToolResult:
    s = ctx.state
    if (g := _gate(ctx, needs_verified=False)) is not None:
        return g
    if s.locked:
        return _err("LOCKED", "Identity verification is locked after repeated failures. Offer to escalate to staff.")
    parsed = _parse_dob(dob)
    if parsed is None:
        return _err("INVALID_ARGUMENT", "Could not read the date of birth unambiguously (e.g. 03-02-1984 could "
                                        "be 3 Feb or 2 Mar). Ask the patient for the day and the month name, then "
                                        "pass YYYY-MM-DD. Do not retry the same value.")
    patient = ctx.store.find_patient(full_name, parsed)
    if s.verified_patient_id is not None:
        if patient is not None and patient.id == s.verified_patient_id:
            return _ok({"verified": True, "name": patient.full_name})
        return _err("ALREADY_VERIFIED", "A different patient is already verified in this conversation. "
                                        "For a linked child use act_for_dependent; otherwise escalate.")
    if patient is None:
        s.failed_verifications += 1
        if s.failed_verifications >= MAX_FAILED_VERIFICATIONS:
            s.locked = True
            return _err("LOCKED", "Too many failed attempts. Verification is locked. Offer to escalate to staff.")
        # Same message whichever field was wrong: never help someone probe names or birthdays.
        return _err("VERIFICATION_FAILED",
                    f"Those details did not match our records. Ask the patient to re-check their full name and "
                    f"date of birth. Attempts left: {MAX_FAILED_VERIFICATIONS - s.failed_verifications}.")
    s.verified_patient_id = patient.id
    return _ok({"verified": True, "name": patient.full_name})


def act_for_dependent(ctx: ToolContext, full_name: str, dob: str) -> ToolResult:
    if (g := _gate(ctx)) is not None:
        return g
    parsed = _parse_dob(dob)
    if parsed is None:
        return _err("INVALID_ARGUMENT", "Could not read the date of birth. Pass it as YYYY-MM-DD; ask if unclear.")
    person = ctx.store.find_patient(full_name, parsed)
    me = ctx.state.verified_patient_id
    if person is not None and person.id == me:
        ctx.state.acting_for = None
        ctx.state.pending_action = None
        return _ok({"acting_for": person.full_name, "self": True})
    # One error for "doesn't exist" and "exists but isn't yours": don't confirm who is a patient here.
    if person is None or person.guardian_id != me:
        return _err("NOT_LINKED", "This person is not linked to the verified patient's record. "
                                  "Do not act for them; offer to escalate to staff.")
    ctx.state.acting_for = person.id
    ctx.state.pending_action = None  # a pending action belonged to the previous subject
    return _ok({"acting_for": person.full_name, "self": False})


# ---------------------------------------------------------------- reads
PARTS_OF_DAY = {"morning": (time(0, 0), time(12, 0)), "afternoon": (time(12, 0), time(17, 0)),
                "evening": (time(17, 0), time(23, 59, 59))}


def search_slots(ctx: ToolContext, date_from: str, date_to: str, specialty: str | None = None,
                 provider_id: str | None = None, part_of_day: str | None = None) -> ToolResult:
    if (g := _gate(ctx)) is not None:
        return g
    d_from, d_to = _parse_date(date_from), _parse_date(date_to)
    if d_from is None or d_to is None:
        return _err("INVALID_ARGUMENT", "date_from and date_to must be YYYY-MM-DD.")
    today = ctx.clock.today()
    if d_from < today or d_to < d_from or d_to > today + timedelta(days=MAX_SEARCH_DAYS_AHEAD):
        return _err("INVALID_DATE", f"Search from today ({today.isoformat()}) up to "
                                    f"{MAX_SEARCH_DAYS_AHEAD} days ahead, with date_from <= date_to.")
    specialties = {p.specialty for p in ctx.store.providers.values()}
    if specialty is not None and specialty not in specialties:
        return _err("INVALID_ARGUMENT", f"Unknown specialty. Valid: {sorted(specialties)}.")
    if provider_id is not None and ctx.store.get_provider(provider_id) is None:
        return _err("INVALID_ARGUMENT", f"Unknown provider_id. Valid: {sorted(ctx.store.providers)}.")
    if part_of_day is not None and part_of_day not in PARTS_OF_DAY:
        return _err("INVALID_ARGUMENT", f"part_of_day must be one of {list(PARTS_OF_DAY)}.")
    if ctx.faults.check("search_slots") == TIMEOUT:
        return _err(TIMEOUT, "The scheduling system timed out. Nothing was changed. You may retry once.")
    start = datetime.combine(d_from, time.min, IST)
    end = datetime.combine(d_to + timedelta(days=1), time.min, IST)
    now = ctx.clock.now()
    free = [s for s in ctx.store.free_slots(start, end, specialty, provider_id) if s.start > now]
    if part_of_day is not None:  # otherwise later slots of a busy day are unreachable
        lo, hi = PARTS_OF_DAY[part_of_day]
        free = [s for s in free if lo <= s.start.astimezone(IST).time() < hi]
    shown = free[:MAX_SLOTS_RETURNED]
    for s in shown:
        if s.id not in ctx.state.offered_slots:
            ctx.state.offered_slots.append(s.id)
    more = len(free) > len(shown)
    hint = ("No free slots in that range. Offer other dates or providers." if not shown else
            "Only the first slots are shown. Do not say other times are unavailable: narrow the dates or use "
            "part_of_day (morning / afternoon / evening) to see them." if more else None)
    return _ok({"slots": [_slot_view(ctx, s) for s in shown], "more_available": more}, hint=hint)


def list_my_appointments(ctx: ToolContext) -> ToolResult:
    if (g := _gate(ctx)) is not None:
        return g
    appts = ctx.store.appointments_for(_allowed_patient_ids(ctx))
    return _ok({"appointments": [_appt_view(ctx, a) for a in appts]})


# ---------------------------------------------------------------- proposals (no writes yet)
def propose_booking(ctx: ToolContext, slot_id: str) -> ToolResult:
    if (g := _gate(ctx, write=True)) is not None:
        return g
    if slot_id not in ctx.state.offered_slots:
        return _err("SLOT_NOT_OFFERED", "Only slots returned by search_slots in this conversation can be booked.")
    if ctx.faults.check("propose_booking") == SLOT_TAKEN:
        ctx.store.block_slot(slot_id)
    if not ctx.store.is_slot_free(slot_id):
        _drop_offer(ctx, slot_id)
        return _err(SLOT_TAKEN, "That slot is no longer available. Offer other slots.")
    patient = ctx.store.get_patient(ctx.state.subject_id)
    view = _slot_view(ctx, ctx.store.get_slot(slot_id))
    summary = f"Book {patient.full_name} with {view['provider']} ({view['specialty']}) on {view['when']}"
    p = _new_pending(ctx, kind="book", patient_id=patient.id, slot_id=slot_id, summary=summary)
    return _ok({"token": p.token, "summary": summary},
               hint="Read the summary to the patient and wait for an explicit yes before calling confirm.")


def _owned_active(ctx: ToolContext, appointment_id: str) -> Appointment | None:
    appt = ctx.store.get_appointment(appointment_id)
    if appt is None or appt.status != "booked" or appt.patient_id not in _allowed_patient_ids(ctx):
        return None
    return appt


def propose_cancel(ctx: ToolContext, appointment_id: str) -> ToolResult:
    if (g := _gate(ctx, write=True)) is not None:
        return g
    appt = _owned_active(ctx, appointment_id)
    if appt is None:  # same answer for "doesn't exist" and "not yours"
        return _err("APPOINTMENT_NOT_FOUND", "No active appointment with that ID for this patient. "
                                             "Use list_my_appointments.")
    view = _appt_view(ctx, appt)
    summary = f"Cancel {view['patient']}'s appointment with {view['provider']} on {view['when']}"
    p = _new_pending(ctx, kind="cancel", patient_id=appt.patient_id, appointment_id=appt.id, summary=summary)
    return _ok({"token": p.token, "summary": summary},
               hint="Read the summary to the patient and wait for an explicit yes before calling confirm.")


def propose_reschedule(ctx: ToolContext, appointment_id: str, new_slot_id: str) -> ToolResult:
    if (g := _gate(ctx, write=True)) is not None:
        return g
    appt = _owned_active(ctx, appointment_id)
    if appt is None:
        return _err("APPOINTMENT_NOT_FOUND", "No active appointment with that ID for this patient. "
                                             "Use list_my_appointments.")
    if new_slot_id not in ctx.state.offered_slots:
        return _err("SLOT_NOT_OFFERED", "Only slots returned by search_slots in this conversation can be used.")
    if ctx.faults.check("propose_reschedule") == SLOT_TAKEN:
        ctx.store.block_slot(new_slot_id)
    if not ctx.store.is_slot_free(new_slot_id):
        _drop_offer(ctx, new_slot_id)
        return _err(SLOT_TAKEN, "That slot is no longer available. Offer other slots.")
    old, new = _appt_view(ctx, appt), _slot_view(ctx, ctx.store.get_slot(new_slot_id))
    summary = (f"Move {old['patient']}'s appointment from {old['when']} ({old['provider']}) "
               f"to {new['when']} ({new['provider']})")
    p = _new_pending(ctx, kind="reschedule", patient_id=appt.patient_id, appointment_id=appt.id,
                     slot_id=new_slot_id, summary=summary)
    return _ok({"token": p.token, "summary": summary},
               hint="Read the summary to the patient and wait for an explicit yes before calling confirm.")


# ---------------------------------------------------------------- the only write
def confirm(ctx: ToolContext, token: str) -> ToolResult:
    s = ctx.state
    if (g := _gate(ctx, write=True)) is not None:
        return g
    if token in s.completed:  # idempotent: a retry returns the first result, never acts twice
        return _ok({**s.completed[token], "already_confirmed": True})
    pending = s.pending_action
    if pending is None or pending.token != token:
        return _err("TOKEN_INVALID", "No pending action matches this token. Propose the action again.")
    if s.turn <= pending.proposed_at_turn:
        return _err("NO_NEW_PATIENT_MESSAGE", "Read the summary to the patient and wait for their reply "
                                              "before confirming.")
    fault = ctx.faults.check("confirm")
    if fault == TIMEOUT:
        return _err(TIMEOUT, "The scheduling system timed out. Nothing was changed. "
                             "Do not tell the patient it succeeded. You may retry confirm once with the same token.")
    if fault == SLOT_TAKEN and pending.slot_id:
        ctx.store.block_slot(pending.slot_id)
    try:
        if pending.kind == "book":
            appt = ctx.store.create_appointment(pending.patient_id, pending.slot_id)
        elif pending.kind == "cancel":
            appt = ctx.store.cancel_appointment(pending.appointment_id)
        else:
            appt = ctx.store.move_appointment(pending.appointment_id, pending.slot_id)
    except StoreError as e:
        s.pending_action = None
        if e.code == "SLOT_UNAVAILABLE":
            _drop_offer(ctx, pending.slot_id)
            return _err(SLOT_TAKEN, "That slot was just taken by someone else. Nothing was booked or changed. "
                                    "Tell the patient and offer other slots.")
        return _err("APPOINTMENT_NOT_FOUND", "That appointment is no longer active. Nothing was changed.")
    result = {"done": pending.kind, "summary": pending.summary, **_appt_view(ctx, appt)}
    s.completed[token] = result
    s.pending_action = None
    return _ok(result)


# ---------------------------------------------------------------- escalation
def escalate_to_human(ctx: ToolContext, reason: str, urgency: str = "routine") -> ToolResult:
    if urgency not in {"routine", "urgent", "emergency"}:
        return _err("INVALID_ARGUMENT", "urgency must be routine, urgent or emergency.")
    s = ctx.state
    first = not s.escalated
    s.escalated = True
    s.pending_action = None
    return _ok({"escalated": True, "ticket": f"ESC-{s.session_id}", "urgency": urgency, "first": first},
               hint="Tell the patient a staff member will follow up. Take no further actions.")


# ---------------------------------------------------------------- dispatch
TOOLS: dict[str, Callable[..., ToolResult]] = {
    f.__name__: f for f in (
        verify_patient, act_for_dependent, search_slots, list_my_appointments,
        propose_booking, propose_cancel, propose_reschedule, confirm, escalate_to_human,
    )
}


def call_tool(ctx: ToolContext, name: str, args: dict[str, Any] | None) -> ToolResult:
    """Entry point for the agent loop. Unknown tools and bad arguments become structured errors."""
    fn = TOOLS.get(name)
    if fn is None:
        return _err("UNKNOWN_TOOL", f"No tool named {name!r}. Available: {sorted(TOOLS)}.")
    try:
        bound = inspect.signature(fn).bind(ctx, **(args or {}))
    except TypeError as e:
        return _err("INVALID_ARGUMENT", f"Bad arguments for {name}: {e}")
    try:
        return fn(*bound.args, **bound.kwargs)
    except Exception as e:  # noqa: BLE001 - a malformed call must never crash the conversation
        return _err("INTERNAL_ERROR", f"{name} failed ({type(e).__name__}). Nothing was changed; "
                                      "check the arguments or escalate.")
