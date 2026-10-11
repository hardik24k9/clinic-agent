"""Re-grade a finished run from its saved traces, WITHOUT calling any model.

Used when deterministic grading improves (a criterion moved from the LLM judge to code, or the judge sanity
filter changes): the conversations and the judge's stored verdicts are reused, only code-side grading is
re-applied. Never changes what the agent said or did.

    python -m evals.regrade <run_id>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.trace import load_trace
from clinic.clock import FixedClock
from clinic.store import ClinicStore

from .graders.judge import sanitize_verdicts
from .graders.trace import CHECKS, MOVED_FROM_JUDGE
from .harness import RUNS_DIR, SuiteResult, TrialResult, trial_passed
from .report import write_report
from .scenario import load_scenarios


def regrade_trial(t: TrialResult, run_dir: Path, wanted: list[str]) -> TrialResult:
    events = load_trace(run_dir / t.trace_file)
    store = ClinicStore.load_seed(clock=FixedClock())
    checks = [c for c in t.checks if not (c.grader == "judge" and c.id in MOVED_FROM_JUDGE)]
    checks = [c for c in checks if not (c.grader == "trace" and c.id in MOVED_FROM_JUDGE)]
    checks = [c for c in checks if c.grader != "judge"] + sanitize_verdicts([c for c in checks if c.grader == "judge"],
                                                                            events)
    checks += [CHECKS[i](events, store) for i in wanted]
    return t.model_copy(update={"checks": checks, "passed": trial_passed(t.valid, checks)})


def regrade_run(run_dir: Path) -> tuple[SuiteResult, list[str]]:
    scns = {s.id: s for s in load_scenarios()}
    suite = SuiteResult.load(run_dir)
    changes = []
    for sid, r in suite.scenarios.items():
        wanted = [c for c in scns[sid].expect.judge if c in MOVED_FROM_JUDGE]
        new = []
        for t in r.trials:
            nt = regrade_trial(t, run_dir, wanted)
            if nt.passed != t.passed:
                changes.append(f"{sid} t{t.trial}: {'PASS' if t.passed else 'FAIL'} -> {'PASS' if nt.passed else 'FAIL'}")
            (run_dir / "trials" / f"{sid}__t{t.trial}.json").write_text(nt.model_dump_json(indent=1))
            new.append(nt)
        r.trials = new
    suite.save(run_dir)
    write_report(suite, run_dir)
    return suite, changes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_id")
    a = ap.parse_args()
    run_dir = RUNS_DIR / a.run_id
    suite, changes = regrade_run(run_dir)
    for c in changes:
        print(f"[regrade] {c}")
    for s in ("train", "heldout"):
        st = suite.split_stats(s)
        print(f"[regrade] {s}: pass^k {st['pass_k']}/{st['scenarios']}, avg pass rate {st['avg_pass_rate']}, "
              f"critical violations {st['critical']}, invalid trials {st['invalid_trials']}")
    print(f"[regrade] report: runs/evals/{a.run_id}/report.md")


if __name__ == "__main__":
    main()
