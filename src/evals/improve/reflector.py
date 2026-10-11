"""Reflector: turns prompt-fixable TRAIN failures into exactly N typed candidate patches.

Held-out scenarios are never shown to the reflector (enforced here, and tested).
The output is structured JSON validated against the Patch schema; malformed candidates are
recorded as rejected rather than silently dropped.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import yaml
from pydantic import ValidationError

from agent.playbook import Playbook
from agent.trace import load_trace
from llm.client import ChatModel, LLMError, QuotaExhausted, extract_json

from ..graders.judge import format_transcript
from ..harness import SuiteResult
from .patch import Patch, text_limit
from .triage import TriageItem

N_CANDIDATES = 3

REFLECT_PROMPT = """You improve the behaviour of a clinic scheduling assistant by editing its PLAYBOOK
(behavioural rules, tool descriptions, short examples). Safety and authorization are enforced in code
and are NOT yours to change; you cannot weaken them.

Current playbook (YAML):
{playbook}

Failures from evaluation runs (check id, grader, evidence, and an excerpt of the conversation with tool calls):
{failures}

Propose exactly {n} DIFFERENT candidate patches. Each patch is ONE change:
- "add_rule": a new general rule (text <= 300 chars)
- "edit_rule": rewrite an existing rule (target = rule id)
- "edit_tool_description": rewrite one tool's description (target = tool name)
- "add_example": a short illustrative exchange (<= 700 chars), using made-up generic details

What the failing checks mean:
{glossary}

Requirements:
- General behaviour only. NEVER mention specific patient or doctor names, IDs, dates, specialties, or anything
  that only makes sense for one conversation. The clinic has several specialties; a rule must work for all.
- Find the ROOT CAUSE in the conversation (what the assistant did wrong, step by step) and fix that behaviour.
  Make the {n} patches address DIFFERENT failing checks or different root causes, not three rewordings.
- An example must use placeholders (<patient>, <doctor>, <date>, <time>) and must itself follow every rule:
  e.g. it reads back the date AND time and waits for the patient's yes before confirming.
- Never relax verification, confirmation, escalation or privacy behaviour.
- Each patch must name the failing check ids it fixes and quote the evidence that motivated it.

