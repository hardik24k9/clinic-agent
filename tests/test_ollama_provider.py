"""Ollama as a local provider: thinking off by default, pinned in manifests, no rate limiting. No network."""
from types import SimpleNamespace

import pytest

from llm.client import LLMClient, LLMError
from llm.config import ConfigError, make_role_config, role_config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for v in ("OLLAMA_REASONING_EFFORT", "AGENT_REASONING_EFFORT", "GUARD_REASONING_EFFORT",
              "OLLAMA_THINKING_HEADROOM", "OLLAMA_API_BASE", "LLM_TIMEOUT_S"):
        monkeypatch.delenv(v, raising=False)


def _capture(client):
    sent = {}

    def create(**payload):
        sent.update(payload)
        msg = SimpleNamespace(content="<think>hmm</think>ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return sent


def test_ollama_defaults_thinking_off_and_points_at_the_host():
    cfg = make_role_config("guard", "ollama", "qwen3.5:4b")
    assert cfg.base_url == "http://host.docker.internal:11434/v1" and cfg.key_env is None and cfg.key_present
    assert cfg.reasoning_effort == "none" and cfg.thinking_headroom == 0
    assert cfg.public() == {"role": "guard", "provider": "ollama", "model": "qwen3.5:4b", "reasoning_effort": "none"}


def test_ollama_request_disables_thinking_and_keeps_max_tokens():
    client = LLMClient(make_role_config("guard", "ollama", "qwen3.5:4b"))
    sent = _capture(client)
    out = client.complete([{"role": "user", "content": "hi"}], json_mode=True, max_tokens=400)
    assert sent["reasoning_effort"] == "none" and sent["max_tokens"] == 400
    assert out.content == "ok"  # inline reasoning, if a model still emits it, is stripped


def test_ollama_thinking_can_be_turned_on_per_role_with_headroom(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "ollama")
    monkeypatch.setenv("AGENT_MODEL", "qwen3.5:4b")
    monkeypatch.setenv("AGENT_REASONING_EFFORT", "low")
    cfg = role_config("agent")
    assert cfg.reasoning_effort == "low" and cfg.thinking_headroom == 1024
    sent = _capture(c := LLMClient(cfg))
    c.complete([{"role": "user", "content": "hi"}], max_tokens=400)
    assert sent["max_tokens"] == 1424 and sent["reasoning_effort"] == "low"


def test_invalid_ollama_effort_is_rejected(monkeypatch):
    monkeypatch.setenv("OLLAMA_REASONING_EFFORT", "turbo")
    with pytest.raises(ConfigError):
        make_role_config("agent", "ollama", "qwen3.5:4b")


def test_ollama_is_not_rate_limited_but_remote_providers_are(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-not-real")
    waits = []
    limiter = SimpleNamespace(wait=lambda key: waits.append(key))
    local = LLMClient(make_role_config("agent", "ollama", "qwen3.5:4b"), limiter=limiter)
    _capture(local)
    local.complete([{"role": "user", "content": "x"}])
    remote = LLMClient(make_role_config("judge", "groq", "openai/gpt-oss-120b"), limiter=limiter)
    _capture(remote)
    remote.complete([{"role": "user", "content": "x"}])
    assert waits == ["groq/openai/gpt-oss-120b"]


def test_ollama_gets_a_longer_default_timeout_than_remote(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-not-real")
    assert LLMClient._default_timeout(make_role_config("agent", "ollama", "m")) == 180
    assert LLMClient._default_timeout(make_role_config("judge", "groq", "m")) == 60
    monkeypatch.setenv("LLM_TIMEOUT_S", "300")
    assert LLMClient._default_timeout(make_role_config("agent", "ollama", "m")) == 300


def test_unreachable_ollama_fails_fast_with_a_clear_hint():
    import openai
    client = LLMClient(make_role_config("agent", "ollama", "qwen3.5:4b"))
    calls = []

    def create(**payload):
        calls.append(1)
        raise openai.APIConnectionError(request=SimpleNamespace(method="POST", url="x"))
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(LLMError, match="is Ollama running"):
        client.complete([{"role": "user", "content": "x"}])
    assert len(calls) == 1  # no pointless retries against a server that isn't there


def test_groq_payload_still_unchanged(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-not-real")
    client = LLMClient(make_role_config("agent", "groq", "openai/gpt-oss-20b"))
    sent = _capture(client)
    client.complete([{"role": "user", "content": "hi"}], max_tokens=400)
    assert sent == {"model": "openai/gpt-oss-20b", "messages": [{"role": "user", "content": "hi"}],
                    "temperature": 0.0, "max_tokens": 400}


def test_default_effort_omits_the_parameter_for_non_thinking_models(monkeypatch):
    monkeypatch.setenv("SIMULATOR_REASONING_EFFORT", "default")
    cfg = make_role_config("simulator", "ollama", "gemma3:4b")
    assert cfg.reasoning_effort is None and cfg.thinking_headroom == 0
    assert cfg.public() == {"role": "simulator", "provider": "ollama", "model": "gemma3:4b"}
    sent = _capture(c := LLMClient(cfg))
    c.complete([{"role": "user", "content": "hi"}], max_tokens=400)
    assert "reasoning_effort" not in sent and sent["max_tokens"] == 400
