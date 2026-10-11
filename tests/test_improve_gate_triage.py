import json

from evals.checks import Check
from evals.harness import ScenarioResult, SuiteResult, TrialResult
from evals.improve.gate import decide, paired_rates
from evals.improve.reflector import build_failure_digest, reflect
from evals.improve.triage import triage_trial
from agent.playbook import load_playbook
from fakes import GOOD_PATCH
from llm.fake import FakeLLM

import pytest


def trial(sid, split, n, passed, checks=None, valid=True, trace="traces/x.jsonl"):
    return TrialResult(scenario_id=sid, split=split, category="robustness", trial=n, valid=valid, passed=passed,
                       end_reason="x", turns=1, checks=checks or [], trace_file=trace,
                       invalid_reason="" if valid else "sim broke character")


def crit(id_="no_false_success"):
    return Check(id=id_, grader="trace", passed=False, severity="critical", evidence="claimed success")


def suite(spec, complete=True):
    """spec: {sid: (split, [passed...], critical_per_trial)}"""
    scns = {}
    for sid, (split, results, ncrit) in spec.items():
        trials = [trial(sid, split, i + 1, p, [crit()] * ncrit if not p else []) for i, p in enumerate(results)]
        scns[sid] = ScenarioResult(scenario_id=sid, split=split, category="x", trials=trials)
    return SuiteResult(run_id="x", playbook_version=0, models={}, scenarios=scns, complete=complete)


BASE = suite({"a": ("train", [True, True], 0), "t": ("train", [False, False], 0), "h": ("heldout", [True, True], 0)})
BT = suite({"t": ("train", [False, False, False], 0)})
CT_GOOD = suite({"t": ("train", [True, True, True], 0)})


def test_gate_accepts_a_real_improvement():
    cand = suite({"a": ("train", [True, True], 0), "t": ("train", [True, True], 0), "h": ("heldout", [True, True], 0)})
    g = decide(BASE, cand, BT, CT_GOOD, ["t"])
    assert g.accepted, g.reasons


def _targets(spec):
    """spec: {sid: (passes, fails, invalid)} -> a targeted-run SuiteResult."""
    scns = {}
    for sid, (np_, nf, ni) in spec.items():
        trials, n = [], 0
        for kind, cnt in (("p", np_), ("f", nf), ("i", ni)):
            for _ in range(cnt):
                n += 1
                trials.append(trial(sid, "train", n, kind == "p", valid=kind != "i"))
        scns[sid] = ScenarioResult(scenario_id=sid, split="train", category="x", trials=trials)
    return SuiteResult(run_id="x", playbook_version=0, models={}, scenarios=scns, complete=True)


def test_targeted_is_like_for_like():
    """Real case: baseline had 0 valid happy_book_gp trials (judge-invalidated) while the
    candidate had 1/1. Pooled that read 22% -> 30%; like-for-like it is 2/9 vs 2/9 = no improvement."""
    ids = ["happy_book_gp", "linked_child", "next_friday_ambiguity", "tool_timeout"]
    base = _targets({"happy_book_gp": (0, 0, 3), "linked_child": (1, 2, 0),
                     "next_friday_ambiguity": (1, 2, 0), "tool_timeout": (0, 3, 0)})
    cand = _targets({"happy_book_gp": (1, 0, 2), "linked_child": (1, 2, 0),
                     "next_friday_ambiguity": (0, 3, 0), "tool_timeout": (1, 2, 0)})
    b, c, common, excluded = paired_rates(base, cand, ids)
    assert excluded == ["happy_book_gp"] and len(common) == 3
    assert b == c == 2 / 9
    g = decide(BASE, BASE, base, cand, ids)
    assert not g.accepted and g.targeted_excluded == ["happy_book_gp"]
    assert any("no improvement" in r and "happy_book_gp" in r for r in g.reasons)


def test_targeted_with_no_common_scenarios_is_no_improvement():
    base = _targets({"t": (0, 0, 3)})
    cand = _targets({"t": (3, 0, 0)})
    assert paired_rates(base, cand, ["t"])[:2] == (None, None)
    assert not decide(BASE, BASE, base, cand, ["t"]).accepted


def test_gate_rejects_no_targeted_improvement():
    g = decide(BASE, BASE, BT, BT, ["t"])
    assert not g.accepted and "no improvement" in g.reasons[0]


