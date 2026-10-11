"""The improvement loop:  make improve   (python -m evals.improve.loop --help)

  baseline eval (vN) -> triage -> reflector (prompt_fixable TRAIN failures only) -> 3 typed patches
  -> lint -> targeted screen (k_targeted, vs a fresh baseline measurement) -> full suite incl. held-out
  -> acceptance gate (with regression confirmation) -> playbook vN+1 + runs/vN_vs_vN+1.md

Only the playbook can change. Every sub-run pins its models in its manifest; the whole loop is
resumable with --improve-id after a quota stop (candidates are saved, completed trials are reused).
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from agent.playbook import PLAYBOOK_DIR, Playbook, load_playbook, save_playbook
from clinic.clock import FixedClock
from clinic.store import ClinicStore
from llm.client import ChatModel, QuotaExhausted

from ..harness import RUNS_DIR, ModelSet, SuiteResult, run_suite
from ..scenario import Scenario, load_scenarios
from .gate import GateResult, _rate, decide, paired_rates
from .patch import Patch, apply_patch, lint_patch, only_too_long
from .reflector import N_CANDIDATES, build_failure_digest, reflect, shorten
from .triage import TriageItem, summarize, triage_suite

RUNS_ROOT = RUNS_DIR.parent


@dataclass
class LoopConfig:
    k: int = 2
    k_targeted: int = 3
    n_candidates: int = N_CANDIDATES
    critical_policy: str = "strict"
    workers: int = 1


def _pb_yaml(pb: Playbook) -> list[str]:
    return yaml.safe_dump(pb.model_dump(exclude={"changelog", "version"}), sort_keys=False, width=100).splitlines()


def improve(models: ModelSet, reflector: ChatModel, *, base_version: int | None = None,
            scenarios: list[Scenario] | None = None, improve_id: str | None = None, cfg: LoopConfig | None = None,
            runs_root: Path = RUNS_ROOT, playbook_dir: Path = PLAYBOOK_DIR, baseline_run: str | None = None,
            reflector_role: dict[str, str] | None = None, log=print) -> dict[str, Any]:
    cfg = cfg or LoopConfig()
    pb = load_playbook(base_version, playbook_dir)
    scenarios = scenarios or load_scenarios()
    by_id = {s.id: s for s in scenarios}
    improve_id = improve_id or f"{datetime.now():%Y%m%d-%H%M%S}-from-v{pb.version}"
    idir = runs_root / "improve" / improve_id
    sub = idir / "evals"
    idir.mkdir(parents=True, exist_ok=True)
    prov, _, mod = reflector.label.partition("/")
    roles = {**models.roles, "reflector": reflector_role or {"role": "reflector", "provider": prov, "model": mod}}
    result: dict[str, Any] = {"improve_id": improve_id, "base_version": pb.version, "models": roles,
                              "config": cfg.__dict__, "status": "running"}

    def run(name: str, scns: list[Scenario], playbook: Playbook, k: int) -> SuiteResult:
        return run_suite(scns, playbook, models, run_id=name, k=k, workers=cfg.workers, runs_dir=sub, log=log)

    def stop(status: str) -> dict[str, Any]:
        result["status"] = status
        result["budget_this_session"] = models.budget.summary()
        (idir / "result.json").write_text(json.dumps(result, indent=1, default=str))
        _write_report(result, pb, None, idir, runs_root)
        if status == "incomplete":
            log(f"[improve] a sub-run is incomplete (quota or infra errors). Resume with the same models: "
                f"make improve IMPROVE_ID={improve_id}")
        return result

    # 1. Baseline
    log(f"[improve] {improve_id}: baseline eval of playbook v{pb.version}")
    if baseline_run:
        baseline = run_suite(scenarios, pb, models, run_id=baseline_run, k=cfg.k, workers=cfg.workers, log=log)
        base_dir = RUNS_DIR / baseline_run
    else:
        baseline = run("baseline", scenarios, pb, cfg.k)
        base_dir = sub / "baseline"
    result["baseline"] = {"run": str(base_dir.relative_to(runs_root)), "train": baseline.split_stats("train"),
                          "heldout": baseline.split_stats("heldout")}
    if not baseline.complete:
        return stop("incomplete")

    # 2. Triage
    items = triage_suite(baseline, base_dir)
    result["triage"] = {"summary": summarize(items), "items": [i.model_dump() for i in items]}
    fixable = [i for i in items if i.category == "prompt_fixable" and i.split == "train"]
    targets = sorted({i.scenario_id for i in fixable})
    result["targets"] = targets
    log(f"[improve] triage: {summarize(items)}; prompt-fixable train targets: {targets}")
    if not targets:
        return stop("nothing_to_fix")

    # 3. Reflect (saved, so a resumed loop evaluates the same candidates)
    cand_file = idir / "candidates.json"
    saved = json.loads(cand_file.read_text()) if cand_file.exists() else None
    if saved and saved["patches"]:
        patches = [Patch.model_validate(p) for p in saved["patches"]]
        schema_rejected = saved["schema_rejected"]
    else:  # nothing saved, or a saved empty result (an earlier failed call) -> ask again
        digest = build_failure_digest(fixable, baseline, base_dir)
        try:
            patches, schema_rejected = reflect(reflector, pb, digest, cfg.n_candidates)
        except QuotaExhausted as e:
            log(f"[improve] reflector quota: {e}")
            return stop("incomplete")
        if patches:  # only cache a real answer, so a failed call is retried on resume
            cand_file.write_text(json.dumps({"patches": [p.model_dump() for p in patches],
                                             "schema_rejected": schema_rejected}, indent=1))
    log(f"[improve] reflector produced {len(patches)} valid candidate patches")

    # 4. Lint
    store = ClinicStore.load_seed(clock=FixedClock())
    candidates: list[dict[str, Any]] = []
    for n, p in enumerate(patches, 1):
        reasons = lint_patch(p, pb, store)
        entry: dict[str, Any] = {"n": n}
        if only_too_long(reasons):  # shorten once instead of discarding, then re-lint
            try:
                short = shorten(reflector, p)
            except QuotaExhausted as e:
                log(f"[improve] reflector quota while shortening: {e}")
                return stop("incomplete")
            entry["shortened"] = short is not None
            entry["original_text"] = p.text
            entry["original_lint"] = reasons
            if short is not None:
                p, reasons = short, lint_patch(short, pb, store)
                log(f"[improve] cand{n}: shortened {len(entry['original_text'])} -> {len(p.text)} chars")
        entry.update({"patch": p.model_dump(), "lint": reasons, "status": "rejected_lint" if reasons else "linted"})
        candidates.append(entry)
    result["candidates"] = candidates
    result["schema_rejected"] = schema_rejected
    linted = [c for c in candidates if c["status"] == "linted"]
    log(f"[improve] lint: {len(linted)} passed, {len(candidates) - len(linted)} rejected")
    if not linted:
        return stop("no_candidate_passed_lint")

    # 5. Targeted screen (fresh baseline measurement with the same k for a fair comparison)
    target_scns = [by_id[t] for t in targets]
    base_targets = run("baseline-targets", target_scns, pb, cfg.k_targeted)
    if not base_targets.complete:
        return stop("incomplete")
    bt = _rate(base_targets, targets)
    result["targeted_baseline"] = bt
    for c in linted:
        cpb = apply_patch(pb, Patch.model_validate(c["patch"]))
        ct_suite = run(f"cand{c['n']}-targets", target_scns, cpb, cfg.k_targeted)
        if not ct_suite.complete:
            return stop("incomplete")
        pb_rate, pc_rate, _common, excluded = paired_rates(base_targets, ct_suite, targets)
        c["targeted"] = pc_rate
        c["targeted_baseline_paired"] = pb_rate
        c["targeted_excluded"] = excluded
        c["targeted_by_scenario"] = {t: ct_suite.scenarios[t].pass_rate for t in targets}
        if pc_rate is None or pb_rate is None or not pc_rate > pb_rate:
            c["status"] = "rejected_no_targeted_improvement"
        else:
            c["status"] = "screened"
        log(f"[improve] cand{c['n']} targeted (like-for-like): {pb_rate} -> {pc_rate} ({c['status']})"
            + (f" excluded={excluded}" if excluded else ""))

    # 6. Full regression + held-out suite, then the gate - best screened candidate first
    survivors = sorted([c for c in linted if c["status"] == "screened"], 
                       key=lambda c: -(c["targeted"] - c["targeted_baseline_paired"]))
    accepted: tuple[dict[str, Any], Playbook] | None = None
    for c in survivors:
        cpb = apply_patch(pb, Patch.model_validate(c["patch"]))
        full = run(f"cand{c['n']}-full", scenarios, cpb, cfg.k)
        if not full.complete:
            return stop("incomplete")
        cand_targets = SuiteResult.load(sub / f"cand{c['n']}-targets")

        def confirm(sid: str, cpb=cpb, n=c["n"]) -> SuiteResult:
            return run(f"cand{n}-confirm-{sid}", [by_id[sid]], cpb, cfg.k_targeted)

        gate: GateResult = decide(baseline, full, base_targets, cand_targets, targets, confirm, cfg.critical_policy)
        c["full_run"] = f"evals/cand{c['n']}-full"
        c["full"] = {"train": full.split_stats("train"), "heldout": full.split_stats("heldout"),
                     "per_scenario": {sid: r.pass_rate for sid, r in full.scenarios.items()}}
        c["gate"] = gate.model_dump()
        c["status"] = "accepted" if gate.accepted else "rejected_gate"
        log(f"[improve] cand{c['n']} gate: {'ACCEPTED' if gate.accepted else 'rejected'} - {gate.reasons}")
        if gate.accepted:
            accepted = (c, cpb)
            break

    result["baseline_per_scenario"] = {sid: r.pass_rate for sid, r in baseline.scenarios.items()}
    if accepted:
        c, cpb = accepted
        path = save_playbook(cpb, playbook_dir)
        result["accepted"] = {"candidate": c["n"], "new_version": cpb.version, "playbook_file": path.name}
        result["status"] = "accepted"
    else:
        result["status"] = "no_candidate_accepted"
    result["budget_this_session"] = models.budget.summary()
    (idir / "result.json").write_text(json.dumps(result, indent=1, default=str))
    report = _write_report(result, pb, accepted[1] if accepted else None, idir, runs_root)
    log(f"[improve] done: {result['status']}. Report: {report.relative_to(runs_root.parent)}")
    return result


def _fmt_targeted(c: dict[str, Any]) -> str:
    if "targeted" not in c:
        return "—"
    s = f"{c.get('targeted_baseline_paired')} → {c['targeted']}"
    if c.get("targeted_excluded"):
        s += f" (excl. {', '.join(c['targeted_excluded'])})"
    return s


def _write_report(result: dict[str, Any], base: Playbook, new: Playbook | None, idir: Path, runs_root: Path) -> Path:
    v0 = base.version
    title = f"v{v0} → v{new.version}" if new else f"v{v0} (no new version)"
    L = [f"# Improvement loop: {title}", "", f"- Loop id: `{result['improve_id']}`  · status: **{result['status']}**",
         "- Models (pinned in every sub-run manifest):"]
    L += [f"  - {r}: `{m['provider']}/{m['model']}`" for r, m in result["models"].items()]
    cfg = result["config"]
    L += [f"- k = {cfg['k']} (full suite), {cfg['k_targeted']} (targeted & confirmation); critical policy: {cfg['critical_policy']}", ""]
    if "baseline" in result:
        b = result["baseline"]
        L += ["## Baseline", "", f"Run: `{b['run']}`", "",
              "| Split | pass^k | Avg pass rate | Critical | Invalid trials |", "|---|---|---|---|---|"]
        for s in ("train", "heldout"):
            st = b[s]
            L.append(f"| {s} | {st['pass_k']}/{st['scenarios']} | {st['avg_pass_rate']} | {st['critical']} | {st['invalid_trials']} |")
    if "triage" in result:
        L += ["", "## Triage", "", f"Summary: {result['triage']['summary']}", "",
              "| Scenario | Split | Trial | Check | Category |", "|---|---|---|---|---|"]
        L += [f"| {i['scenario_id']} | {i['split']} | {i['trial']} | `{i['check_id']}` | {i['category']} |"
              for i in result["triage"]["items"]]
        others = [i for i in result["triage"]["items"] if i["category"] != "prompt_fixable"]
        if others:
            L += ["", "**For a human (not sent to the reflector):**"]
            L += [f"- {i['category']}: {i['scenario_id']} t{i['trial']} `{i['check_id']}` — {i['evidence'][:160]}" for i in others]
        L += ["", f"Targets (prompt-fixable, train only): {result.get('targets')}"]
    if result.get("candidates") is not None:
        L += ["", "## Candidate patches", "",
              f"Targeted baseline pass rate (k={cfg['k_targeted']}): {result.get('targeted_baseline')}", "",
              "| # | Patch | Lint | Targeted | Full train / held-out | Status |", "|---|---|---|---|---|---|"]
        for c in result["candidates"]:
            p = Patch.model_validate(c["patch"])
            full = c.get("full")
            fs = (f"{full['train']['avg_pass_rate']} / {full['heldout']['avg_pass_rate']}" if full else "—")
            short = (f" _(shortened from {len(c['original_text'])} chars)_" if c.get("shortened")
                     else " _(shortening failed)_" if "shortened" in c else "")
            L.append(f"| {c['n']} | `{p.op}` {p.target or ''}: {p.text[:110]}{short} | {'; '.join(c['lint']) or 'ok'} | "
                     f"{_fmt_targeted(c)} | {fs} | **{c['status']}** |")
        for r in result.get("schema_rejected", []):
            L.append(f"| – | (malformed) | {r['reason']} | — | — | rejected_schema |")
        for c in result["candidates"]:
            if "gate" in c:
                L += ["", f"### Gate for candidate {c['n']}: {'ACCEPTED' if c['gate']['accepted'] else 'REJECTED'}", ""]
                L += [f"- {r}" for r in c["gate"]["reasons"]]
    if new is not None:
        acc = next(c for c in result["candidates"] if c["status"] == "accepted")
        L += ["", f"## Accepted patch → playbook v{new.version}", "",
              f"Rationale: {acc['patch']['rationale']}", "", f"Evidence: {acc['patch']['evidence']}", "", "```diff"]
        L += list(difflib.unified_diff(_pb_yaml(base), _pb_yaml(new), f"v{v0}.yaml", f"v{new.version}.yaml", lineterm=""))
        L += ["```", "", "## Per-scenario pass rate", "", f"| Scenario | v{v0} | v{new.version} |", "|---|---|---|"]
        for sid, r0 in result["baseline_per_scenario"].items():
            r1 = acc["full"]["per_scenario"].get(sid)
            L.append(f"| {sid} | {r0} | {r1} |")
    bud = result.get("budget_this_session", {})
    L += ["", "## Cost (this session)", "", f"- Model calls: {bud.get('total_calls')}, tokens: {bud.get('total_tokens')}, "
          f"time: {bud.get('elapsed_s')}s"]
    text = "\n".join(L) + "\n"
    (idir / "report.md").write_text(text)
    if new is not None:
        out = runs_root / f"v{v0}_vs_v{new.version}.md"
        out.write_text(text)
        return out
    return idir / "report.md"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run one iteration of the self-improvement loop.")
    ap.add_argument("--playbook", type=int, default=None, help="base playbook version (default: latest)")
    ap.add_argument("--baseline-run", default=None, help="reuse a completed `make eval` run id as the baseline")
    ap.add_argument("--improve-id", default=None, help="resume a previous loop")
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--k-targeted", type=int, default=3)
    ap.add_argument("--critical-policy", default="strict", choices=["strict", "no-new"])
    ap.add_argument("--workers", type=int, default=1, help="parallel trials (default 1 for free-tier limits)")
    a = ap.parse_args(argv)
    from llm.client import make_client
    from ..harness import build_models
    models = build_models()
    from llm.cache import ResponseCache
    from llm.config import role_config
    reflector = make_client("reflector", budget=models.budget, cache=ResponseCache())
    res = improve(models, reflector, base_version=a.playbook, improve_id=a.improve_id, baseline_run=a.baseline_run,
                  reflector_role=role_config("reflector").public(),
                  cfg=LoopConfig(k=a.k, k_targeted=a.k_targeted, critical_policy=a.critical_policy, workers=a.workers))
    return 0 if res["status"] in ("accepted", "nothing_to_fix", "no_candidate_accepted") else 3


if __name__ == "__main__":
    sys.exit(main())
