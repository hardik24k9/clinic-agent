"""End-to-end harness runs with fake models: trial execution, grading, files, pinning, resume, quota."""
import json

import pytest

from agent.playbook import load_playbook
from evals.harness import ModelSet, SuiteResult, run_suite
from evals.scenario import load_scenarios
from fakes import all_pass_judge, booking_agent, patient
from llm.budget import Budget
from llm.client import QuotaExhausted
from llm.fake import FakeLLM

ROLES = {r: {"role": r, "provider": "fake", "model": r} for r in ("agent", "simulator", "guard", "judge")}


def _models(agent, sim, judge=None):
    return ModelSet(agent=agent, simulator=sim, guard=None, judge=judge or all_pass_judge(), budget=Budget(),
                    roles=ROLES)


def _gp_scenario():
    return [s for s in load_scenarios() if s.id == "happy_book_gp"]


def _happy_models(claim=False):
    return _models(booking_agent("Sanjay Gupta", "1988-12-01", "2026-10-14", "general_medicine", afternoon=True,
                                 claim_success_anyway=claim),
                   patient(["Sanjay Gupta, 1 December 1988", "Yes please, go ahead.", "Thanks, bye <END>"]))


def test_happy_path_trial_passes_and_writes_artifacts(tmp_path):
    suite = run_suite(_gp_scenario(), load_playbook(0), _happy_models(), run_id="t1", k=1,
                      runs_dir=tmp_path, log=lambda *_: None)
    r = suite.scenarios["happy_book_gp"]
    assert r.pass_k and r.passes == 1, [c for c in r.trials[0].checks if c.failed]
    run_dir = tmp_path / "t1"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["models"]["agent"]["model"] == "agent" and manifest["playbook_version"] == 0
    assert (run_dir / "traces" / "happy_book_gp__t1.jsonl").exists()
    assert (run_dir / "report.md").read_text().startswith("# Eval run")
    assert SuiteResult.load(run_dir).scenarios["happy_book_gp"].passes == 1


def test_resume_skips_completed_trials(tmp_path):
    run_suite(_gp_scenario(), load_playbook(0), _happy_models(), run_id="r", k=1, runs_dir=tmp_path, log=lambda *_: None)
    exploding = _models(FakeLLM(lambda m, t: (_ for _ in ()).throw(AssertionError("should not run"))),
                        FakeLLM(lambda m, t: "x"))
    suite = run_suite(_gp_scenario(), load_playbook(0), exploding, run_id="r", k=1, runs_dir=tmp_path,
                      log=lambda *_: None)
    assert suite.scenarios["happy_book_gp"].passes == 1


def test_resume_with_different_models_is_refused(tmp_path):
    run_suite(_gp_scenario(), load_playbook(0), _happy_models(), run_id="p", k=1, runs_dir=tmp_path, log=lambda *_: None)
    other = _happy_models()
    other.roles = {**ROLES, "agent": {"role": "agent", "provider": "fake", "model": "DIFFERENT"}}
    with pytest.raises(SystemExit, match="Refusing to resume"):
        run_suite(_gp_scenario(), load_playbook(0), other, run_id="p", k=1, runs_dir=tmp_path, log=lambda *_: None)


def test_quota_exhaustion_stops_cleanly_without_switching_models(tmp_path):
    def quota(m, t):
        raise QuotaExhausted("groq/x: daily tokens used up")
    suite = run_suite(_gp_scenario(), load_playbook(0), _models(FakeLLM(quota), patient([])), run_id="q", k=1,
                      runs_dir=tmp_path, log=lambda *_: None)
    assert suite.complete is False and suite.scenarios["happy_book_gp"].trials == []
    assert "incomplete" in (tmp_path / "q" / "report.md").read_text()


def test_false_success_is_caught_end_to_end(tmp_path):
    scn = [s for s in load_scenarios() if s.id == "slot_taken_at_confirm"]
    agent = booking_agent("Sanjay Gupta", "1988-12-01", "2026-10-16", "general_medicine", claim_success_anyway=True)
    sim = patient(["Sanjay Gupta, 1 December 1988", "Yes, book it.", "Great, bye <END>"])
    suite = run_suite(scn, load_playbook(0), _models(agent, sim), run_id="f", k=1, runs_dir=tmp_path,
                      log=lambda *_: None)
    t = suite.scenarios["slot_taken_at_confirm"].trials[0]
    failed = {c.id for c in t.checks if c.failed}
    assert not t.passed and {"no_false_success", "db_goal"} <= failed
    assert suite.split_stats("train")["critical"] >= 1
    assert "Critical violations" in (tmp_path / "f" / "report.md").read_text()


def test_simulator_provider_error_marks_trial_invalid_not_failed(tmp_path):
    from llm.client import LLMError

    def boom(m, t):
        raise LLMError("simulator provider 500")
    models = _models(booking_agent("Sanjay Gupta", "1988-12-01", "2026-10-14", "general_medicine", afternoon=True),
                     FakeLLM(boom))
    suite = run_suite(_gp_scenario(), load_playbook(0), models, run_id="e", k=1, runs_dir=tmp_path, log=lambda *_: None)
    t = suite.scenarios["happy_book_gp"].trials[0]
    assert not t.valid and t.invalid_reason.startswith("infra_error")
    assert not (tmp_path / "e" / "trials" / "happy_book_gp__t1.json").exists()  # retried on resume


def test_judge_receives_the_scenario_persona(tmp_path):
    """The judge sees the persona, so in-persona insisting is not marked as 'did not follow goal'."""
    inner = all_pass_judge()
    prompts = []

    class Spy:
        label = "fake/judge"

        def complete(self, messages, *a, **kw):
            prompts.append(messages[0]["content"])
            return inner.complete(messages, *a, **kw)
    m = _happy_models()
    m.judge = Spy()
    run_suite(_gp_scenario(), load_playbook(0), m, run_id="p", k=1, runs_dir=tmp_path, log=lambda *_: None)
    persona = _gp_scenario()[0].persona
    assert prompts and f"Patient's persona: {persona}" in prompts[0]
