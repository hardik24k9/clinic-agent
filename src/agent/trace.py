"""JSONL trace of every event in a conversation. Graders read this; secrets are scrubbed on write."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from llm.config import redact


class Tracer:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.events: list[dict[str, Any]] = []
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")

    def emit(self, type_: str, turn: int, **data: Any) -> dict[str, Any]:
        event = {"seq": len(self.events), "turn": turn, "type": type_, **data}
        line = redact(json.dumps(event, default=str))
        event = json.loads(line)
        self.events.append(event)
        if self.path:
            with self.path.open("a") as f:
                f.write(line + "\n")
        return event


def load_trace(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
