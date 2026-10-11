"""Interactive chat with the agent:  make chat   (or: python -m agent.chat)

Commands: /state  show session state   /quit  exit
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from clinic.clock import FixedClock
from clinic.faults import FaultPlan
from clinic.store import ClinicStore
from llm.budget import Budget
from llm.client import LLMError, QuotaExhausted, make_client
from llm.config import ConfigError, redact

from .guard import EmergencyGuard
from .playbook import load_playbook
from .runner import AgentRunner
from .state import SessionState
from .tools import ToolContext
from .trace import Tracer

RUNS = Path(__file__).resolve().parents[2] / "runs"
DIM, BOLD, RED, RESET = "\033[2m", "\033[1m", "\033[31m", "\033[0m"


def main() -> int:
    version = int(sys.argv[1]) if len(sys.argv) > 1 else None
    try:
        budget = Budget()
        agent_llm = make_client("agent", budget=budget)
        guard_llm = make_client("guard", budget=budget)
    except ConfigError as e:
        print(f"{RED}Config error:{RESET} {e}. Run `make doctor`.")
        return 2
    playbook = load_playbook(version)
    clock = FixedClock()
    ctx = ToolContext(store=ClinicStore.load_seed(clock=clock), state=SessionState(session_id="CHAT"),
                      clock=clock, faults=FaultPlan())
    trace_path = RUNS / "traces" / f"chat-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    tracer = Tracer(trace_path)
    runner = AgentRunner(ctx, agent_llm, playbook, guard=EmergencyGuard(guard_llm), tracer=tracer)
    print(f"{BOLD}Sunrise Family Clinic — scheduling assistant{RESET}  "
          f"{DIM}(agent {agent_llm.label}, playbook v{playbook.version}, clinic date {clock.now():%a %d %b %Y}){RESET}")
    print(f"{DIM}Type /state to see session state, /quit to exit. Trace: {trace_path.relative_to(RUNS.parent)}{RESET}\n")
    while True:
        try:
            text = input(f"{BOLD}you>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/state":
            print(DIM + ctx.state.model_dump_json(indent=1, exclude={"completed"}) + RESET)
            continue
        seen = len(tracer.events)
        try:
            reply = runner.handle(text)
        except QuotaExhausted as e:
            print(f"{RED}Quota reached:{RESET} {e}\nTry later, or point AGENT_MODEL at another model in .env.")
            continue
        except LLMError as e:
            print(f"{RED}Model error:{RESET} {redact(str(e))}")
            continue
        for ev in tracer.events[seen:]:
            if ev["type"] == "guard" and ev["flagged"]:
                print(f"{RED}  [guard] emergency flagged ({ev['category']}, via {ev['source']}) → writes locked{RESET}")
            elif ev["type"] == "tool":
                status = "ok" if ev["ok"] else ev["code"]
                print(f"{DIM}  [tool] {ev['name']}({_short(ev['args'])}) → {status}{RESET}")
        print(f"{BOLD}agent>{RESET} {reply}\n")
    print(DIM + f"Model usage: {budget.summary()}" + RESET)
    return 0


def _short(args) -> str:
    if not isinstance(args, dict):
        return "…"
    return ", ".join(f"{k}={v!r}" for k, v in args.items())[:90]


if __name__ == "__main__":
    raise SystemExit(main())
