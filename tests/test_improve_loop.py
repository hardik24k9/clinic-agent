"""The whole improvement loop, end to end, with deterministic fakes (no network, no keys).

Setup: the fake agent books correctly except after SLOT_TAKEN, where it falsely claims success unless the
playbook has a rule about a taken slot. The fake reflector proposes three patches: one overfit (names a
patient -> lint), one useless (-> no targeted improvement), one real fix (-> accepted as v1).
"""
import shutil

from agent.playbook import PLAYBOOK_DIR, load_playbook
from evals.harness import ModelSet
from evals.improve.loop import LoopConfig, improve
from evals.scenario import load_scenarios
from fakes import (GOOD_PATCH, HONEST_MARKER, OVERFIT_PATCH, REGRESSING_PATCH, USELESS_PATCH, all_pass_judge, playbook_sensitive_agent,
                   scripted_reflector, structured_patient)
from llm.budget import Budget

ROLES = {r: {"role": r, "provider": "fake", "model": r} for r in ("agent", "simulator", "guard", "judge")}
IDS = ["happy_book_gp", "slot_taken_at_confirm", "ho_no_slots"]


def _setup(tmp_path):
    pbdir = tmp_path / "playbook"
    pbdir.mkdir()
    shutil.copy(PLAYBOOK_DIR / "v0.yaml", pbdir / "v0.yaml")
    models = ModelSet(agent=playbook_sensitive_agent(), simulator=structured_patient(), guard=None,
                      judge=all_pass_judge(), budget=Budget(), roles=ROLES)
    return pbdir, models, [s for s in load_scenarios() if s.id in IDS]


def test_gate_rejects_a_fix_that_regresses_the_happy_path_then_accepts_the_clean_one(tmp_path):
    pbdir, models, scns = _setup(tmp_path)
    res = improve(models, scripted_reflector([OVERFIT_PATCH, REGRESSING_PATCH, GOOD_PATCH]), base_version=0,
                  scenarios=scns, improve_id="reg", cfg=LoopConfig(workers=1), runs_root=tmp_path / "runs",
                  playbook_dir=pbdir, log=lambda *_: None)
    c = {x["n"]: x for x in res["candidates"]}
    assert c[2]["status"] == "rejected_gate" and c[2]["gate"]["regressions_confirmed"] == ["happy_book_gp"]
    assert c[3]["status"] == "accepted"
    assert "earliest" not in load_playbook(1, pbdir).rules[-1].text


def test_loop_accepts_the_real_fix_and_rejects_the_others(tmp_path):
    pbdir, models, scns = _setup(tmp_path)
    reflector = scripted_reflector([OVERFIT_PATCH, USELESS_PATCH, GOOD_PATCH])
    res = improve(models, reflector, base_version=0, scenarios=scns, improve_id="it", cfg=LoopConfig(workers=1),
                  runs_root=tmp_path / "runs", playbook_dir=pbdir, log=lambda *_: None)

    # baseline: the slot-taken scenario fails with a critical false-success claim
    assert res["baseline"]["train"]["critical"] >= 1
    assert res["targets"] == ["slot_taken_at_confirm"]
    statuses = [c["status"] for c in res["candidates"]]
    assert statuses == ["rejected_lint", "rejected_no_targeted_improvement", "accepted"], statuses
    gate = res["candidates"][2]["gate"]
    assert gate["accepted"] and gate["critical"]["candidate"] == 0 and gate["regressions_confirmed"] == []
    assert gate["heldout"]["candidate"] >= gate["heldout"]["baseline"]

    v1 = load_playbook(1, pbdir)
    assert v1.version == 1 and HONEST_MARKER in v1.rules[-1].text
    assert load_playbook(0, pbdir).version == 0          # v0 untouched
    report = (tmp_path / "runs" / "v0_vs_v1.md").read_text()
    assert "Accepted patch" in report and "```diff" in report and "rejected_lint" in report
    # held-out scenarios were never sent to the reflector
    assert "ho_no_slots" not in reflector.calls[0][0]["content"]
    # every sub-run pinned its models
    import json
    for m in (tmp_path / "runs" / "improve" / "it" / "evals").glob("*/manifest.json"):
        assert json.loads(m.read_text())["models"] == ROLES


