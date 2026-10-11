"""Regression tests: one bad trial must never crash the suite.

The original bug: after QuotaExhausted the harness called Future.cancel() on queued trials, then read
them from as_completed(); .result() on a cancelled future raised CancelledError and aborted the run.
"""
import json

from agent.playbook import load_playbook
from evals.harness import ModelSet, SuiteResult, run_suite
from evals.scenario import load_scenarios
from fakes import all_pass_judge
from llm.budget import Budget
from llm.client import QuotaExhausted
from llm.fake import FakeLLM, say

ROLES = {r: {"role": r, "provider": "fake", "model": r} for r in ("agent", "simulator", "guard", "judge")}
IDS = ["happy_cancel", "happy_book_gp", "linked_child", "medical_advice"]
QUIET = dict(log=lambda *_: None)


def _scenarios():
    by = {s.id: s for s in load_scenarios()}
    return [by[i] for i in IDS]  # happy_cancel first, so its failure happens while others are queued


def _agent(raise_for_opening=None, exc=None):
    def responder(messages, tools):
        first_user = next(m["content"] for m in messages if m["role"] == "user")
        if raise_for_opening and first_user.startswith(raise_for_opening):
            raise exc
        return say("Okay.")
    return FakeLLM(responder, "fake/agent")


def _models(agent, sim=None):
    return ModelSet(agent=agent, simulator=sim or FakeLLM(lambda m, t: "Thanks, bye <END>"), guard=None,
                    judge=all_pass_judge(), budget=Budget(), roles=ROLES)


def test_quota_with_queued_trials_in_parallel_does_not_crash(tmp_path):
    """Exact reproduction of the crash: 2 workers, 8 trials, quota on the first scenario."""
    agent = _agent("I need to cancel", QuotaExhausted("groq: tokens per day exhausted"))
    suite = run_suite(_scenarios(), load_playbook(0), _models(agent), run_id="q", k=2, workers=2,
                      runs_dir=tmp_path, **QUIET)  # previously: concurrent.futures.CancelledError
    assert suite.complete is False and suite.stop_reason.startswith("quota")
    assert suite.not_run >= 1
    saved = sorted(p.name for p in (tmp_path / "q" / "trials").glob("*.json"))
    assert not any(n.startswith("happy_cancel") for n in saved)        # the stopped trial is not checkpointed
    assert (tmp_path / "q" / "summary.json").exists()
    assert "Run status" in (tmp_path / "q" / "report.md").read_text()
    errors = [json.loads(l) for l in (tmp_path / "q" / "errors.jsonl").read_text().splitlines()]
    assert errors[0]["kind"] == "quota" and "tokens per day" in errors[0]["error"]


def test_resume_after_quota_finishes_the_run_and_reuses_saved_trials(tmp_path):
    agent = _agent("I need to cancel", QuotaExhausted("quota"))
    first = run_suite(_scenarios(), load_playbook(0), _models(agent), run_id="r", k=2, workers=2,
                      runs_dir=tmp_path, **QUIET)
    done_before = sum(len(r.trials) for r in first.scenarios.values())
    calls = []

    def counting(messages, tools):
        calls.append(1)
        return say("Okay.")
    second = run_suite(_scenarios(), load_playbook(0), _models(FakeLLM(counting)), run_id="r", k=2, workers=2,
                       runs_dir=tmp_path, **QUIET)
    assert second.complete and second.stop_reason is None
    assert sum(len(r.trials) for r in second.scenarios.values()) == 8
    assert len(calls) == 2 * (8 - done_before)  # 2 agent calls per fake trial; saved trials are not re-run


def test_unexpected_worker_exception_is_an_infra_error_not_a_crash(tmp_path):
    agent = _agent("I need to cancel", RuntimeError("boom in a worker"))
    suite = run_suite(_scenarios(), load_playbook(0), _models(agent), run_id="w", k=1, workers=2,
                      runs_dir=tmp_path, **QUIET)
    t = suite.scenarios["happy_cancel"].trials[0]
    assert not t.valid and t.end_reason == "infra_error" and "RuntimeError" in t.invalid_reason
    assert all(len(suite.scenarios[i].trials) == 1 and suite.scenarios[i].trials[0].end_reason != "infra_error"
               for i in IDS[1:])                                      # the other trials still ran and were graded
    assert suite.complete is False and len(suite.infra_errors) == 1
    assert not (tmp_path / "w" / "trials" / "happy_cancel__t1.json").exists()  # retried on resume


def test_genuine_agent_failures_are_recorded_and_the_suite_continues(tmp_path):
    suite = run_suite(_scenarios(), load_playbook(0), _models(_agent()), run_id="g", k=1, workers=2,
                      runs_dir=tmp_path, **QUIET)
    assert suite.complete and all(len(r.trials) == 1 for r in suite.scenarios.values())
    t = suite.scenarios["happy_book_gp"].trials[0]
    assert t.valid and not t.passed and any(c.id == "db_goal" and c.failed for c in t.checks)
    assert (tmp_path / "g" / "trials" / "happy_book_gp__t1.json").exists()


def test_trial_timeout_is_an_infra_error(tmp_path):
    sim = FakeLLM(lambda m, t: "still here")  # never ends on its own
    suite = run_suite(_scenarios()[:1], load_playbook(0), _models(_agent(), sim), run_id="t", k=1,
                      trial_timeout_s=0.0, runs_dir=tmp_path, **QUIET)
    t = suite.scenarios["happy_cancel"].trials[0]
    assert t.end_reason == "infra_error" and "TrialTimeout" in t.invalid_reason and not suite.complete


def test_sequential_and_parallel_give_the_same_results(tmp_path):
    a = run_suite(_scenarios(), load_playbook(0), _models(_agent()), run_id="s1", k=1, workers=1,
                  runs_dir=tmp_path, **QUIET)
    b = run_suite(_scenarios(), load_playbook(0), _models(_agent()), run_id="s2", k=1, workers=3,
                  runs_dir=tmp_path, **QUIET)
    def view(s: SuiteResult):
        return {sid: [(t.passed, sorted(c.id for c in t.checks if c.failed)) for t in r.trials]
                for sid, r in s.scenarios.items()}
    assert view(a) == view(b)


def test_cli_defaults_to_sequential_workers():
    import inspect
    from evals import harness
    from evals.improve.loop import LoopConfig
    assert 'default=1' in inspect.getsource(harness.main).split('"--workers"')[1].split(")")[0]
    assert LoopConfig().workers == 1
