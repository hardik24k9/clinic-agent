import pytest

from agent.guard import EmergencyGuard, rule_check
from llm.client import LLMError
from llm.fake import FakeLLM


@pytest.mark.parametrize("text,cat", [
    ("I have crushing chest pain since morning", "cardiac"),
    ("i cant breathe properly", "breathing"),
    ("my father's face is drooping", "stroke"),
    ("honestly I just want to die", "self_harm"),
    ("I took too many pills", "poisoning"),
])
def test_rules_catch_red_flags(text, cat):
    assert rule_check(text) == cat


@pytest.mark.parametrize("text", ["I have a rash on my arm", "Can I book a check-up?",
                                  "What's the dose of paracetamol?", "mild fever and cough"])
def test_rules_ignore_routine_messages(text):
    assert rule_check(text) is None


def test_llm_layer_catches_paraphrase():
    guard = EmergencyGuard(FakeLLM(lambda m, t: {"red_flag": True, "category": "cardiac"}))
    r = guard.check("there's an elephant sitting on my chest and my left arm is numb")
    assert r.flagged and r.source == "llm"


def test_llm_failure_degrades_to_rules_only():
    def boom(m, t):
        raise LLMError("down")
    guard = EmergencyGuard(FakeLLM(boom))
    r = guard.check("chest pain right now")
    assert r.flagged and r.degraded and r.source == "rules"
    assert not guard.check("book a checkup").flagged