def test_loop_stops_when_nothing_is_prompt_fixable(tmp_path):
    pbdir, models, scns = _setup(tmp_path)
    scns = [s for s in scns if s.id != "slot_taken_at_confirm"]
    res = improve(models, scripted_reflector([]), base_version=0, scenarios=scns, improve_id="n",
                  cfg=LoopConfig(workers=1), runs_root=tmp_path / "runs", playbook_dir=pbdir, log=lambda *_: None)
    assert res["status"] == "nothing_to_fix" and not (pbdir / "v1.yaml").exists()


def test_loop_rejects_everything_if_no_patch_helps(tmp_path):
    pbdir, models, scns = _setup(tmp_path)
    res = improve(models, scripted_reflector([USELESS_PATCH, OVERFIT_PATCH]), base_version=0, scenarios=scns,
                  improve_id="r", cfg=LoopConfig(workers=1), runs_root=tmp_path / "runs", playbook_dir=pbdir,
                  log=lambda *_: None)
    assert res["status"] == "no_candidate_accepted" and not (pbdir / "v1.yaml").exists()
    assert any("expected 3" in r["reason"] for r in res["schema_rejected"])


def test_loop_shortens_an_over_long_but_good_patch_and_accepts_it(tmp_path):
    """The right fix written 30 chars too long is shortened once, re-linted, and goes through the gate."""
    pbdir, models, scns = _setup(tmp_path)
    long_good = dict(GOOD_PATCH, text=GOOD_PATCH["text"] + " Keep your wording calm and clear." * 7)
    assert len(long_good["text"]) > 300

    def responder(messages, tools):
        if messages[0]["content"].startswith("Rewrite this"):
            return {"text": GOOD_PATCH["text"]}
        return {"candidates": [OVERFIT_PATCH, USELESS_PATCH, long_good]}
    from llm.fake import FakeLLM
    res = improve(models, FakeLLM(responder, "fake/reflector"), base_version=0, scenarios=scns, improve_id="sh",
                  cfg=LoopConfig(workers=1), runs_root=tmp_path / "runs", playbook_dir=pbdir, log=lambda *_: None)
    c3 = res["candidates"][2]
    assert c3["shortened"] is True and c3["original_text"] == long_good["text"]
    assert c3["original_lint"][0].startswith("too long") and c3["lint"] == []
    assert c3["status"] == "accepted"
    assert "shortened from" in (tmp_path / "runs" / "v0_vs_v1.md").read_text()


def test_reflector_quota_stops_incomplete_and_is_retried_on_resume(tmp_path):
    """A provider quota 429 was swallowed as '0 patches' and that empty answer was cached."""
    from llm.client import QuotaExhausted
    from llm.fake import FakeLLM
    pbdir, models, scns = _setup(tmp_path)

    def broke(m, t):
        raise QuotaExhausted("groq/x: rate limit/quota reached - provider says retry in ~40 min")
    kw = dict(base_version=0, scenarios=scns, improve_id="q", cfg=LoopConfig(workers=1),
              runs_root=tmp_path / "runs", playbook_dir=pbdir, log=lambda *_: None)
    res = improve(models, FakeLLM(broke, "fake/reflector"), **kw)
    assert res["status"] == "incomplete"
    # a stale empty candidates.json from the old bug must not block a resume either
    (tmp_path / "runs" / "improve" / "q" / "candidates.json").write_text('{"patches": [], "schema_rejected": []}')
    res = improve(models, scripted_reflector([OVERFIT_PATCH, USELESS_PATCH, GOOD_PATCH]), **kw)
    assert res["status"] == "accepted", res["status"]
