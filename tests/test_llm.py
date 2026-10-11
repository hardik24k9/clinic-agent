import pytest

from llm.budget import Budget
from llm.cache import ResponseCache, cache_key
from llm.client import _parse_tool_calls, _retry_after_seconds
from llm.config import ConfigError, redact, role_config


def test_redact_masks_configured_keys_and_key_shaped_strings(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "A" * 40)
    text = f"error calling with key {'gsk_' + 'A' * 40} and AIzaSyFAKEFAKEFAKE123"
    out = redact(text)
    assert "AAAA" not in out and "FAKEFAKE" not in out and "***" in out


def test_role_config_reads_env_and_validates(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "groq")
    monkeypatch.setenv("AGENT_MODEL", "some-model")
    cfg = role_config("agent")
    assert cfg.label == "groq/some-model" and cfg.key_env == "GROQ_API_KEY" and not cfg.key_present
    assert "key" not in str(cfg.public()).lower()
    with pytest.raises(ConfigError):
        cfg.api_key()
    monkeypatch.setenv("AGENT_PROVIDER", "nope")
    with pytest.raises(ConfigError):
        role_config("agent")


def test_ollama_is_optional_and_needs_no_key(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "ollama")
    monkeypatch.setenv("AGENT_MODEL", "qwen3:8b")
    cfg = role_config("agent")
    assert cfg.key_present and cfg.base_url.endswith("/v1")


def test_cache_round_trip(tmp_path):
    c = ResponseCache(tmp_path / "c.sqlite")
    k = cache_key({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert c.get(k) is None
    c.put(k, {"content": "hello"})
    assert c.get(k) == {"content": "hello"}
    assert k != cache_key({"model": "m2", "messages": [{"role": "user", "content": "hi"}]})


def test_budget_counts_calls_and_cache_hits():
    b = Budget()
    b.record("agent", 100, 20, cached=False)
    b.record("agent", 0, 0, cached=True)
    s = b.summary()
    assert s["by_role"]["agent"] == {"calls": 1, "cache_hits": 1, "prompt_tokens": 100, "completion_tokens": 20}


def test_retry_after_parsing_from_message():
    class E(Exception):
        response = None
    assert _retry_after_seconds(E("Please try again in 7m12.5s.")) == pytest.approx(432.5)
    assert _retry_after_seconds(E("try again in 1.2s")) == pytest.approx(1.2)


def test_invalid_tool_json_is_kept_as_none():
    class F:  # mimics the SDK object shape
        def __init__(self, name, arguments):
            self.name, self.arguments = name, arguments
    class C:
        def __init__(self, i, f):
            self.id, self.function = i, f
    calls = _parse_tool_calls([C("1", F("confirm", '{"token": "X"}')), C("2", F("confirm", "{bad json"))])
    assert calls[0].arguments == {"token": "X"} and calls[1].arguments is None


def test_reasoning_tags_are_stripped_and_json_is_extracted():
    from llm.client import extract_json, strip_reasoning
    assert strip_reasoning("<think>hmm, maybe</think>Hello") == "Hello"
    assert strip_reasoning("<think>never closed") == ""
    assert extract_json('<think>x</think>\n```json\n{"red_flag": true}\n```') == {"red_flag": True}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_doctor_role_probes_check_the_real_job():
    from llm.doctor import probe
    from llm.fake import FakeLLM, call
    assert probe(FakeLLM(lambda m, t: call("ping", ok=True)), "agent") == "ok"
    assert probe(FakeLLM(lambda m, t: "plain text"), "agent").startswith("WARN")
    assert probe(FakeLLM(lambda m, t: '<think>..</think>{"red_flag": true, "category": "cardiac"}'), "guard") == "ok"
    assert "did not flag" in probe(FakeLLM(lambda m, t: {"red_flag": False}), "guard")
    assert "not valid JSON" in probe(FakeLLM(lambda m, t: "sure!"), "judge")
    assert probe(FakeLLM(lambda m, t: {"ok": True}), "reflector") == "ok"
