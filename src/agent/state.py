"""Server-side session state.

Kept outside the transcript: tools read it directly, and the prompt renderer
shows it to the model every turn, so the model never has to *infer* who is verified,
what was offered, or whether a confirmation is pending.
"""
from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, Field

ActionKind = Literal["book", "cancel", "reschedule"]


class PendingAction(BaseModel):
    kind: ActionKind
    token: str
    patient_id: str                    # whose appointment this is (may be a dependent)
    slot_id: str | None = None         # book / reschedule target
    appointment_id: str | None = None  # cancel / reschedule source
    proposed_at_turn: int
    summary: str


class SessionState(BaseModel):
    session_id: str = "S0001"
    turn: int = 0                                  # +1 on every patient message (runner)
    verified_patient_id: str | None = None
    acting_for: str | None = None                  # linked dependent, if any
    offered_slots: list[str] = Field(default_factory=list)
    pending_action: PendingAction | None = None
    emergency: bool = False
    failed_verifications: int = 0
    locked: bool = False                           # verification lockout
    escalated: bool = False                        # handed to a human; automation stops
    completed: dict[str, dict[str, Any]] = Field(default_factory=dict)  # token -> result (idempotency)
    token_seq: int = 0

    def begin_patient_turn(self) -> None:
        self.turn += 1

    @property
    def subject_id(self) -> str | None:
        """The patient that reads/writes apply to."""
        return self.acting_for or self.verified_patient_id

    def new_token(self) -> str:
        # Deterministic (reproducible runs) but not sequential-looking. Guessing doesn't help anyway:
        # only the token of the *current* pending action is accepted.
        self.token_seq += 1
        digest = hashlib.sha256(f"{self.session_id}:{self.token_seq}".encode()).hexdigest()[:8]
        return f"CONF-{digest.upper()}"
