"""Client-side request pacing per model (no quota numbers are hard-coded; default is configurable)."""
from __future__ import annotations

import os
import threading
import time


class RateLimiter:
    def __init__(self, rpm: float | None = None) -> None:
        rpm = rpm or float(os.environ.get("LLM_MAX_RPM", "30"))
        self.interval = 60.0 / rpm if rpm > 0 else 0.0
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: str) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next.get(key, now))
            self._next[key] = slot + self.interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)
