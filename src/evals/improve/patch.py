"""Typed playbook patches: schema, lint and apply.

A patch is ONE change to the playbook (add/edit a rule, edit a tool description, add an example).
Lint rejects anything that overfits to a scenario or could weaken safety. Python code - where
safety and authorization live - is not a valid target at all.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from agent.playbook import MAX_EXAMPLE_CHARS, MAX_RULE_CHARS, Example, Playbook, Rule
from agent.tool_schemas import PARAMETERS
from clinic.store import ClinicStore

PatchOp = Literal["add_rule", "edit_rule", "edit_tool_description", "add_example"]


class Patch(BaseModel):
    op: PatchOp
    target: str | None = None      # rule id (edit_rule) or tool name (edit_tool_description)
    text: str
    rationale: str = ""
    fixes: list[str] = Field(default_factory=list)
    evidence: str = ""

    def describe(self) -> str:
        tgt = f" {self.target}" if self.target else ""
        return f"{self.op}{tgt}: {self.text}"


class PatchError(Exception):
    pass


_WEAKENING = re.compile(
    r"\b(skip|bypass|disable|override)\b|without (verif|confirm|asking)|"
    r"(do not|don'?t|never|no need to) (verify|confirm|escalate|check identity|ask for confirmation)|"
    r"ignore (the )?(safety|guard|rules|system)|book (it )?directly|reveal|share (their|another|other)",
    re.IGNORECASE)
_SPECIFIC = [
    (re.compile(r"\b(P\d{3}|A\d{4}|PR\d|PR\d-\d{8}-\d{4}|CONF-[A-F0-9]+)\b"), "a record/slot/token ID"),
    (re.compile(r"\b20\d\d-\d\d-\d\d\b"), "a specific date"),
    (re.compile(r"\b\d{1,2}(st|nd|rd|th)? (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I), "a specific date"),
    (re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2}\b", re.I), "a specific date"),
    # an invented doctor name in an example becomes something the agent copies
    (re.compile(r"\bDr\.? ?[A-Z][a-z]+"), "a doctor's name (use a placeholder like <doctor>)"),
    # one specialty baked into a general rule breaks every other specialty
    (re.compile(r"\b(general medicine|general_medicine|dermatolog\w*|pediatric\w*|paediatric\w*)\b", re.I),
     "a specific specialty (rules must work for every specialty)"),
]


def _scenario_names(store: ClinicStore) -> set[str]:
    names = set()
    for p in store.patients.values():
        names.update(p.full_name.lower().split())
    for p in store.providers.values():
        names.update(w.lower() for w in p.name.replace("Dr.", "").split())
    return {n for n in names if len(n) > 2}


TOO_LONG = "too long"


def text_limit(patch: Patch) -> int | None:
    if patch.op in ("add_rule", "edit_rule"):
        return MAX_RULE_CHARS
    if patch.op == "add_example":
        return MAX_EXAMPLE_CHARS
    return None


def only_too_long(reasons: list[str]) -> bool:
    """True when length is the ONLY lint problem - then the loop asks the reflector to shorten it."""
    return bool(reasons) and all(r.startswith(TOO_LONG) for r in reasons)


def lint_patch(patch: Patch, pb: Playbook, store: ClinicStore) -> list[str]:
    reasons = []
    text = patch.text
    limit = text_limit(patch)
    if limit is not None and len(text) > limit:
        reasons.append(f"{TOO_LONG} ({len(text)} > {limit} chars)")
        patch = patch.model_copy(update={"text": text[:limit]})  # still check everything else below
    for rx, what in _SPECIFIC:
        if rx.search(text):
            reasons.append(f"mentions {what} (overfits to a scenario)")
    words = set(re.findall(r"[a-z]+", text.lower()))
    if hit := sorted(words & _scenario_names(store)):
        reasons.append(f"mentions scenario-specific names {hit}")
    if _WEAKENING.search(text):
        reasons.append("could weaken a safety behaviour")
    if not patch.fixes:
        reasons.append("does not say which failing check it fixes")
    if patch.op == "edit_rule" and patch.target not in {r.id for r in pb.rules}:
        reasons.append(f"edit_rule target {patch.target!r} does not exist")
    if patch.op == "edit_tool_description" and patch.target not in PARAMETERS:
        reasons.append(f"unknown tool {patch.target!r}")
    norm = " ".join(text.lower().split())
    if any(" ".join(r.text.lower().split()) == norm for r in pb.rules):
        reasons.append("duplicates an existing rule")
    try:
        apply_patch(pb, patch)
    except PatchError as e:
        reasons.append(str(e))
    return reasons


def apply_patch(pb: Playbook, patch: Patch) -> Playbook:
    data = pb.model_dump()
    if patch.op == "add_rule":
        data["rules"].append({"id": pb.next_rule_id(), "text": patch.text})
    elif patch.op == "edit_rule":
        for r in data["rules"]:
            if r["id"] == patch.target:
                r["text"] = patch.text
                break
        else:
            raise PatchError(f"no rule {patch.target}")
    elif patch.op == "edit_tool_description":
        if patch.target not in PARAMETERS:
            raise PatchError(f"unknown tool {patch.target}")
        data["tool_descriptions"][patch.target] = patch.text
    else:
        data["examples"].append({"id": f"E{len(pb.examples) + 1:02d}", "text": patch.text})
    data["version"] = pb.version + 1
    data["changelog"] = [*pb.changelog, f"v{pb.version + 1}: {patch.describe()[:200]}"]
    try:
        return Playbook.model_validate(data)
    except ValidationError as e:
        raise PatchError(f"playbook would be invalid: {e.errors()[0]['msg']}") from None


__all__ = ["Patch", "PatchError", "apply_patch", "lint_patch", "Rule", "Example"]
