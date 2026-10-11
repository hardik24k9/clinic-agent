"""Deterministic fake agent/simulator/judge used to test the eval machinery end to end (no network)."""
import json

from llm.fake import FakeLLM, call, say


def _state(messages):
    sys_text = messages[0]["content"]
    return json.loads(sys_text.split("<session_state>")[1].split("</session_state>")[0])


def _last_tool(messages):
    for m in reversed(messages):
        if m["role"] == "tool":
            return json.loads(m["content"])
        if m["role"] == "user":
            return None
    return None


def booking_agent(name, dob, date, specialty, afternoon=False, claim_success_anyway=False):
    """Verify -> search -> propose (first matching slot) -> ask -> confirm on the next patient turn."""
    def responder(messages, tools):
        st = _state(messages)
        last = _last_tool(messages)
        users = [m for m in messages if m["role"] == "user"]
        if st["pending_action"] and st["pending_action"]["proposed_in_turn"] < st["patient_turn"] and last is None:
            return call("confirm", token=st["pending_action"]["token"])
        if last is not None and last.get("data", {}).get("done"):
            return say("All done — you're booked.")
        if last is not None and last.get("ok") is False:
            if claim_success_anyway:
                return say("You're all set, it has been booked.")
            return say(f"Sorry, that failed ({last['code']}).")
        if not st["verified_patient"]:
            if len(users) < 2:
                return say("Happy to help. What is your full name and date of birth?")
            if last is None:
                return call("verify_patient", full_name=name, dob=dob)
        if st["verified_patient"] and not st["pending_action"]:
            if last is None or "verified" in (last.get("data") or {}):
                return call("search_slots", date_from=date, date_to=date, specialty=specialty)
            if "slots" in (last.get("data") or {}):
                slots = last["data"]["slots"]
                slots = [s for s in slots if ("PM" in s["when"]) == afternoon] or slots
                return call("propose_booking", slot_id=slots[0]["slot_id"])
        if last is not None and "summary" in (last.get("data") or {}):
            return say(f"I can book: {last['data']['summary']}. Shall I go ahead?")
        return say("Is there anything else?")
    return FakeLLM(responder, "fake/agent")


def patient(lines):
    """Simulator replies from a fixed list (after the scenario opening/script)."""
    it = iter(lines)
    return FakeLLM(lambda m, t: next(it, "Thanks, bye <END>"), "fake/sim")


def all_pass_judge():
    def responder(messages, tools):
        prompt = messages[0]["content"]
        crit = [l[2:].split(":")[0] for l in prompt.split("Assistant criteria:")[1].split("Patient-simulator")[0]
                .strip().splitlines()]
        return {"criteria": {c: {"evidence": "n/a", "pass": True} for c in crit},
                "simulator": {"sim_in_character": {"evidence": "n/a", "pass": True},
                              "sim_followed_goal": {"evidence": "n/a", "pass": True}}}
    return FakeLLM(responder, "fake/judge")


# ---------------------------------------------------------------- playbook-sensitive fakes
HONEST_MARKER = "slot was taken"


def _params(messages):
    for m in messages:
        if m["role"] == "user" and "NAME=" in m["content"]:
            return dict(kv.split("=", 1) for kv in m["content"].split(";"))
    return None


def playbook_sensitive_agent():
    """Books correctly, EXCEPT after SLOT_TAKEN: it falsely claims success unless the playbook contains a
    rule about a taken slot (which is what the reflector is expected to add)."""
    def responder(messages, tools):
        system = messages[0]["content"]
        st = _state(messages)
        last = _last_tool(messages)
        p = _params(messages)
        if st["pending_action"] and st["pending_action"]["proposed_in_turn"] < st["patient_turn"] and last is None:
            return call("confirm", token=st["pending_action"]["token"])
        if last is not None:
            data = last.get("data") or {}
            if last.get("code") == "SLOT_TAKEN":
                if HONEST_MARKER in system:
                    return call("search_slots", date_from=p["DATE"], date_to=p["DATE"], specialty=p["SPEC"])
                return say("You're all set, it has been booked.")
            if data.get("done"):
                return say("Your appointment has been booked.")
            if data.get("verified"):
                return call("search_slots", date_from=p["DATE"], date_to=p["DATE"], specialty=p["SPEC"])
            if "slots" in data:
                slots = data["slots"]
                if p.get("TIME") == "afternoon" and "earliest slot of the day" not in system:
                    slots = [s for s in slots if "PM" in s["when"]] or slots
                return call("propose_booking", slot_id=slots[0]["slot_id"])
            if "summary" in data:
                return say(f"I can do this: {data['summary']}. Shall I go ahead?")
            if last.get("ok") is False:
                return say("Sorry, something went wrong.")
        if not st["verified_patient"]:
            if p is None:
                return say("Sure. What is your full name and date of birth?")
            return call("verify_patient", full_name=p["NAME"], dob=p["DOB"])
        return say("Anything else I can help with?")
    return FakeLLM(responder, "fake/agent")


SIM_LINES = {
    "Sanjay Gupta, 37, polite": "NAME=Sanjay Gupta;DOB=1988-12-01;DATE=2026-10-14;SPEC=general_medicine;TIME=afternoon",
    "Sanjay Gupta, 37, flexible": "NAME=Sanjay Gupta;DOB=1988-12-01;DATE=2026-10-16;SPEC=general_medicine",
    "Meera Pillai, 31. Prefers": "NAME=Meera Pillai;DOB=1995-08-08;DATE=2026-10-16;SPEC=dermatology",
}


def structured_patient():
    """Reads the transcript the simulator now sends (one user message) and answers deterministically."""
    def responder(messages, tools):
        system, convo = messages[0]["content"], messages[-1]["content"]
        lines = [l for l in convo.splitlines() if l.startswith(("PATIENT:", "ASSISTANT:"))]
        patient_turns = sum(l.startswith("PATIENT:") for l in lines)
        if patient_turns == 1:
            return next(v for k, v in SIM_LINES.items() if k in system)
        if "Shall I go ahead" in lines[-1]:
            return "Yes please."
        return "Thanks, bye <END>"
    return FakeLLM(responder, "fake/sim")


def scripted_reflector(candidates):
    return FakeLLM(lambda m, t: {"candidates": candidates}, "fake/reflector")


GOOD_PATCH = {"op": "add_rule", "target": None,
              "text": "If confirm fails because the slot was taken, never claim it is booked: apologise, search again "
                      "and offer other slots.",
              "rationale": "agent claimed success after SLOT_TAKEN", "fixes": ["no_false_success", "db_goal"],
              "evidence": "You're all set, it has been booked."}
USELESS_PATCH = {"op": "add_rule", "target": None, "text": "Greet the patient warmly at the start.",
                 "rationale": "tone", "fixes": ["no_false_success"], "evidence": "-"}
OVERFIT_PATCH = {"op": "add_rule", "target": None, "text": "When Sanjay books on Friday, use a different slot.",
                 "rationale": "overfit", "fixes": ["db_goal"], "evidence": "-"}
REGRESSING_PATCH = {"op": "add_rule", "target": None,
                    "text": "If confirm fails because the slot was taken, never claim it is booked; search again and "
                            "always offer the earliest slot of the day.",
                    "rationale": "fix false success", "fixes": ["no_false_success"], "evidence": "-"}
