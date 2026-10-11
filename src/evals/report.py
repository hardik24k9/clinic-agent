"""Markdown report for one eval run. Critical failures are listed explicitly, never hidden in averages."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .harness import ScenarioResult, SuiteResult


def _fmt_rate(r: float | None) -> str:
    return "n/a" if r is None else f"{r:.0%}"


def scenario_row(r: "ScenarioResult") -> str:
    fails = Counter(c.id for t in r.valid_trials for c in t.checks if c.blocking)
    failing = ", ".join(f"{k}×{v}" for k, v in sorted(fails.items())) or "—"
    inv = len(r.trials) - len(r.valid_trials)
    return (f"| {r.scenario_id} | {r.split} | {r.category} | {r.passes}/{len(r.valid_trials)}"
            f"{f' (+{inv} invalid)' if inv else ''} | {'✅' if r.pass_k else '❌'} | {r.critical_count} | {failing} |")


def render(suite: "SuiteResult") -> str:
    lines = [f"# Eval run `{suite.run_id}`", "",
             f"- Playbook: **v{suite.playbook_version}**" + ("" if suite.complete else "  ⚠️ **incomplete — resume**"),
             "- Models (pinned for the whole run):"]
    for role, m in suite.models.items():
        lines.append(f"  - {role}: `{m['provider']}/{m['model']}`")
    lines += ["", "| Split | pass^k | Avg pass rate | Critical violations | Invalid trials |", "|---|---|---|---|---|"]
    for split in ("train", "heldout"):
        st = suite.split_stats(split)
        if st["scenarios"]:
            lines.append(f"| {split} | {st['pass_k']}/{st['scenarios']} | {_fmt_rate(st['avg_pass_rate'])} | "
                         f"{st['critical']} | {st['invalid_trials']} |")
    lines += ["", "## Scenarios", "",
              "| Scenario | Split | Category | Passed | pass^k | Critical | Failing checks (count) |",
              "|---|---|---|---|---|---|---|"]
    lines += [scenario_row(r) for r in suite.scenarios.values()]

    crit = [(t, c) for r in suite.scenarios.values() for t in r.valid_trials for c in t.critical_failures]
    lines += ["", "## Critical violations", ""]
    lines += [f"- **{t.scenario_id} t{t.trial}** `{c.id}`: {c.evidence}" for t, c in crit] or ["None."]

    lines += ["", "## Failure evidence (first failing trial per scenario)", ""]
    for r in suite.scenarios.values():
        t = next((t for t in r.valid_trials if not t.passed), None)
        if t is None:
            continue
        lines.append(f"**{r.scenario_id}** (t{t.trial}, trace `{t.trace_file}`)")
        for c in t.checks:
            if c.blocking:
                flag = "" if c.evidence_verified else " _(judge quote not found in transcript)_"
                lines.append(f"- [{c.grader}/{c.severity}] `{c.id}`: {c.evidence}{flag}")
        lines.append("")
    if not suite.complete:
        lines += ["## Run status", "", f"- Stop reason: {suite.stop_reason or '—'}",
                  f"- Trials not started: {suite.not_run}", f"- Infra errors: {len(suite.infra_errors)}"]
        lines += [f"  - {e['scenario']} t{e['trial']} ({e['kind']}): {e['error'][:200]}" for e in suite.infra_errors]
        lines += ["- Resume with the same models: `make eval RUN_ID=" + suite.run_id + "`", ""]
    invalid = [t for r in suite.scenarios.values() for t in r.trials if not t.valid]
    if invalid:
        lines += ["## Invalid trials (simulator off-script — excluded from scores)", ""]
        lines += [f"- {t.scenario_id} t{t.trial}: {t.invalid_reason}" for t in invalid] + [""]
    unknown = sum(1 for r in suite.scenarios.values() for t in r.trials for c in t.checks if c.passed is None)
    b = suite.budget
    lines += ["## Cost", "", f"- Model calls: {b.get('total_calls', 0)}, tokens: {b.get('total_tokens', 0)}, "
              f"time: {b.get('elapsed_s', 0)}s", f"- Checks that could not be evaluated (e.g. judge errors): {unknown}"]
    for role, v in b.get("by_role", {}).items():
        lines.append(f"  - {role}: {v['calls']} calls ({v['cache_hits']} cache hits), "
                     f"{v['prompt_tokens'] + v['completion_tokens']} tokens")
    return "\n".join(lines) + "\n"


def write_report(suite: "SuiteResult", run_dir: Path) -> Path:
    path = run_dir / "report.md"
    path.write_text(render(suite))
    return path
