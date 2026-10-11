"""Gemini as a second provider: configuration, request shape, thought signatures, doctor. No network."""
import json
from types import SimpleNamespace

import pytest

from llm.client import LLMClient, LLMResponse, ToolCall, _parse_tool_calls
from llm.config import (GEMINI_LITE_MODEL, GEMINI_MAIN_MODEL, PROVIDERS, ConfigError, gemini_profile_config,
                        make_role_config, role_config)

SIG = {"google": {"thought_signature": "CvcQAdHtim-opaque-signature"}}


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key-not-real")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key-not-real")
    for v in ("GEMINI_REASONING_EFFORT", "AGENT_REASONING_EFFORT", "GUARD_REASONING_EFFORT",
              "GEMINI_THINKING_HEADROOM", "GEMINI_AGENT_MODEL", "GEMINI_GUARD_MODEL"):
        monkeypatch.delenv(v, raising=False)


# ---------------------------------------------------------------- configuration
def test_gemini_uses_googles_openai_compatible_endpoint(keys):
    cfg = make_role_config("agent", "gemini", GEMINI_MAIN_MODEL)
    assert cfg.base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert cfg.key_env == "GEMINI_API_KEY" and cfg.key_present
    assert cfg.reasoning_effort == "low" and cfg.thinking_headroom == 1024


def test_profile_models_are_the_verified_stable_ids(keys):
    assert GEMINI_MAIN_MODEL == "gemini-3.8-flash" and GEMINI_LITE_MODEL == "gemini-3.5-flash-lite"
    assert {r: gemini_profile_config(r).model for r in ("agent", "simulator", "judge", "reflector")} == \
        dict.fromkeys(("agent", "simulator", "judge", "reflector"), "gemini-3.8-flash")
    assert gemini_profile_config("guard").model == "gemini-3.5-flash-lite"   # lightweight classifier only


