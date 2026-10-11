"""Offline demo of the improvement-loop MACHINERY with deterministic fake models (no network, no key).

This is NOT a measure of agent quality: the fake agent is scripted to make one mistake (claiming success
after a slot is taken) unless the playbook has a rule against it. It shows, reproducibly, that triage,
lint, targeted screening, the regression gate and the report work. Real results come from `make improve`.

Output:
  runs/offline-demo/v0_vs_v1.OFFLINE-DEMO.md   the labelled report (the only committed file)
  runs/offline-demo/work/                      all sub-runs, traces, raw report (gitignored, regenerable)
Playbooks are written to a temp dir; src/agent/playbook is never touched. The script never deletes or
renames files (it also runs in sandboxes that forbid deletion): it clears work/ when it can, and otherwise
resumes the identical deterministic run from its checkpoints.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from agent.playbook import PLAYBOOK_DIR  # noqa: E402
from evals.harness import ModelSet  # noqa: E402
from evals.improve.loop import LoopConfig, improve  # noqa: E402
from evals.scenario import load_scenarios  # noqa: E402
from fakes import (GOOD_PATCH, OVERFIT_PATCH, REGRESSING_PATCH, all_pass_judge, playbook_sensitive_agent,  # noqa: E402
                   scripted_reflector, structured_patient)
from llm.budget import Budget  # noqa: E402

BANNER = ("> **OFFLINE DEMO — deterministic fake models.** This validates the improvement-loop machinery "
          "(triage → lint → targeted → gate → report). It says nothing about real agent quality; see "
          "`runs/v0_vs_v1.md` from `make improve` for the real-model result.\n\n")


def main() -> int:
    out = ROOT / "runs" / "offline-demo"
    work = out / "work"
    shutil.rmtree(work, ignore_errors=True)
    roles = {r: {"role": r, "provider": "fake", "model": f"deterministic-{r}"} for r in ("agent", "simulator", "guard", "judge")}
    models = ModelSet(agent=playbook_sensitive_agent(), simulator=structured_patient(), guard=None,
                      judge=all_pass_judge(), budget=Budget(), roles=roles)
    scns = [s for s in load_scenarios() if s.id in ("happy_book_gp", "slot_taken_at_confirm", "ho_no_slots")]
    with tempfile.TemporaryDirectory() as tmp:
        pbdir = Path(tmp)
        shutil.copy(PLAYBOOK_DIR / "v0.yaml", pbdir / "v0.yaml")
        res = improve(models, scripted_reflector([OVERFIT_PATCH, REGRESSING_PATCH, GOOD_PATCH]), base_version=0,
                      scenarios=scns, improve_id="offline-demo", cfg=LoopConfig(workers=1), runs_root=work,
                      playbook_dir=pbdir)
    final = out / "v0_vs_v1.OFFLINE-DEMO.md"
    final.write_text(BANNER + (work / "v0_vs_v1.md").read_text().replace("improve/offline-demo", "work/improve/offline-demo"))
    print(f"\nstatus: {res['status']}  ->  {final.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
