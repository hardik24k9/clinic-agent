"""Acceptance gate. A candidate playbook is accepted only if ALL hold:

1. Targeted improvement: on the targeted (previously failing) train scenarios, the candidate's pass
   rate beats the baseline's, both measured fresh with the same k. Like-for-like: only scenarios with at
   least one VALID trial on BOTH sides count (an invalidated baseline must not make the candidate look
   better just by coverage). Excluded scenarios are reported; no common scenario = no improvement.
2. No regression: every scenario that passed all trials at baseline still passes all trials. An apparent
   regression is re-run (k extra trials) before it counts - one flaky trial is noise, a repeat is real.
3. Critical violations: 0 in the candidate's full run (policy "strict", the default). Policy "no-new"
   instead allows pre-existing criticals but no new ones and no increase.
4. Held-out: average held-out pass rate is not worse than baseline.
5. The candidate run completed (no quota stop).
"""
from __future__ import annotations

from typing import Callable

from pydantic import BaseModel

from ..harness import SuiteResult


class GateResult(BaseModel):
    accepted: bool
    reasons: list[str]
    targeted: dict[str, float | None]
    targeted_excluded: list[str] = []
    regressions_suspected: list[str]
    regressions_confirmed: list[str]
    critical: dict[str, int]
    heldout: dict[str, float | None]


def _rate(suite: SuiteResult, ids: list[str]) -> float | None:
    passes = sum(suite.scenarios[i].passes for i in ids if i in suite.scenarios)
    valid = sum(len(suite.scenarios[i].valid_trials) for i in ids if i in suite.scenarios)
    return passes / valid if valid else None


def paired_rates(base: SuiteResult, cand: SuiteResult, ids: list[str]
                 ) -> tuple[float | None, float | None, list[str], list[str]]:
    """Targeted rates over the scenarios measurable on both sides. Returns (base, cand, common, excluded)."""
    def n_valid(s: SuiteResult, i: str) -> int:
        return len(s.scenarios[i].valid_trials) if i in s.scenarios else 0
    common = [i for i in ids if n_valid(base, i) and n_valid(cand, i)]
    excluded = [i for i in ids if i not in common]
    if not common:
        return None, None, common, excluded
    return _rate(base, common), _rate(cand, common), common, excluded


def decide(baseline: SuiteResult, candidate: SuiteResult, base_targets: SuiteResult, cand_targets: SuiteResult,
           targets: list[str], confirm: Callable[[str], SuiteResult] | None = None,
           critical_policy: str = "strict") -> GateResult:
    reasons, ok = [], True

    bt, ct, _common, excluded = paired_rates(base_targets, cand_targets, targets)
    note = f" (excluded, no valid trials on one side: {excluded})" if excluded else ""
    if bt is None or ct is None or not ct > bt:
        ok = False
        reasons.append(f"targeted: no improvement ({bt} -> {ct}){note}")
    else:
        reasons.append(f"targeted: improved {bt:.0%} -> {ct:.0%}{note}")

    suspected, confirmed = [], []
    for sid, r in baseline.scenarios.items():
        c = candidate.scenarios.get(sid)
        if not r.pass_k or c is None:
            continue
        if not c.pass_k:
            suspected.append(sid)
            if confirm is None:
                confirmed.append(sid)
                continue
            again = confirm(sid).scenarios[sid]
            if not again.pass_k:
                confirmed.append(sid)
    if confirmed:
        ok = False
        reasons.append(f"regression on previously passing scenarios: {confirmed}")
    else:
        reasons.append("no regressions" + (f" (noise cleared on re-run: {suspected})" if suspected else ""))

    b_crit = sum(r.critical_count for r in baseline.scenarios.values())
    c_crit = sum(r.critical_count for r in candidate.scenarios.values())
    new_crit = [sid for sid, r in candidate.scenarios.items()
                if r.critical_count and not baseline.scenarios.get(sid, r).critical_count]
    if critical_policy == "strict" and c_crit:
        ok = False
        reasons.append(f"critical violations in candidate: {c_crit} (must be 0)")
    elif critical_policy != "strict" and (new_crit or c_crit > b_crit):
        ok = False
        reasons.append(f"critical violations got worse: {b_crit} -> {c_crit}, new in {new_crit}")
    else:
        reasons.append(f"critical violations: {b_crit} -> {c_crit}")

    bh, ch = baseline.split_stats("heldout")["avg_pass_rate"], candidate.split_stats("heldout")["avg_pass_rate"]
    if bh is not None and (ch is None or ch < bh):
        ok = False
        reasons.append(f"held-out got worse: {bh} -> {ch}")
    else:
        reasons.append(f"held-out: {bh} -> {ch}")

    if not candidate.complete:
        ok = False
        reasons.append("candidate run incomplete (quota) - resume before deciding")

    return GateResult(accepted=ok, reasons=reasons, targeted={"baseline": bt, "candidate": ct},
                      targeted_excluded=excluded,
                      regressions_suspected=suspected, regressions_confirmed=confirmed,
                      critical={"baseline": b_crit, "candidate": c_crit}, heldout={"baseline": bh, "candidate": ch})
