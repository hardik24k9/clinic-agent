"""Call/token accounting per role, so every report shows what a run cost."""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any


class Budget:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started = time.time()
        self.calls: dict[str, int] = defaultdict(int)
        self.cached: dict[str, int] = defaultdict(int)
        self.prompt_tokens: dict[str, int] = defaultdict(int)
        self.completion_tokens: dict[str, int] = defaultdict(int)

    def record(self, role: str, prompt: int, completion: int, cached: bool) -> None:
        with self._lock:
            if cached:
                self.cached[role] += 1
                return
            self.calls[role] += 1
            self.prompt_tokens[role] += prompt
            self.completion_tokens[role] += completion

    def summary(self) -> dict[str, Any]:
        roles = sorted(set(self.calls) | set(self.cached))
        return {
            "elapsed_s": round(time.time() - self.started, 1),
            "by_role": {r: {"calls": self.calls[r], "cache_hits": self.cached[r],
                            "prompt_tokens": self.prompt_tokens[r],
                            "completion_tokens": self.completion_tokens[r]} for r in roles},
            "total_calls": sum(self.calls.values()),
            "total_tokens": sum(self.prompt_tokens.values()) + sum(self.completion_tokens.values()),
        }
