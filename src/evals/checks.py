"""Common result type for every grader."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Severity = Literal["critical", "major", "minor"]


class Check(BaseModel):
    id: str
    grader: Literal["state", "trace", "judge", "sim"]
    passed: bool | None          # None = could not be evaluated (e.g. judge unavailable)
    severity: Severity
    evidence: str = ""
    evidence_verified: bool = True   # judge quotes are checked against the transcript

    @property
    def failed(self) -> bool:
        return self.passed is False

    @property
    def blocking(self) -> bool:
        """A failed critical or major check fails the trial; minor ones are reported only."""
        return self.failed and self.severity in ("critical", "major")
