import os

# Never read a developer's real .env (or API keys) during tests.
os.environ["CLINIC_NO_DOTENV"] = "1"
for _k in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
    os.environ.pop(_k, None)
# Hermetic tests: inside Docker, compose injects the developer's .env into the environment. Drop every model/
# provider setting so tests see the same (empty) configuration everywhere; tests set what they need explicitly.
_ROLE_PREFIXES = ("AGENT_", "SIMULATOR_", "GUARD_", "JUDGE_", "REFLECTOR_")
for _k in list(os.environ):
    if (_k.startswith(_ROLE_PREFIXES) or _k.startswith(("GEMINI_", "OLLAMA_", "LLM_"))
            or _k.endswith(("_REASONING_EFFORT", "_THINKING_HEADROOM"))):
        os.environ.pop(_k, None)

import pytest

from clinic.clock import FixedClock
from clinic.store import ClinicStore


@pytest.fixture
def clock():
    return FixedClock()


@pytest.fixture
def store(clock):
    return ClinicStore.load_seed(clock=clock)


from agent.tools import ToolContext, call_tool  # noqa: E402
from clinic.faults import FaultPlan  # noqa: E402


@pytest.fixture
def make_ctx(store, clock):
    def _make(faults=None):
        return ToolContext(store=store, clock=clock, faults=FaultPlan(faults or {}))
    return _make


@pytest.fixture
def ctx(make_ctx):
    return make_ctx()


def run(ctx, name, **args):
    return call_tool(ctx, name, args)


def verified(ctx, name="Anita Rao", dob="1984-03-02"):
    r = run(ctx, "verify_patient", full_name=name, dob=dob)
    assert r.ok, r
    return ctx


def offer(ctx, specialty="general_medicine", day="2026-10-13"):
    r = run(ctx, "search_slots", date_from=day, date_to=day, specialty=specialty)
    assert r.ok and r.data["slots"], r
    return r.data["slots"][0]["slot_id"]


def patient_says(ctx):
    ctx.state.begin_patient_turn()
