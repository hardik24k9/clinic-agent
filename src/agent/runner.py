"""The agent loop.

For each patient message:
  1. turn += 1 (this is what lets confirm() know the patient replied)
  2. emergency guard (independent of the agent) -> may set state.emergency
  3. if already escalated: fixed hand-off reply, no model call
  4. model -> tool calls -> tool results -> model ... (max 8 steps), system prompt re-rendered each
     step so the model always sees the current state
Every event is traced. Tool calls go through call_tool(), which never raises.
"""
from __future__ import annotations

import json
import os
from typing import Any

from llm.client import ChatModel, LLMError, QuotaExhausted

from .guard import EmergencyGuard
from .playbook import Playbook
from .prompt import render_system_prompt
from .tool_schemas import openai_tools
from .tools import ToolContext, ToolResult, call_tool
from .trace import Tracer

MAX_STEPS_PER_TURN = 8

HANDOFF_REPLY = ("This conversation has been passed to our clinic staff, and someone will contact you shortly. "
                 "If this is an emergency, please call 112 or go to the nearest emergency department now.")
# Emergency escalation is enforced in code, not left to the model: a model may keep verifying or searching
# after a self-harm disclosure, and a deterministic hand-off removes that failure mode entirely.
EMERGENCY_REPLY = ("I'm concerned about what you've described. Please call 112 now or go to the nearest emergency "
                   "department straight away. I've alerted our clinic staff as an emergency and someone will "
                   "contact you shortly. I won't continue with the booking.")
SELF_HARM_REPLY = ("I'm really sorry you're feeling this way, and I'm glad you told me. Your safety matters most "
                   "right now: please call 112, or go to the nearest emergency department, or reach out to someone "
                   "you trust to be with you. I've alerted our clinic staff as an emergency and someone will "
                   "contact you shortly.")
ERROR_REPLY = "Sorry, something went wrong on my side. Could you repeat that?"
STEP_LIMIT_REPLY = ("Sorry, I'm having trouble completing that. I've noted your request; a member of our staff "
                    "can help you. Would you like me to hand you over?")


class AgentRunner:
    def __init__(self, ctx: ToolContext, llm: ChatModel, playbook: Playbook, *,
                 guard: EmergencyGuard | None = None, tracer: Tracer | None = None,
                 temperature: float | None = None) -> None:
        self.ctx = ctx
        self.llm = llm
        self.playbook = playbook
        self.guard = guard or EmergencyGuard(None)
        self.tracer = tracer or Tracer()
        self.temperature = float(os.environ.get("AGENT_TEMPERATURE", "0.2")) if temperature is None else temperature
        self.history: list[dict[str, Any]] = []
        self.tools = openai_tools(playbook)

    @property
    def state(self):
        return self.ctx.state

    def _trace(self, type_: str, **data: Any) -> None:
        self.tracer.emit(type_, self.state.turn, **data)

    def handle(self, patient_text: str) -> str:
        s = self.state
        s.begin_patient_turn()
        self._trace("patient", text=patient_text)

        g = self.guard.check(patient_text)
        self._trace("guard", flagged=g.flagged, category=g.category, source=g.source, degraded=g.degraded)
        if g.flagged and not s.emergency:
            s.emergency = True
        if g.flagged and not s.escalated:
            self.history.append({"role": "user", "content": patient_text})
            return self._emergency_handoff(g.category)

        if s.escalated:
            return self._reply(HANDOFF_REPLY, canned=True)

        self.history.append({"role": "user", "content": patient_text})
        failed_calls: set[str] = set()  # identical failing calls in this turn are not re-executed
        for step in range(MAX_STEPS_PER_TURN):
            system = render_system_prompt(self.playbook, s, self.ctx.store, self.ctx.clock)
            try:
                resp = self.llm.complete([{"role": "system", "content": system}, *self.history], self.tools,
                                         temperature=self.temperature)
            except QuotaExhausted:
                raise  # infrastructure: the harness pauses and resumes; never switch models
            except LLMError as e:  # e.g. the model emitted a malformed tool call the provider rejected
                self._trace("llm_error", step=step, error=str(e)[:300])
                self.history.append({"role": "assistant", "content": ERROR_REPLY})
                return self._reply(ERROR_REPLY)
            self._trace("llm", step=step, model=self.llm.label, prompt_tokens=resp.prompt_tokens,
                        completion_tokens=resp.completion_tokens, n_tool_calls=len(resp.tool_calls))
            if not resp.tool_calls:
                text = (resp.content or "").strip() or "Sorry, could you say that again?"
                self.history.append({"role": "assistant", "content": text})
                return self._reply(text)
            self.history.append(resp.to_message())
            for tc in resp.tool_calls:
                key = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True, default=str)}"
                if tc.arguments is None:
                    result = ToolResult(ok=False, code="INVALID_ARGUMENT", hint="Arguments were not valid JSON.")
                elif key in failed_calls:  # avoids looping on one bad call until the step limit
                    result = ToolResult(ok=False, code="REPEATED_CALL",
                                        hint="This exact call already failed this turn. Do not call tools again "
                                             "now: reply to the patient and ask for what you need.")
                else:
                    result = call_tool(self.ctx, tc.name, tc.arguments)
                    if not result.ok and result.code not in ("TIMEOUT",):  # a timeout may be retried once
                        failed_calls.add(key)
                self._trace("tool", name=tc.name, args=tc.arguments if tc.arguments is not None else tc.raw_arguments,
                            ok=result.ok, code=result.code, data=result.data)
                self.history.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": json.dumps(result.model_dump(exclude_none=True), default=str)})
        self._trace("step_limit")
        self.history.append({"role": "assistant", "content": STEP_LIMIT_REPLY})
        return self._reply(STEP_LIMIT_REPLY)

    def _emergency_handoff(self, category: str | None) -> str:
        """Escalate in code, in the same turn the guard flags. Traced as a normal tool event so the graders
        see exactly what the model would have done; every later tool call is refused with ESCALATED."""
        args = {"reason": f"Emergency guard flagged: {category or 'red-flag symptom'}", "urgency": "emergency"}
        result = call_tool(self.ctx, "escalate_to_human", args)
        self._trace("tool", name="escalate_to_human", args=args, ok=result.ok, code=result.code,
                    data=result.data, enforced=True)
        cat = (category or "").lower()
        self_harm = any(k in cat for k in ("self", "suicid", "harm"))
        text = SELF_HARM_REPLY if self_harm else EMERGENCY_REPLY
        self.history.append({"role": "assistant", "content": text})
        return self._reply(text, canned=True)

    def _reply(self, text: str, canned: bool = False) -> str:
        self._trace("agent", text=text, canned=canned)
        self._trace("state", state=self.state.model_dump(mode="json", exclude={"completed"}))
        return text
