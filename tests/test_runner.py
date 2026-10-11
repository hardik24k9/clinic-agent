"""Agent loop with a deterministic fake model: no network, no keys."""
import json

from agent.guard import EmergencyGuard
from agent.playbook import load_playbook
from agent.prompt import render_system_prompt
from agent.runner import HANDOFF_REPLY, MAX_STEPS_PER_TURN, STEP_LIMIT_REPLY, AgentRunner
from agent.trace import Tracer
from llm.fake import FakeLLM, call, say


def _last_tool_result(messages):
    for m in reversed(messages):
        if m["role"] == "tool":
            return json.loads(m["content"])
        if m["role"] == "user":
            return None
    return None


def booking_agent(messages, tools):
    """A tiny scripted 'agent': verify -> search -> propose -> (patient says yes) -> confirm."""
    user_turns = [m["content"] for m in messages if m["role"] == "user"]
    last = _last_tool_result(messages)
    n = len(user_turns)
    if n == 1:
        return say("Sure. What's your full name and date of birth?")
    if n == 2:
        if last is None:
            return call("verify_patient", full_name="Anita Rao", dob="1984-03-02")
        if "slots" not in (last.get("data") or {}) and last.get("data", {}).get("verified"):
            return call("search_slots", date_from="2026-10-13", date_to="2026-10-13", specialty="general_medicine")
        if "slots" in last.get("data", {}):
            return call("propose_booking", slot_id=last["data"]["slots"][0]["slot_id"])
        return say(f"I can book: {last['data']['summary']}. Shall I confirm?")
    if n == 3:
        if last is None:
            token = json.loads(messages[0]["content"].split("<session_state>")[1].split("</session_state>")[0]
                               )["pending_action"]["token"]
            return call("confirm", token=token)
        return say("Done, you're booked." if last["ok"] else "Sorry, that didn't go through.")
    return say("Anything else?")


def _runner(ctx, responder, guard=None):
    return AgentRunner(ctx, FakeLLM(responder), load_playbook(0), guard=guard, tracer=Tracer())


def test_full_booking_conversation_writes_exactly_one_appointment(ctx):
    r = _runner(ctx, booking_agent)
    before = ctx.store.snapshot()
    r.handle("Hi, I'd like a GP appointment tomorrow")
    reply = r.handle("Anita Rao, 2 March 1984")
    assert "Shall I confirm" in reply and ctx.store.snapshot() == before
    assert "booked" in r.handle("Yes please")
    from clinic.store import ClinicStore
    assert len(ClinicStore.diff(before, ctx.store.snapshot())) == 1
    types = [e["type"] for e in r.tracer.events]
    assert types.count("patient") == 3 and "tool" in types and types[-1] == "state"


def test_guard_flag_escalates_in_code_same_turn_without_model(ctx):
    """The hand-off is enforced in code; the model is never asked what to do in an emergency."""
    llm_calls = []

    def agent(messages, tools):
        llm_calls.append(1)
        return say("Let me verify you first.")
    r = _runner(ctx, agent, guard=EmergencyGuard(None))
    reply = r.handle("I have chest pain and my arm is numb")
    assert llm_calls == [] and ctx.state.emergency and ctx.state.escalated
    assert "112" in reply and "emergency department" in reply
    tools = [e for e in r.tracer.events if e["type"] == "tool"]
    assert len(tools) == 1 and tools[0]["name"] == "escalate_to_human" and tools[0]["ok"]
    assert tools[0]["args"]["urgency"] == "emergency" and tools[0]["enforced"] is True
    assert tools[0]["turn"] == [e for e in r.tracer.events if e["type"] == "guard"][0]["turn"]
    # later turns: fixed hand-off, no model call, no tools
    assert r.handle("ok but can you still book me?") == HANDOFF_REPLY and llm_calls == []


def test_self_harm_gets_specific_caring_reply(ctx):
    from agent.runner import SELF_HARM_REPLY
    r = _runner(ctx, lambda m, t: say("x"), guard=EmergencyGuard(None))
    assert r.handle("honestly I want to die") == SELF_HARM_REPLY
    assert ctx.state.escalated


def test_guard_flag_mid_booking_drops_pending_action(ctx):
    r = _runner(ctx, lambda m, t: say("x"), guard=EmergencyGuard(None))
    ctx.state.pending_action = None
    r.handle("I have crushing chest pain right now")
    assert ctx.state.pending_action is None and ctx.state.escalated


def test_after_escalation_no_model_call_and_fixed_reply(ctx):
    llm_calls = []

    def agent(messages, tools):
        llm_calls.append(1)
        return say("x")
    r = _runner(ctx, agent)
    ctx.state.escalated = True
    assert r.handle("hello?") == HANDOFF_REPLY and llm_calls == []


def test_invalid_json_arguments_become_structured_error(ctx):
    from llm.client import LLMResponse, ToolCall
    replies = iter([LLMResponse(content="", tool_calls=[ToolCall(id="1", name="confirm", arguments=None,
                                                                   raw_arguments="{oops")]),
                    say("Sorry about that.")])
    r = _runner(ctx, lambda m, t: next(replies))
    r.handle("confirm it")
    tool_ev = [e for e in r.tracer.events if e["type"] == "tool"][0]
    assert tool_ev["code"] == "INVALID_ARGUMENT"


def test_step_limit_stops_runaway_tool_loops(ctx):
    r = _runner(ctx, lambda m, t: call("list_my_appointments"))
    assert r.handle("loop forever") == STEP_LIMIT_REPLY
    assert sum(e["type"] == "tool" for e in r.tracer.events) == MAX_STEPS_PER_TURN


def test_prompt_contains_rules_state_and_token(ctx, store, clock):
    from agent.state import PendingAction
    ctx.state.pending_action = PendingAction(kind="book", token="CONF-ABC", patient_id="P001",
                                             slot_id="x", proposed_at_turn=1, summary="Book something")
    text = render_system_prompt(load_playbook(0), ctx.state, store, clock)
    assert "R01" in text and "CONF-ABC" in text and "Monday 12 October 2026" in text
    assert "EMERGENCY MODE" not in text


def test_trace_never_contains_keys(ctx, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "Z" * 40)
    r = _runner(ctx, lambda m, t: say("echo gsk_" + "Z" * 40))
    r.handle("hi")
    assert "ZZZZ" not in json.dumps(r.tracer.events)


def test_playbook_v0_is_valid_and_covers_every_tool():
    from agent.tool_schemas import PARAMETERS
    pb = load_playbook(0)
    assert set(pb.tool_descriptions) == set(PARAMETERS)


def test_identical_failing_call_is_not_reexecuted(ctx):
    """tool_timeout t2: the model repeated verify_patient with the same bad DOB until the step limit."""
    n = {"i": 0}

    def agent(messages, tools):
        n["i"] += 1
        if n["i"] <= 3:
            return call("verify_patient", full_name="Anita Rao", dob="sometime in 84")
        return say("Could you tell me your date of birth with the month name?")
    r = _runner(ctx, agent)
    reply = r.handle("Anita Rao, born sometime in 84")
    codes = [e["code"] for e in r.tracer.events if e["type"] == "tool"]
    assert codes == ["INVALID_ARGUMENT", "REPEATED_CALL", "REPEATED_CALL"]
    assert "month name" in reply and ctx.state.failed_verifications == 0
