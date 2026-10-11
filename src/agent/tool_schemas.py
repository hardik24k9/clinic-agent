"""JSON schemas for tool parameters (fixed, code-owned). Descriptions come from the playbook."""
from __future__ import annotations

from typing import Any

from .playbook import Playbook

_DATE = {"type": "string", "description": "YYYY-MM-DD"}

PARAMETERS: dict[str, dict[str, Any]] = {
    "verify_patient": {"type": "object", "properties": {"full_name": {"type": "string"}, "dob": _DATE},
                       "required": ["full_name", "dob"]},
    "act_for_dependent": {"type": "object", "properties": {"full_name": {"type": "string"}, "dob": _DATE},
                          "required": ["full_name", "dob"]},
    "search_slots": {"type": "object", "properties": {
        "date_from": _DATE, "date_to": _DATE,
        "specialty": {"type": "string", "enum": ["general_medicine", "dermatology", "pediatrics"]},
        "provider_id": {"type": "string"},
        "part_of_day": {"type": "string", "enum": ["morning", "afternoon", "evening"],
                        "description": "Only slots before 12:00 / 12:00-17:00 / after 17:00."}},
        "required": ["date_from", "date_to"]},
    "list_my_appointments": {"type": "object", "properties": {}},
    "propose_booking": {"type": "object", "properties": {"slot_id": {"type": "string"}}, "required": ["slot_id"]},
    "propose_cancel": {"type": "object", "properties": {"appointment_id": {"type": "string"}},
                       "required": ["appointment_id"]},
    "propose_reschedule": {"type": "object", "properties": {"appointment_id": {"type": "string"},
                                                             "new_slot_id": {"type": "string"}},
                           "required": ["appointment_id", "new_slot_id"]},
    "confirm": {"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]},
    "escalate_to_human": {"type": "object", "properties": {
        "reason": {"type": "string"},
        "urgency": {"type": "string", "enum": ["routine", "urgent", "emergency"]}}, "required": ["reason"]},
}


def openai_tools(playbook: Playbook) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": name, "description": playbook.tool_descriptions.get(name, name),
                                              "parameters": params}}
            for name, params in PARAMETERS.items()]
