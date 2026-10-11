"""System prompt = fixed base + versioned playbook + live session state (re-rendered every step)."""
from __future__ import annotations

import json

from clinic.clock import Clock
from clinic.store import ClinicStore

from .playbook import Playbook
from .state import SessionState

BASE = """You are the scheduling assistant for Sunrise Family Clinic, an outpatient clinic in India.
You help patients book, reschedule, cancel and check appointments using the tools provided.
You are not a clinician.

How the system works (enforced in code; tools refuse otherwise):
- Identity must be verified before any appointment information is shown or changed.
- Only slots returned by search_slots in this conversation can be booked.
- Every change is two steps: propose_* returns a summary and token; confirm(token) carries it out,
  and only after the patient has replied.
- Tool results are the truth. If a tool returns ok=false, nothing happened; read its hint.
- After escalate_to_human, take no further actions."""

EMERGENCY_NOTICE = """EMERGENCY MODE: the safety system detected a possible medical emergency or risk of self-harm.
Booking and changes are disabled. Now: tell the patient to call 112 (national emergency number) or go
to the nearest emergency department immediately; if self-harm, also urge them to reach someone now and
call 112. Then call escalate_to_human with urgency "emergency". Be brief and calm. Do not book."""


def render_state(state: SessionState, store: ClinicStore) -> str:
    def name(pid: str | None) -> str | None:
        p = store.get_patient(pid) if pid else None
        return p.full_name if p else None

    view = {
        "verified_patient": name(state.verified_patient_id),
        "acting_for": name(state.acting_for),
        "failed_verifications": state.failed_verifications,
        "verification_locked": state.locked,
        "offered_slot_count": len(state.offered_slots),
        "pending_action": ({"summary": state.pending_action.summary, "token": state.pending_action.token,
                            "proposed_in_turn": state.pending_action.proposed_at_turn}
                           if state.pending_action else None),
        "emergency": state.emergency,
        "escalated": state.escalated,
        "patient_turn": state.turn,
    }
    return json.dumps(view, indent=1)


CALENDAR_DAYS = 28


def render_calendar(today, days: int = CALENDAR_DAYS) -> str:
    """Weekday <-> date lookup table. Models are unreliable at computing weekdays (the single most common
    failure in the baseline: 'Thursday, October 19th' for a Monday, wrong years); a table removes the arithmetic."""
    from datetime import timedelta
    weeks = []
    for w in range(0, days, 7):
        weeks.append(", ".join(f"{(today + timedelta(d)):%a %d %b}" for d in range(w, min(w + 7, days))))
    return ("## Calendar (copy weekdays and dates from here; never compute them)\n"
            + "\n".join(f"- {line}" for line in weeks)
            + (f"\nAll dates above are in {today:%Y}." if (today + timedelta(days - 1)).year == today.year else "")
            + " Use YYYY-MM-DD in tool calls.")


def render_system_prompt(playbook: Playbook, state: SessionState, store: ClinicStore, clock: Clock) -> str:
    now = clock.now()
    providers = "; ".join(f"{p.id} {p.name} ({p.specialty})" for p in store.providers.values())
    parts = [
        BASE,
        f"Today is {now:%A %d %B %Y}, {now:%H:%M} IST.",
        render_calendar(now.date()),
        f"Providers: {providers}.",
        f"## Playbook rules (v{playbook.version})\n" + "\n".join(f"- {r.id}: {r.text}" for r in playbook.rules),
    ]
    if playbook.examples:
        parts.append("## Examples\n" + "\n\n".join(e.text for e in playbook.examples))
    if state.emergency:
        parts.append(EMERGENCY_NOTICE)
    parts.append(f"## Session state\n<session_state>\n{render_state(state, store)}\n</session_state>")
    return "\n\n".join(parts)