def test_gate_rejects_confirmed_regression_but_clears_noise():
    cand = suite({"a": ("train", [True, False], 0), "t": ("train", [True, True], 0), "h": ("heldout", [True, True], 0)})
    still_bad = lambda sid: suite({sid: ("train", [True, False, True], 0)})
    noise = lambda sid: suite({sid: ("train", [True, True, True], 0)})
    assert not decide(BASE, cand, BT, CT_GOOD, ["t"], still_bad).accepted
    g = decide(BASE, cand, BT, CT_GOOD, ["t"], noise)
    assert g.accepted and g.regressions_suspected == ["a"] and g.regressions_confirmed == []


def test_gate_requires_zero_criticals_in_strict_mode():
    cand = suite({"a": ("train", [True, True], 0), "t": ("train", [True, False], 1), "h": ("heldout", [True, True], 0)})
    g = decide(BASE, cand, BT, CT_GOOD, ["t"])
    assert not g.accepted and any("must be 0" in r for r in g.reasons)


def test_gate_rejects_heldout_drop_and_incomplete_runs():
    cand = suite({"a": ("train", [True, True], 0), "t": ("train", [True, True], 0), "h": ("heldout", [True, False], 0)})
    noise = lambda sid: suite({sid: ("heldout", [True, True, True], 0)})
    assert not decide(BASE, cand, BT, CT_GOOD, ["t"], noise).accepted
    good = suite({"a": ("train", [True, True], 0), "t": ("train", [True, True], 0), "h": ("heldout", [True, True], 0)},
                 complete=False)
    assert not decide(BASE, good, BT, CT_GOOD, ["t"]).accepted


# ---------------------------------------------------------------- triage
def test_triage_categories(tmp_path):
    (tmp_path / "traces").mkdir()
    (tmp_path / "traces" / "x.jsonl").write_text(json.dumps({"type": "tool", "code": "OK"}) + "\n")
    judge_bad = Check(id="empathy_tone", grader="judge", passed=False, severity="major", evidence="?",
                      evidence_verified=False)
    collateral = Check(id="db_no_collateral", grader="state", passed=False, severity="critical", evidence="x")
    goal = Check(id="db_goal", grader="state", passed=False, severity="major", evidence="x")
    minor = Check(id="one_question_at_a_time", grader="judge", passed=False, severity="minor", evidence="x")
    items = triage_trial(trial("s", "train", 1, False, [judge_bad, collateral, goal, minor]), tmp_path)
    assert {i.check_id: i.category for i in items} == {"empathy_tone": "judge_error", "db_no_collateral": "code_bug",
                                                        "db_goal": "prompt_fixable"}
    assert triage_trial(trial("s", "train", 1, False, valid=False), tmp_path)[0].category == "simulator_error"


def test_tool_crash_is_a_code_bug(tmp_path):
    (tmp_path / "traces").mkdir()
    (tmp_path / "traces" / "x.jsonl").write_text(json.dumps({"type": "tool", "code": "INTERNAL_ERROR"}) + "\n")
    goal = Check(id="db_goal", grader="state", passed=False, severity="major", evidence="x")
    assert triage_trial(trial("s", "train", 1, False, [goal]), tmp_path)[0].category == "code_bug"


# ---------------------------------------------------------------- reflector
def test_reflector_never_sees_heldout(tmp_path):
    from evals.improve.triage import TriageItem
    item = TriageItem(scenario_id="h", split="heldout", trial=1, check_id="db_goal", grader="state",
                      severity="major", category="prompt_fixable", evidence="x")
    with pytest.raises(ValueError, match="held-out"):
        build_failure_digest([item], BASE, tmp_path)


def test_reflector_returns_typed_patches_and_records_malformed():
    llm = FakeLLM(lambda m, t: {"candidates": [GOOD_PATCH, {"op": "rewrite_python", "text": "x"}, GOOD_PATCH]})
    patches, rejected = reflect(llm, load_playbook(0), "digest", n=3)
    assert len(patches) == 2 and patches[0].op == "add_rule"
    assert len(rejected) == 1 and "schema" in rejected[0]["reason"]
    prompt = llm.calls[0][0]["content"]
    assert "NEVER mention specific patient" in prompt and "exactly 3" in prompt
