"""Deterministic fault injection.

Scenarios declare faults, e.g. {"confirm": "slot_taken_once", "search_slots": "timeout_once"}.
Faults are counted per tool call, so a scenario behaves identically on every run.

Assumption: a simulated TIMEOUT means the operation did NOT happen. (A real timeout is
ambiguous - the write may have landed - which is why confirm() is idempotent: a retry
with the same token can never create a second booking.)
"""
from __future__ import annotations

from dataclasses import dataclass, field

TIMEOUT = "TIMEOUT"
SLOT_TAKEN = "SLOT_TAKEN"

_MODES = {
    "timeout_once": TIMEOUT,
    "timeout_always": TIMEOUT,
    "slot_taken_once": SLOT_TAKEN,
}
_SLOT_TAKEN_TOOLS = {"propose_booking", "propose_reschedule", "confirm"}


@dataclass
class FiredFault:
    tool: str
    mode: str
    call_no: int


@dataclass
class FaultPlan:
    faults: dict[str, str] = field(default_factory=dict)
    fired: list[FiredFault] = field(default_factory=list)
    _calls: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for tool, mode in self.faults.items():
            if mode not in _MODES:
                raise ValueError(f"unknown fault mode {mode!r} for {tool}")
            if _MODES[mode] == SLOT_TAKEN and tool not in _SLOT_TAKEN_TOOLS:
                raise ValueError(f"{mode} only applies to {sorted(_SLOT_TAKEN_TOOLS)}, not {tool}")

    def check(self, tool: str) -> str | None:
        """Call once per tool invocation (after guards pass). Returns a fault code to apply, or None."""
        n = self._calls.get(tool, 0) + 1
        self._calls[tool] = n
        mode = self.faults.get(tool)
        if mode is None or (mode.endswith("_once") and n > 1):
            return None
        self.fired.append(FiredFault(tool=tool, mode=mode, call_no=n))
        return _MODES[mode]
