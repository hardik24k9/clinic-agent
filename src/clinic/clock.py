"""Deterministic clock. Every run sees the same 'now', so dates like "next Friday" have one answer."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Protocol

# India has no DST, so a fixed offset is exact and needs no tz database.
IST = timezone(timedelta(hours=5, minutes=30), "IST")

DEFAULT_NOW = datetime(2026, 10, 12, 9, 0, tzinfo=IST)  # Monday


class Clock(Protocol):
    def now(self) -> datetime: ...
    def today(self) -> date: ...


class FixedClock:
    def __init__(self, now: datetime = DEFAULT_NOW) -> None:
        if now.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._now = now

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()
