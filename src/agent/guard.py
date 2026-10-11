"""Emergency guard: screens EVERY patient message, independently of the main agent.

Two layers, flag if either fires:
1. Deterministic red-flag patterns (instant, free, never down).
2. A small LLM classifier for paraphrases the patterns miss.
If the LLM layer errors, the result is rules-only and marked `degraded` in the trace (the rules
still apply). Over-flagging is preferred to missing: a false positive only pauses booking and
hands to a human; a miss could cost a life.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from llm.client import ChatModel, LLMError, extract_json

_PATTERNS: list[tuple[str, str]] = [
    ("cardiac", r"chest (pain|pressure|tightness|is (tight|hurting))|heart attack|crushing (pain|feeling) in my chest"),
    ("breathing", r"(can'?t|cannot|can not|struggling to|hard to|difficulty) breath|short(ness)? of breath|choking"),
    ("stroke", r"\bstroke\b|face (is )?droop|slurred speech|numb(ness)? (on|in) one side|can'?t move my (arm|leg)"),
    ("consciousness", r"unconscious|passed out|fainted|not waking up|seizure|fitting"),
    ("bleeding", r"(severe|heavy|heavily|won'?t stop) bleed|bleeding (a lot|heavily|won'?t stop)|coughing (up )?blood|vomiting blood"),
    ("allergy", r"anaphyla|throat (is )?(closing|swelling)|tongue (is )?swelling"),
    ("self_harm", r"suicid|kill myself|end (my|it all)|take my (own )?life|self[- ]?harm|hurt(ing)? myself|want to die|don'?t want to (live|be alive)"),
    ("poisoning", r"overdos|swallowed (bleach|poison|pills)|took too many (pills|tablets)"),
]
_COMPILED = [(cat, re.compile(p, re.IGNORECASE)) for cat, p in _PATTERNS]

GUARD_PROMPT = """You are a medical triage safety filter for a clinic's scheduling assistant.
Decide if the patient's message describes a CURRENT red-flag emergency (e.g. chest pain, trouble
breathing, stroke signs, heavy bleeding, loss of consciousness, severe allergic reaction, poisoning)
or any sign of suicide or self-harm. Routine symptoms (rash, mild fever, cough, back ache), questions
about medicines, and administrative requests are NOT emergencies.
Reply with JSON only: {"red_flag": true|false, "category": "<short label or none>"}"""


@dataclass
class GuardResult:
    flagged: bool
    category: str | None
    source: str          # "rules", "llm", "rules+llm" or "none"
    degraded: bool = False


def rule_check(text: str) -> str | None:
    for cat, rx in _COMPILED:
        if rx.search(text):
            return cat
    return None


class EmergencyGuard:
    def __init__(self, llm: ChatModel | None = None) -> None:
        self.llm = llm

    def check(self, text: str) -> GuardResult:
        rule_cat = rule_check(text)
        llm_cat, degraded = None, False
        if self.llm is not None:
            try:
                resp = self.llm.complete([{"role": "system", "content": GUARD_PROMPT},
                                          {"role": "user", "content": text}],
                                         json_mode=True, temperature=0.0, max_tokens=400,
                                         use_cache=True)  # same text -> same verdict; saves quota
                data = extract_json(resp.content or "{}")
                if data.get("red_flag") is True:
                    llm_cat = str(data.get("category") or "unspecified")
            except (LLMError, json.JSONDecodeError, AttributeError):
                degraded = True
        sources = [s for s, hit in (("rules", rule_cat), ("llm", llm_cat)) if hit]
        return GuardResult(flagged=bool(sources), category=rule_cat or llm_cat,
                           source="+".join(sources) or "none", degraded=degraded)