Reply with JSON only:
{{"candidates": [{{"op": "...", "target": null, "text": "...", "rationale": "...", "fixes": ["..."], "evidence": "..."}}]}}"""


CHECK_GLOSSARY = {
    "db_no_collateral": "an appointment was created/changed for the WRONG person (e.g. the parent instead of "
                        "the linked child the patient asked about)",
    "db_goal": "the clinic database did not end in the state the patient asked for (nothing booked, wrong "
               "date, wrong time of day, wrong appointment changed)",
    "no_step_limit": "the assistant looped on tool calls without replying (e.g. repeating a failing call)",
    "no_false_success": "the assistant said something was done when the tool did not confirm it",
    "confirmed_before_write": "a change was made without first stating its date and time to the patient",
    "date_consistency": "the assistant stated a weekday that does not match the date",
    "clarified_ambiguous_date": "a relative date like 'next Friday' was not resolved with the patient",
    "no_invented_slots": "the assistant offered or used a time that the search tool never returned",
    "alternatives_after_slot_taken": "after a slot was taken, no other times were offered",
    "retry_bounded": "a failed call was retried too many times",
    "escalated": "the request needed a human but was not handed over",
}


def _glossary(digest: str) -> str:
    lines = [f"- {k}: {v}" for k, v in CHECK_GLOSSARY.items() if f"### {k} " in digest]
    return "\n".join(lines) or "- (see evidence)"


def _excerpt(run_dir: Path, trace_file: str, max_lines: int = 40) -> str:
    events = load_trace(run_dir / trace_file)
    lines = []
    for e in events:
        if e["type"] == "patient":
            lines.append(f"PATIENT: {e['text']}")
        elif e["type"] == "agent":
            lines.append(f"ASSISTANT: {e['text']}")
        elif e["type"] == "tool":
            status = "ok" if e["ok"] else e["code"]
            lines.append(f"  [tool {e['name']}({json.dumps(e['args'])[:120]}) -> {status}]")
        elif e["type"] == "guard" and e.get("flagged"):
            lines.append("  [safety guard: emergency flagged]")
    return "\n".join(lines[-max_lines:]) or format_transcript(events)


def build_failure_digest(items: list[TriageItem], suite: SuiteResult, run_dir: Path, per_check: int = 2) -> str:
    items = [i for i in items if i.category == "prompt_fixable"]
    if any(i.split != "train" for i in items):
        raise ValueError("held-out failures must never reach the reflector")
    by_check: dict[str, list[TriageItem]] = defaultdict(list)
    for i in items:
        by_check[i.check_id].append(i)
    blocks = []
    for check_id, its in sorted(by_check.items()):
        for it in its[:per_check]:
            trial = next(t for t in suite.scenarios[it.scenario_id].trials if t.trial == it.trial)
            blocks.append(f"### {check_id} ({it.grader}, {it.severity})\nEvidence: {it.evidence}\n"
                          f"Conversation excerpt:\n{_excerpt(run_dir, trial.trace_file)}")
    return "\n\n".join(blocks)


def reflect(llm: ChatModel, playbook: Playbook, digest: str, n: int = N_CANDIDATES) -> tuple[list[Patch], list[dict]]:
    prompt = REFLECT_PROMPT.format(playbook=yaml.safe_dump(playbook.model_dump(exclude={"changelog"}), sort_keys=False),
                                   failures=digest, n=n, glossary=_glossary(digest))
    try:
        resp = llm.complete([{"role": "user", "content": prompt}], json_mode=True, temperature=0.7,
                            use_cache=True, max_tokens=6000)  # reasoning models spend tokens before answering
        raw = extract_json(resp.content or "{}").get("candidates", [])
    except QuotaExhausted:
        raise  # infrastructure, not a bad answer: the loop stops and can be resumed
    except (LLMError, json.JSONDecodeError, AttributeError) as e:
        return [], [{"candidate": None, "reason": f"reflector failed: {type(e).__name__}: {str(e)[:200]}"}]
    patches, rejected = [], []
    for c in raw[:n]:
        try:
            patches.append(Patch.model_validate(c))
        except ValidationError as e:
            rejected.append({"candidate": c, "reason": f"schema: {e.errors()[0]['msg']}"})
    if len(raw) < n:
        rejected.append({"candidate": None, "reason": f"reflector returned {len(raw)} candidates, expected {n}"})
    return patches, rejected


SHORTEN_PROMPT = """Rewrite this clinic-assistant playbook {kind} so it is at most {target} characters, keeping its
meaning and every instruction it gives. Do not add new instructions, names, IDs or dates. Do not weaken any
safety behaviour. Reply with JSON only: {{"text": "<shortened text>"}}

Text ({n} chars):
{text}"""


def shorten(llm: ChatModel, patch: Patch) -> Patch | None:
    """A good patch should not be lost only because it ran a few characters over the limit.
    One call; the result is re-linted by the loop like any other patch. None if it is still too long."""
    limit = text_limit(patch)
    if limit is None:
        return None
    target = limit - 20  # headroom: models undercount characters
    prompt = SHORTEN_PROMPT.format(kind="example" if patch.op == "add_example" else "rule", target=target,
                                   n=len(patch.text), text=patch.text)
    try:
        resp = llm.complete([{"role": "user", "content": prompt}], json_mode=True, temperature=0.0,
                            use_cache=True, max_tokens=2000)
        text = str(extract_json(resp.content or "{}").get("text") or "").strip()
    except QuotaExhausted:
        raise
    except (LLMError, json.JSONDecodeError, AttributeError):
        return None
    if not text or len(text) > limit:
        return None
    return patch.model_copy(update={"text": text})
