"""Evaluation harness:  make eval   (python -m evals.harness --help)

Runs scenarios x k trials against a playbook version, grades each trial with the four graders and
writes everything under runs/evals/<run_id>/:
  manifest.json         exact provider/model per role, playbook version, k, scenarios (pinned)
  traces/*.jsonl        full event trace per trial
  trials/*.json         graded result per trial (checkpoint: completed trials are never re-run)
  summary.json, report.md

Model pinning: a resumed run refuses to continue if the configured models differ from the manifest.
On QuotaExhausted the run stops cleanly and can be resumed with the same --run-id later.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agent.guard import EmergencyGuard
from agent.playbook import Playbook, load_playbook
from agent.runner import AgentRunner
from agent.state import SessionState
from agent.tools import ToolContext
from agent.trace import Tracer
from clinic.clock import FixedClock
from clinic.faults import FaultPlan
from clinic.store import ClinicStore
from llm.budget import Budget
from llm.client import ChatModel, QuotaExhausted
from llm.config import redact

from .checks import Check
from .graders.judge import judge, sanitize_verdicts
from .graders.sim_fidelity import grade_sim, is_valid
from .graders.state import grade_state
from .graders.trace import MOVED_FROM_JUDGE, grade_trace
from .scenario import Scenario, load_scenarios
from .simulator import PatientSimulator

RUNS_DIR = Path(__file__).resolve().parents[2] / "runs" / "evals"


@dataclass
class ModelSet:
    agent: ChatModel
    simulator: ChatModel
    guard: ChatModel | None
    judge: ChatModel | None
    budget: Budget
    roles: dict[str, dict[str, str]]  # public provider/model per role (for the manifest)


def build_models(roles: tuple[str, ...] = ("agent", "simulator", "guard", "judge")) -> ModelSet:
    from llm.cache import ResponseCache
    from llm.client import make_client
    from llm.config import role_config
    from llm.ratelimit import RateLimiter

    budget, cache, limiter = Budget(), ResponseCache(), RateLimiter()
    clients = {r: make_client(r, budget=budget, cache=cache, limiter=limiter) for r in roles}
    return ModelSet(agent=clients["agent"], simulator=clients["simulator"], guard=clients.get("guard"),
                    judge=clients.get("judge"), budget=budget,
                    roles={r: role_config(r).public() for r in roles})


class TrialResult(BaseModel):
    scenario_id: str
    split: str
    category: str
    trial: int
    valid: bool
    invalid_reason: str = ""
    passed: bool
    end_reason: str
    turns: int
    checks: list[Check]
    trace_file: str

    @property
    def critical_failures(self) -> list[Check]:
        return [c for c in self.checks if c.failed and c.severity == "critical" and c.grader != "sim"]


class ScenarioResult(BaseModel):
    scenario_id: str
    split: str
    category: str
    trials: list[TrialResult]

    @property
    def valid_trials(self) -> list[TrialResult]:
        return [t for t in self.trials if t.valid]

    @property
    def passes(self) -> int:
        return sum(t.passed for t in self.valid_trials)

    @property
    def pass_rate(self) -> float | None:
        v = self.valid_trials
        return self.passes / len(v) if v else None

    @property
    def pass_k(self) -> bool:
        """pass^k: every valid trial passed (reliability, not just luck)."""
        v = self.valid_trials
        return bool(v) and all(t.passed for t in v)

    @property
    def critical_count(self) -> int:
        return sum(len(t.critical_failures) for t in self.valid_trials)


class SuiteResult(BaseModel):
    run_id: str
    playbook_version: int
    models: dict[str, dict[str, str]]
    scenarios: dict[str, ScenarioResult]
    budget: dict[str, Any] = {}
    complete: bool = True
    stop_reason: str | None = None          # e.g. quota exhausted
    infra_errors: list[dict[str, Any]] = []  # trials that could not be evaluated (not agent failures)
    not_run: int = 0                          # trials skipped because the run was stopping

    def split_stats(self, split: str) -> dict[str, Any]:
        rs = [r for r in self.scenarios.values() if r.split == split]
        rates = [r.pass_rate for r in rs if r.pass_rate is not None]
        return {"scenarios": len(rs), "pass_k": sum(r.pass_k for r in rs),
                "avg_pass_rate": round(sum(rates) / len(rates), 3) if rates else None,
                "critical": sum(r.critical_count for r in rs),
                "invalid_trials": sum(len(r.trials) - len(r.valid_trials) for r in rs)}

    def save(self, run_dir: Path) -> None:
        (run_dir / "summary.json").write_text(self.model_dump_json(indent=1))

    @classmethod
    def load(cls, run_dir: Path) -> "SuiteResult":
        return cls.model_validate_json((run_dir / "summary.json").read_text())


# ------------------------------------------------------------------ one trial
class TrialTimeout(Exception):
    """A trial exceeded its wall-clock budget (checked between turns; threads can't be interrupted)."""


def split_criteria(scn: Scenario) -> tuple[list[str], list[str]]:
    """Criteria that code can check move from the judge to the trace grader."""
    moved = [c for c in scn.expect.judge if c in MOVED_FROM_JUDGE]
    return list(scn.expect.trace) + moved, [c for c in scn.expect.judge if c not in MOVED_FROM_JUDGE]


def trial_passed(valid: bool, checks: list[Check]) -> bool:
    return valid and not any(c.blocking for c in checks if c.grader != "sim")


def run_trial(scn: Scenario, playbook: Playbook, trial: int, models: ModelSet, run_dir: Path,
              timeout_s: float | None = None) -> TrialResult:
    clock = FixedClock()
    store = ClinicStore.load_seed(clock=clock)
    ctx = ToolContext(store=store, state=SessionState(session_id=f"{scn.id}-{trial}"), clock=clock,
                      faults=FaultPlan(dict(scn.faults)))
    trace_path = run_dir / "traces" / f"{scn.id}__t{trial}.jsonl"
    tracer = Tracer(trace_path)
    runner = AgentRunner(ctx, models.agent, playbook, guard=EmergencyGuard(models.guard), tracer=tracer)
    sim = PatientSimulator(models.simulator, scn)
    before = store.snapshot()
    reply, end_reason, turns = None, "max_turns", 0
    started = time.monotonic()
    for turn in range(1, scn.max_turns + 1):
        if timeout_s is not None and time.monotonic() - started > timeout_s:
            tracer.emit("end", ctx.state.turn, reason="trial_timeout")
            raise TrialTimeout(f"exceeded {timeout_s:.0f}s after {turns} turns")
        msg, ended = sim.next_message(turn, reply)
        if not msg:
            end_reason = "simulator_end"
            break
        reply = runner.handle(msg)
        turns = turn
        if ended:
            end_reason = "simulator_end"
            break
        if ctx.state.escalated:
            end_reason = "escalated"
            break
    tracer.emit("end", ctx.state.turn, reason=end_reason)
    after = store.snapshot()
    events = tracer.events

    checks = grade_state(scn.expect.db, before, after, store)
    trace_extra, judge_extra = split_criteria(scn)
    checks += grade_trace(events, store, trace_extra)
    judge_checks, judge_sim = judge(models.judge, events, scn.goal, judge_extra,
                                    scripted_turns={1, *scn.script}, persona=scn.persona)  # same turns grade_sim skips
    judge_checks = sanitize_verdicts(judge_checks, events)
    sim_checks = grade_sim(events, scn, judge_sim)
    checks += judge_checks + sim_checks
    valid, why = is_valid(sim_checks)
    passed = trial_passed(valid, checks)
    return TrialResult(scenario_id=scn.id, split=scn.split, category=scn.category, trial=trial, valid=valid,
                       invalid_reason=why, passed=passed, end_reason=end_reason, turns=turns, checks=checks,
                       trace_file=str(trace_path.relative_to(run_dir)))


# ------------------------------------------------------------------ the suite
def run_suite(scenarios: list[Scenario], playbook: Playbook, models: ModelSet, *, run_id: str | None = None,
              k: int = 2, k_overrides: dict[str, int] | None = None, workers: int = 1,
              runs_dir: Path = RUNS_DIR, trial_timeout_s: float | None = 900, log=print) -> SuiteResult:
    run_id = run_id or f"{datetime.now():%Y%m%d-%H%M%S}-v{playbook.version}"
    run_dir = runs_dir / run_id
    (run_dir / "trials").mkdir(parents=True, exist_ok=True)
    k_of = {s.id: (k_overrides or {}).get(s.id, k) for s in scenarios}
    pb_hash = hashlib.sha256(playbook.model_dump_json(exclude={"changelog"}).encode()).hexdigest()[:12]
    manifest = {"run_id": run_id, "playbook_version": playbook.version, "playbook_hash": pb_hash,
                "models": models.roles,
                "scenarios": [s.id for s in scenarios], "k": k_of}
    mpath = run_dir / "manifest.json"
    if mpath.exists():
        old = json.loads(mpath.read_text())
        if (old["models"] != models.roles or old["playbook_version"] != playbook.version
                or old.get("playbook_hash", pb_hash) != pb_hash):  # same version number, different content
            raise SystemExit(f"Refusing to resume {run_id}: models/playbook differ from its manifest "
                             f"(was {old['models']}, now {models.roles}). Start a new run instead.")
        manifest["created"] = old.get("created")
    manifest.setdefault("created", None)
    manifest["created"] = manifest["created"] or datetime.now().isoformat(timespec="seconds")
    mpath.write_text(json.dumps(manifest, indent=1))

    store = ClinicStore.load_seed(clock=FixedClock())
    for s in scenarios:
        if problems := s.validate_against(store):
            raise SystemExit(f"spec_error in scenario {s.id}: {problems}")

    jobs, results = [], {}
    for s in scenarios:
        for t in range(1, k_of[s.id] + 1):
            f = run_dir / "trials" / f"{s.id}__t{t}.json"
            if f.exists():
                results[(s.id, t)] = TrialResult.model_validate_json(f.read_text())
            else:
                jobs.append((s, t, f))
    log(f"[eval] run {run_id}: playbook v{playbook.version}, {len(scenarios)} scenarios, "
        f"{len(jobs)} trials to run ({len(results)} already done)")
    # Robustness contract: one bad trial never crashes the suite.
    #  - a trial that raises anything        -> recorded as an infra_error trial (invalid, NOT checkpointed: retried on resume)
    #  - QuotaExhausted                      -> stop starting new trials (no Future.cancel(): a cancelled future's
    #                                           .result() raises CancelledError and would abort the run)
    #  - trials not started because of a stop -> counted as not_run; the run is marked incomplete and resumable
    stop = threading.Event()
    stop_reason: str | None = None
    infra_errors: list[dict[str, Any]] = []
    not_run = 0
    errors_log = run_dir / "errors.jsonl"

    def record_error(kind: str, sid: str, t: int, message: str) -> None:
        entry = {"time": datetime.now().isoformat(timespec="seconds"), "kind": kind, "scenario": sid, "trial": t,
                 "error": redact(message)[:500]}
        with errors_log.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
        if kind != "quota":
            infra_errors.append(entry)

    def infra_trial(s: Scenario, t: int, reason: str) -> TrialResult:
        return TrialResult(scenario_id=s.id, split=s.split, category=s.category, trial=t, valid=False,
                           invalid_reason=f"infra_error: {redact(reason)[:200]}", passed=False,
                           end_reason="infra_error", turns=0, checks=[], trace_file=f"traces/{s.id}__t{t}.jsonl")

    def work(job):
        s, t, f = job
        if stop.is_set():
            return None  # not started: the run is stopping
        try:
            r = run_trial(s, playbook, t, models, run_dir, timeout_s=trial_timeout_s)
        except QuotaExhausted:
            stop.set()
            raise
        except Exception as e:  # noqa: BLE001 - any worker failure is an infra error for THIS trial only
            return infra_trial(s, t, f"{type(e).__name__}: {e}")
        f.write_text(r.model_dump_json(indent=1))  # checkpoint only fully graded trials
        return r

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, j): j for j in jobs}
        for fut in as_completed(futures):
            s, t, _ = futures[fut]
            try:
                r = fut.result()
            except QuotaExhausted as e:
                stop_reason = stop_reason or f"quota: {redact(str(e))[:300]}"
                record_error("quota", s.id, t, str(e))
                log(f"[eval]   {s.id} t{t}: STOPPED (quota) - no new trials will start")
                continue
            except CancelledError:  # defensive: we never cancel, but a cancelled future must not crash the run
                not_run += 1
                continue
            except Exception as e:  # noqa: BLE001 - defensive: work() already catches; never crash the suite
                record_error("harness", s.id, t, f"{type(e).__name__}: {e}")
                results[(s.id, t)] = infra_trial(s, t, f"{type(e).__name__}: {e}")
                continue
            if r is None:
                not_run += 1
                continue
            if r.end_reason == "infra_error":
                record_error("infra", s.id, t, r.invalid_reason)
                log(f"[eval]   {s.id} t{t}: INFRA ERROR ({r.invalid_reason[:120]})")
            else:
                log(f"[eval]   {s.id} t{t}: {'PASS' if r.passed else ('INVALID' if not r.valid else 'FAIL')}")
            results[(s.id, t)] = r

    by_scn: dict[str, ScenarioResult] = {}
    for s in scenarios:
        trials = [results[(s.id, t)] for t in range(1, k_of[s.id] + 1) if (s.id, t) in results]
        by_scn[s.id] = ScenarioResult(scenario_id=s.id, split=s.split, category=s.category, trials=trials)
    complete = stop_reason is None and not infra_errors and not_run == 0
    suite = SuiteResult(run_id=run_id, playbook_version=playbook.version, models=models.roles,
                        scenarios=by_scn, budget=models.budget.summary(), complete=complete,
                        stop_reason=stop_reason, infra_errors=infra_errors, not_run=not_run)
    suite.save(run_dir)
    from .report import write_report
    write_report(suite, run_dir)
    if not complete:
        why = stop_reason or f"{len(infra_errors)} infra error(s)"
        log(f"[eval] INCOMPLETE: {why}; {not_run} trial(s) not started. Completed trials are saved "
            f"(details: {errors_log.relative_to(run_dir.parent.parent.parent)}).\n"
            f"[eval] Resume later with the SAME models:  make eval RUN_ID={run_id}")
    return suite


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the evaluation suite against a playbook version.")
    ap.add_argument("--playbook", type=int, default=None, help="playbook version (default: latest)")
    ap.add_argument("--split", default="all", choices=["all", "train", "heldout"])
    ap.add_argument("--only", default="", help="comma-separated scenario ids")
    ap.add_argument("--k", type=int, default=2, help="trials per scenario")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel trials (default 1: sequential is gentler on free-tier rate limits)")
    ap.add_argument("--trial-timeout", type=float, default=900, help="seconds per trial before it is an infra error")
    ap.add_argument("--run-id", default=None, help="resume an existing run")
    a = ap.parse_args(argv)
    playbook = load_playbook(a.playbook)
    scenarios = load_scenarios(a.split, [x for x in a.only.split(",") if x] or None)
    models = build_models()
    t0 = time.time()
    suite = run_suite(scenarios, playbook, models, run_id=a.run_id, k=a.k, workers=a.workers,
                      trial_timeout_s=a.trial_timeout)
    for split in ("train", "heldout"):
        st = suite.split_stats(split)
        if st["scenarios"]:
            print(f"[eval] {split}: pass^k {st['pass_k']}/{st['scenarios']}, avg pass rate {st['avg_pass_rate']}, "
                  f"critical violations {st['critical']}, invalid trials {st['invalid_trials']}")
    print(f"[eval] report: runs/evals/{suite.run_id}/report.md  ({time.time() - t0:.0f}s)")
    return 0 if suite.complete else 3


if __name__ == "__main__":
    sys.exit(main())
