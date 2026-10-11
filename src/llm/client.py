"""Provider-agnostic LLM client over OpenAI-compatible endpoints (Groq, Gemini, OpenRouter, Ollama).

Design rules:
- One fixed model per client. There is NO automatic fallback to another model: during an eval a
  silent switch would make before/after comparisons meaningless. Quota exhaustion raises
  QuotaExhausted so the harness can checkpoint and resume later with the same model.
- Retries with exponential backoff for transient errors (429 with a short wait, 5xx, timeouts).
- Every error message passes through redact() so a key can never reach logs or traces.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .budget import Budget
from .cache import ResponseCache, cache_key
from .config import RoleConfig, redact
from .ratelimit import RateLimiter

MAX_RETRIES = 4
LONG_WAIT_S = 90  # a 429 asking us to wait longer than this is treated as quota exhaustion


class LLMError(Exception):
    pass


class QuotaExhausted(LLMError):
    """The provider's quota is used up for now. Do not switch models: pause and resume later."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None   # None when the model produced invalid JSON
    raw_arguments: str = ""
    # Opaque provider data that must be echoed back on the next request. Gemini 3 puts its thought signature
    # here ({"google": {"thought_signature": ...}}); multi-turn tool calls fail with HTTP 400 without it.
    extra_content: dict[str, Any] | None = None


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    cached: bool = False

    def to_message(self) -> dict[str, Any]:
        """OpenAI-format assistant message, for appending to the conversation history."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or (None if self.tool_calls else "")}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.name, "arguments": c.raw_arguments or "{}"},
                                  **({"extra_content": c.extra_content} if c.extra_content else {})}
                                 for c in self.tool_calls]
        return msg


class ChatModel(Protocol):
    """Anything that can complete a chat: the real client or the deterministic fake used in tests."""
    label: str

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *,
                 temperature: float = 0.0, json_mode: bool = False, use_cache: bool = False,
                 max_tokens: int | None = None) -> LLMResponse: ...


_THINK = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Some models (e.g. Qwen3) put their reasoning inline as <think>...</think>. Keep only the answer."""
    return _THINK.sub("", text or "").strip()


def extract_json(text: str) -> Any:
    """Parse a JSON object from a model reply, tolerating reasoning tags and ```json fences."""
    text = strip_reasoning(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start:end + 1])


def _parse_tool_calls(raw_calls: Any) -> list[ToolCall]:
    out = []
    for c in raw_calls or []:
        raw = c.function.arguments or "{}"
        try:
            args = json.loads(raw)
            if not isinstance(args, dict):
                args = None
        except json.JSONDecodeError:
            args = None
        extra = getattr(c, "extra_content", None)
        if extra is None:
            extra = (getattr(c, "model_extra", None) or {}).get("extra_content")
        out.append(ToolCall(id=c.id, name=c.function.name, arguments=args, raw_arguments=raw,
                            extra_content=extra if isinstance(extra, dict) else None))
    return out


def _retry_after_seconds(err: Exception) -> float | None:
    resp = getattr(err, "response", None)
    headers = getattr(resp, "headers", None) or {}
    value = headers.get("retry-after") if hasattr(headers, "get") else None
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)s", str(err))
    if m:
        h, mnt, s = m.groups()
        return int(h or 0) * 3600 + int(mnt or 0) * 60 + float(s)
    return None


class LLMClient:
    def __init__(self, cfg: RoleConfig, *, budget: Budget | None = None, cache: ResponseCache | None = None,
                 limiter: RateLimiter | None = None, timeout_s: float | None = None) -> None:
        import openai  # imported lazily so tests never need network or keys

        self._openai = openai
        self.cfg = cfg
        self.label = cfg.label
        self.budget = budget or Budget()
        self.cache = cache
        self.limiter = limiter or RateLimiter()
        self._client = openai.OpenAI(api_key=cfg.api_key(), base_url=cfg.base_url,
                                     timeout=timeout_s or self._default_timeout(cfg), max_retries=0)

    @staticmethod
    def _default_timeout(cfg: RoleConfig) -> float:
        # A local model may need to load into memory on first use; remote APIs answer much faster.
        import os
        return float(os.environ.get("LLM_TIMEOUT_S", "180" if cfg.provider == "ollama" else "60"))

    def complete(self, messages, tools=None, *, temperature=0.0, json_mode=False, use_cache=False,
                 max_tokens=None) -> LLMResponse:
        payload: dict[str, Any] = {"model": self.cfg.model, "messages": messages, "temperature": temperature}
        if tools:
            payload["tools"] = tools
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if max_tokens:
            payload["max_tokens"] = max_tokens + self.cfg.thinking_headroom  # headroom is 0 except for Gemini
        if self.cfg.reasoning_effort:                                       # only set for Gemini
            payload["reasoning_effort"] = self.cfg.reasoning_effort
        key = cache_key({"provider": self.cfg.provider, **payload}) if (use_cache and self.cache) else None
        if key and (hit := self.cache.get(key)):
            self.budget.record(self.cfg.role, 0, 0, cached=True)
            return LLMResponse(content=hit["content"], cached=True, model=self.cfg.model,
                               tool_calls=[ToolCall(**c) for c in hit.get("tool_calls", [])])
        resp = self._call_with_retries(payload)
        choice = resp.choices[0].message
        usage = getattr(resp, "usage", None)
        out = LLMResponse(content=strip_reasoning(choice.content or ""), tool_calls=_parse_tool_calls(choice.tool_calls),
                          prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                          completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                          model=self.cfg.model)
        self.budget.record(self.cfg.role, out.prompt_tokens, out.completion_tokens, cached=False)
        if key:
            self.cache.put(key, {"content": out.content, "tool_calls": [c.__dict__ for c in out.tool_calls]})
        return out

    def _call_with_retries(self, payload: dict[str, Any]) -> Any:
        oa = self._openai
        delay = 2.0
        for attempt in range(MAX_RETRIES + 1):
            if self.cfg.provider != "ollama":  # local model: no provider rate limit to respect
                self.limiter.wait(self.label)
            try:
                return self._client.chat.completions.create(**payload)
            except oa.RateLimitError as e:
                wait = _retry_after_seconds(e)
                if wait is not None and wait > LONG_WAIT_S or attempt == MAX_RETRIES:
                    when = f" - provider says retry in ~{wait / 60:.0f} min" if wait else ""
                    raise QuotaExhausted(redact(f"{self.label}: rate limit/quota reached{when} ({e})")) from None
                time.sleep(wait if wait is not None else delay)
            except (oa.APITimeoutError, oa.APIConnectionError, oa.InternalServerError) as e:
                if attempt == MAX_RETRIES or (self.cfg.provider == "ollama" and isinstance(e, oa.APIConnectionError)):
                    hint = (f" - is Ollama running on the host and reachable at {self.cfg.base_url}?"
                            if self.cfg.provider == "ollama" and isinstance(e, oa.APIConnectionError) else "")
                    raise LLMError(redact(f"{self.label}: {type(e).__name__}: {e}{hint}")) from None
                time.sleep(delay)
            except oa.APIStatusError as e:  # 4xx other than 429: not retryable
                raise LLMError(redact(f"{self.label}: HTTP {e.status_code}: {e}")) from None
            delay = min(delay * 2, 30)
        raise LLMError(f"{self.label}: exhausted retries")


def make_client(role: str, *, budget: Budget | None = None, cache: ResponseCache | None = None,
                limiter: RateLimiter | None = None) -> LLMClient:
    from .config import role_config
    return LLMClient(role_config(role), budget=budget, cache=cache, limiter=limiter)