def test_gemini_selectable_per_role_through_env(keys, monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "gemini")
    monkeypatch.setenv("AGENT_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("AGENT_REASONING_EFFORT", "medium")
    cfg = role_config("agent")
    assert cfg.label == "gemini/gemini-3.8-flash" and cfg.reasoning_effort == "medium"
    assert cfg.public() == {"role": "agent", "provider": "gemini", "model": "gemini-3.8-flash",
                            "reasoning_effort": "medium"}  # thinking level is pinned in run manifests


def test_invalid_gemini_settings_are_rejected(keys, monkeypatch):
    monkeypatch.setenv("GEMINI_REASONING_EFFORT", "extreme")
    with pytest.raises(ConfigError):
        make_role_config("agent", "gemini", GEMINI_MAIN_MODEL)
    monkeypatch.setenv("GEMINI_REASONING_EFFORT", "low")
    monkeypatch.setenv("GEMINI_THINKING_HEADROOM", "lots")
    with pytest.raises(ConfigError):
        make_role_config("agent", "gemini", GEMINI_MAIN_MODEL)


def test_groq_configuration_is_unchanged(keys, monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "groq")
    monkeypatch.setenv("AGENT_MODEL", "openai/gpt-oss-20b")
    cfg = role_config("agent")
    assert cfg.base_url == "https://api.groq.com/openai/v1" and cfg.reasoning_effort is None
    assert cfg.thinking_headroom == 0
    assert cfg.public() == {"role": "agent", "provider": "groq", "model": "openai/gpt-oss-20b"}  # manifests resume
    assert set(PROVIDERS) == {"groq", "gemini", "openrouter", "ollama"}


# ---------------------------------------------------------------- request shape
def _capture(client):
    sent = {}

    def create(**payload):
        sent.update(payload)
        msg = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return sent


def test_groq_request_payload_is_byte_for_byte_unchanged(keys):
    client = LLMClient(make_role_config("agent", "groq", "openai/gpt-oss-20b"))
    sent = _capture(client)
    client.complete([{"role": "user", "content": "hi"}], max_tokens=400)
    assert sent == {"model": "openai/gpt-oss-20b", "messages": [{"role": "user", "content": "hi"}],
                    "temperature": 0.0, "max_tokens": 400}


def test_gemini_request_adds_thinking_level_and_headroom(keys):
    client = LLMClient(make_role_config("guard", "gemini", GEMINI_LITE_MODEL))
    sent = _capture(client)
    client.complete([{"role": "user", "content": "hi"}], json_mode=True, max_tokens=400)
    assert sent["reasoning_effort"] == "low" and sent["max_tokens"] == 400 + 1024
    assert sent["response_format"] == {"type": "json_object"} and sent["model"] == "gemini-3.5-flash-lite"


# ---------------------------------------------------------------- thought signatures
class _SdkCall:  # mimics the SDK object: Gemini puts extra_content in model_extra
    def __init__(self, extra=None):
        self.id, self.function = "call_1", SimpleNamespace(name="search_slots", arguments='{"date_from": "x"}')
        self.model_extra = {"extra_content": extra} if extra else {}


def test_thought_signature_is_kept_and_sent_back():
    [call] = _parse_tool_calls([_SdkCall(SIG)])
    assert call.extra_content == SIG
    msg = LLMResponse(content="", tool_calls=[call]).to_message()
    assert msg["tool_calls"][0]["extra_content"] == SIG          # echoed on the next request
    assert msg["tool_calls"][0]["function"]["name"] == "search_slots"


def test_tool_calls_without_signature_have_no_extra_field():
    [call] = _parse_tool_calls([_SdkCall()])
    msg = LLMResponse(content="", tool_calls=[call]).to_message()
    assert "extra_content" not in msg["tool_calls"][0]           # Groq messages unchanged


def test_cached_tool_calls_keep_the_signature(tmp_path, keys):
    from llm.cache import ResponseCache
    cache = ResponseCache(tmp_path / "c.sqlite")
    client = LLMClient(make_role_config("agent", "gemini", GEMINI_MAIN_MODEL), cache=cache)

    def create(**payload):
        tc = _SdkCall(SIG)
        msg = SimpleNamespace(content="", tool_calls=[tc])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    first = client.complete([{"role": "user", "content": "x"}], use_cache=True)
    again = client.complete([{"role": "user", "content": "x"}], use_cache=True)
    assert again.cached and again.tool_calls[0].extra_content == SIG == first.tool_calls[0].extra_content


# ---------------------------------------------------------------- doctor
def test_doctor_gemini_agent_probe_does_a_tool_round_trip():
    from llm.doctor import probe
    from llm.fake import FakeLLM, say
    seen = []

    def responder(messages, tools):
        seen.append(messages)
        if len(seen) == 1:
            return LLMResponse(content="", tool_calls=[ToolCall(id="c1", name="ping", arguments={"ok": True},
                                                                raw_arguments='{"ok": true}', extra_content=SIG)])
        return say("done")
    assert probe(FakeLLM(responder), "agent", roundtrip=True) == "ok"
    assistant = seen[1][1]
    assert assistant["tool_calls"][0]["extra_content"] == SIG and seen[1][2]["role"] == "tool"


def test_doctor_gemini_profile_offline_lists_profile_models(keys, capsys, monkeypatch):
    from llm import doctor
    monkeypatch.setattr("sys.argv", ["doctor", "--provider", "gemini", "--offline"])
    doctor.main()
    out = capsys.readouterr().out
    assert "gemini/gemini-3.8-flash" in out and "gemini/gemini-3.5-flash-lite" in out
    assert "[thinking: low]" in out and "test-gemini-key" not in out
    assert "same model family" in out  # all-Gemini profile: judge == agent family is flagged


def test_default_effort_is_accepted_for_gemini_and_omitted(keys, monkeypatch):
    monkeypatch.setenv("SIMULATOR_REASONING_EFFORT", "default")
    cfg = make_role_config("simulator", "gemini", GEMINI_MAIN_MODEL)
    assert cfg.reasoning_effort is None and cfg.thinking_headroom == 1024  # Gemini still thinks by default
    assert "reasoning_effort" not in cfg.public()


def test_tests_are_hermetic_against_a_developers_env(monkeypatch):
    """Regression: Docker injects .env; SIMULATOR_REASONING_EFFORT=default leaked into a Gemini test."""
    import importlib, os
    monkeypatch.setenv("SIMULATOR_REASONING_EFFORT", "default")
    monkeypatch.setenv("AGENT_PROVIDER", "ollama")
    import conftest
    importlib.reload(conftest)  # re-runs the module-level cleanup
    assert "SIMULATOR_REASONING_EFFORT" not in os.environ and "AGENT_PROVIDER" not in os.environ
