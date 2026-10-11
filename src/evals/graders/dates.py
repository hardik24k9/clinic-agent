"""Deterministic date-consistency check (no LLM).

Models often pair a date with the wrong weekday, e.g. "Friday, 17 Oct" when 17 Oct 2026 is a Saturday.
A transcript-only judge tends to miss it. This check fails any trial where:

  1. the agent pairs a weekday with a date that falls on a different weekday; or
  2. the agent's stated weekday/date contradicts the tool's own `when` label for that date
     (e.g. tool says "Sat 17 Oct", agent says "Friday, 17 Oct"); or
  3. right after a search, the agent presents the results as "slots on <Weekday>" while none of the dates
     it just received fall on that weekday (weekday stated without a date).

Dates without a year use the clinic's fixed clock year; "Thursday the 22nd" (no month) resolves to the
first such day on or after the clinic's "today".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from clinic.clock import DEFAULT_NOW

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

_WD = r"(?P<wd>mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:r|rs|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\.?"
_MON = (r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
        r"sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s*(?P<year>20\d\d))?"
_SEP = r"[\s,(]*"
_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    rf"\b{_WD}{_SEP}{_DAY}\s+{_MON}{_YEAR}\b",                  # Friday, 17 Oct | Friday (13 Oct 2026 | Fri 16 Oct
    rf"\b{_WD}{_SEP}{_MON}\s+{_DAY}{_YEAR}\b",                  # Friday, October 16
    rf"\b{_DAY}\s+{_MON}{_YEAR}\s*[(,]\s*{_WD}\b",              # 17 Oct (Friday) | 16 October 2026, Friday
    rf"\b{_MON}\s+{_DAY}{_YEAR}\s*[(,]\s*{_WD}\b",              # October 16 (Friday)
    rf"\b(?P<iso>20\d\d-\d\d-\d\d)\s*[(,]\s*{_WD}\b",           # 2026-10-17 (Friday)
    rf"\b{_WD}\s+the\s+{_DAY}\b",                               # Thursday the 22nd
)]
_WEEKDAY_ONLY = re.compile(
    r"\b(?:slots?|appointments?|availability|times?)\b[^.\n|]{0,40}?\b(?:on|for)\s+(?:this\s+|next\s+|the\s+)?"
    r"(?P<wd>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b(?![\s,(]*(?:\d|the\s+\d|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))", re.IGNORECASE)
_WHEN = re.compile(r"^(?P<wd>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s+(?P<mon>[A-Za-z]{3})\s+(?P<year>\d{4})")


def normalize(text: str) -> str:
    """Model output uses narrow/no-break spaces and non-ASCII hyphens; make them plain."""
    for ch in (" ", " ", " ", " "):
        text = text.replace(ch, " ")
    for ch in ("‐", "‑", "‒", "–", "—"):
        text = text.replace(ch, "-")
    return text


def _weekday_index(token: str) -> int:
    t = token.lower().rstrip(".")
    return next(i for i, d in enumerate(DAYS) if d.startswith(t[:3]))


@dataclass(frozen=True)
class DateMention:
    text: str
    stated_weekday: int
    on: date


def find_date_mentions(text: str, today: date = DEFAULT_NOW.date()) -> list[DateMention]:
    text = normalize(text)
    out: list[DateMention] = []
    taken: list[tuple[int, int]] = []
    for rx in _PATTERNS:
        for m in rx.finditer(text):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue  # already parsed by a more specific pattern
            g = m.groupdict()
            try:
                if g.get("iso"):
                    on = date.fromisoformat(g["iso"])
                elif g.get("mon"):
                    on = date(int(g.get("year") or today.year), MONTHS.index(g["mon"][:3].lower()) + 1, int(g["day"]))
                else:  # "Thursday the 22nd": first such day-of-month on or after today
                    day, on = int(g["day"]), today
                    while on.day != day:
                        on += timedelta(days=1)
            except ValueError:
                continue  # e.g. "31 Sep": not a date, nothing to compare
            taken.append((m.start(), m.end()))
            out.append(DateMention(text=m.group(0).strip(" ,("), stated_weekday=_weekday_index(g["wd"]), on=on))
    return out


def _tool_labels(events: list[dict[str, Any]], upto_seq: int) -> dict[date, str]:
    """date -> the tool's own label (e.g. 'Sat 17 Oct 2026') from every slot/appointment the agent has seen."""
    labels: dict[date, str] = {}
    for e in events:
        if e["seq"] >= upto_seq or e["type"] != "tool" or not e.get("ok"):
            continue
        data = e.get("data") or {}
        for item in [*data.get("slots", []), *data.get("appointments", [])]:
            m = _WHEN.match(str(item.get("when", "")))
            if m:
                d = date(int(m["year"]), MONTHS.index(m["mon"][:3].lower()) + 1, int(m["day"]))
                labels[d] = f"{m['wd']} {m['day']} {m['mon']} {m['year']}"
    return labels


def _slot_date(slot: dict[str, Any]) -> date | None:
    m = _WHEN.match(str(slot.get("when", "")))
    return date(int(m["year"]), MONTHS.index(m["mon"][:3].lower()) + 1, int(m["day"])) if m else None


def date_problems(events: list[dict[str, Any]], today: date = DEFAULT_NOW.date()) -> list[str]:
    problems: list[str] = []
    for msg in (e for e in events if e["type"] == "agent"):
        labels = _tool_labels(events, msg["seq"])
        for dm in find_date_mentions(msg["text"], today):
            actual = DAYS[dm.on.weekday()]
            if dm.stated_weekday != dm.on.weekday():
                tool = f"; tool result says '{labels[dm.on]}'" if dm.on in labels else ""
                problems.append(f"turn {msg['turn']}: says '{dm.text}' but {dm.on.isoformat()} is a "
                                f"{actual.title()}{tool}")
        # weekday stated without a date, presenting results just returned by a search in this same turn
        searches = [e for e in events if e["type"] == "tool" and e["name"] == "search_slots" and e.get("ok")
                    and e["turn"] == msg["turn"] and e["seq"] < msg["seq"] and (e.get("data") or {}).get("slots")]
        if searches:
            got = {d.weekday() for d in (_slot_date(s) for s in searches[-1]["data"]["slots"]) if d}
            for m in _WEEKDAY_ONLY.finditer(normalize(msg["text"])):
                wd = _weekday_index(m["wd"])
                if got and wd not in got:
                    shown = sorted({labels[d] for d in labels if d.weekday() in got})[:2]
                    problems.append(f"turn {msg['turn']}: presents results as '{m.group(0).strip()}' but the search "
                                    f"returned {', '.join(shown) or 'other days'}")
    return problems
