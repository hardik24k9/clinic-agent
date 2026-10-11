"""Deterministic stand-in for a real model. Used by tests (and offline demos): no network, no keys.

A FakeLLM is driven by a `responder(messages, tools) -> LLMResponse | str | dict`. Helpers build
common responses: say("text") and call("tool_name", **args).
"""
from __future__ import annotations

import json
from itertools import count
from typing import Any, Callable

from .client import LLMResponse, ToolCall

_ids = count(1)


def say(text: str) -> LLMResponse:
    return LLMResponse(content=text, model="fake")


def call(name: str, **args: Any) -> LLMResponse:
    return LLMResponse(content="", model="fake", tool_calls=[
        ToolCall(id=f"call_{next(_ids)}", name=name, arguments=args, raw_arguments=json.dumps(args))])


class FakeLLM:
    def __init__(self, responder: Callable[[list[dict[str, Any]], Any], Any], label: str = "fake/fake") -> None:
        self.responder = responder
        self.label = label
        self.calls: list[list[dict[str, Any]]] = []

    def complete(self, messages, tools=None, *, temperature=0.0, json_mode=False, use_cache=False,
                 max_tokens=None) -> LLMResponse:
        self.calls.append(messages)
        out = self.responder(messages, tools)
        if isinstance(out, LLMResponse):
            return out
        if isinstance(out, dict):
            return say(json.dumps(out))
        return say(str(out))


def scripted(responses: list[Any], label: str = "fake/scripted") -> FakeLLM:
    """Returns the given responses in order (then repeats the last one)."""
    it = iter(responses)
    last: list[Any] = [say("")]

    def responder(_m, _t):
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]
    return FakeLLM(responder, label)
