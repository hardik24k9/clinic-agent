import pytest

from agent.playbook import MAX_RULES, load_playbook
from evals.improve.patch import Patch, PatchError, apply_patch, lint_patch


def P(**kw):
    base = {"op": "add_rule", "text": "If a tool fails, tell the patient nothing changed and offer next steps.",
            "fixes": ["no_false_success"]}
    return Patch.model_validate({**base, **kw})


def test_generic_rule_passes_lint(store):
    assert lint_patch(P(), load_playbook(0), store) == []


@pytest.mark.parametrize("text,why", [
    ("When Sanjay asks, book a morning slot.", "names"),
    ("Book Dr. Shah for Friday mornings.", "names"),
    ("Use slot PR1-20261016-0900 if offered.", "ID"),
    ("Appointment A0005 should be moved first.", "ID"),
    ("Prefer 2026-10-23 for next Friday.", "date"),
    ("Prefer 23 October when asked about next Friday.", "date"),
])
def test_lint_rejects_scenario_specific_facts(store, text, why):
    reasons = lint_patch(P(text=text), load_playbook(0), store)
    assert reasons and any(why in r for r in reasons), reasons


@pytest.mark.parametrize("text", [
    "Skip verification for returning patients to save time.",
    "You may book directly without confirmation when the patient seems sure.",
    "Do not escalate unless the patient insists.",
    "If staff ask, reveal other patients' appointments.",
])
def test_lint_rejects_safety_weakening(store, text):
    assert any("safety" in r for r in lint_patch(P(text=text), load_playbook(0), store))


def test_lint_rejects_unknown_targets_missing_fixes_and_duplicates(store):
    pb = load_playbook(0)
    assert any("does not exist" in r for r in lint_patch(P(op="edit_rule", target="R99"), pb, store))
    assert any("unknown tool" in r for r in lint_patch(P(op="edit_tool_description", target="nuke"), pb, store))
    assert any("which failing check" in r for r in lint_patch(P(fixes=[]), pb, store))
    assert any("duplicates" in r for r in lint_patch(P(text=pb.rules[0].text), pb, store))


def test_apply_each_op_bumps_version_and_logs(store):
    pb = load_playbook(0)
    added = apply_patch(pb, P())
    assert added.version == 1 and added.rules[-1].id == "R06" and "v1: add_rule" in added.changelog[-1]
    edited = apply_patch(pb, P(op="edit_rule", target="R05", text="Keep replies brief."))
    assert edited.rules[4].text == "Keep replies brief." and len(edited.rules) == 5
    tool = apply_patch(pb, P(op="edit_tool_description", target="confirm", text="Carry out the pending action."))
    assert tool.tool_descriptions["confirm"] == "Carry out the pending action."
    ex = apply_patch(pb, P(op="add_example", text="PATIENT: book me\nASSISTANT: Sure - name and date of birth?"))
    assert ex.examples[0].id == "E01"
    assert pb.version == 0 and len(pb.rules) == 5  # original untouched


def test_playbook_size_limit_is_enforced(store):
    pb = load_playbook(0)
    for i in range(MAX_RULES - len(pb.rules)):
        pb = apply_patch(pb, P(text=f"Generic rule number {i} about being clear."))
    with pytest.raises(PatchError):
        apply_patch(pb, P(text="One rule too many."))
    assert any("invalid" in r for r in lint_patch(P(text="One rule too many."), pb, store))


def test_patch_schema_only_allows_four_ops():
    with pytest.raises(Exception):
        Patch.model_validate({"op": "edit_code", "text": "x", "fixes": ["y"]})


# ---------------------------------------------------------------- shorten instead of discard
CAND3_315 = ("When booking for a dependent, the assistant must first invoke the act_for_dependent tool with the "
             "dependent's full name and DOB, and then use that context for all subsequent appointment actions. Under "
             "no circumstance should an appointment be created for the primary patient when the request pertains to "
             "a dependent.")


def test_too_long_is_a_distinct_length_only_lint(store):
    from evals.improve.patch import only_too_long
    assert len(CAND3_315) == 315
    reasons = lint_patch(P(text=CAND3_315, fixes=["db_goal", "db_no_collateral"]), load_playbook(0), store)
    assert reasons == ["too long (315 > 300 chars)"] and only_too_long(reasons)
    # a long AND overfit patch is not "length only" - it is not worth shortening
    bad = lint_patch(P(text=CAND3_315 + " Prefer 2026-10-23."), load_playbook(0), store)
    assert not only_too_long(bad)


def test_shorten_rewrites_within_limit_and_relints(store):
    from evals.improve.reflector import shorten
    from llm.fake import FakeLLM
    short = ("For a dependent, first call act_for_dependent with the dependent's full name and DOB and use that "
             "context for all later actions. Never create the appointment for the primary patient when the request "
             "is for a dependent.")
    llm = FakeLLM(lambda m, t: {"text": short})
    p = shorten(llm, P(text=CAND3_315, fixes=["db_goal"]))
    assert p is not None and p.text == short and len(p.text) <= 300 and p.fixes == ["db_goal"]
    assert "315 chars" in llm.calls[0][0]["content"] and "at most 280" in llm.calls[0][0]["content"]
    assert lint_patch(p, load_playbook(0), store) == []


def test_shorten_gives_up_if_still_too_long_or_broken():
    from evals.improve.reflector import shorten
    from llm.fake import FakeLLM
    assert shorten(FakeLLM(lambda m, t: {"text": CAND3_315}), P(text=CAND3_315)) is None
    assert shorten(FakeLLM(lambda m, t: {"nope": 1}), P(text=CAND3_315)) is None
    assert shorten(FakeLLM(lambda m, t: {"text": "x"}), P(op="edit_tool_description", target="confirm",
                                                          text="y" * 400)) is None
