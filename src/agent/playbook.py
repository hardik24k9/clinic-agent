"""The playbook: the ONLY layer the improvement loop may edit.

It holds behavioural rules, tool descriptions and short examples. Safety and authorization are
not here - they live in agent/tools.py and can never be weakened by a playbook patch.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

PLAYBOOK_DIR = Path(__file__).with_name("playbook")
MAX_RULES = 20
MAX_RULE_CHARS = 300
MAX_EXAMPLES = 5
MAX_EXAMPLE_CHARS = 700


class Rule(BaseModel):
    id: str
    text: str

    @field_validator("text")
    @classmethod
    def _len(cls, v: str) -> str:
        if not v.strip() or len(v) > MAX_RULE_CHARS:
            raise ValueError(f"rule text must be 1..{MAX_RULE_CHARS} chars")
        return v.strip()


class Example(BaseModel):
    id: str
    text: str

    @field_validator("text")
    @classmethod
    def _len(cls, v: str) -> str:
        if not v.strip() or len(v) > MAX_EXAMPLE_CHARS:
            raise ValueError(f"example text must be 1..{MAX_EXAMPLE_CHARS} chars")
        return v.strip()


class Playbook(BaseModel):
    version: int
    rules: list[Rule]
    tool_descriptions: dict[str, str]
    examples: list[Example] = Field(default_factory=list)
    changelog: list[str] = Field(default_factory=list)

    @field_validator("rules")
    @classmethod
    def _rules(cls, v: list[Rule]) -> list[Rule]:
        if len(v) > MAX_RULES:
            raise ValueError(f"at most {MAX_RULES} rules")
        if len({r.id for r in v}) != len(v):
            raise ValueError("duplicate rule ids")
        return v

    @field_validator("examples")
    @classmethod
    def _examples(cls, v: list[Example]) -> list[Example]:
        if len(v) > MAX_EXAMPLES:
            raise ValueError(f"at most {MAX_EXAMPLES} examples")
        return v

    def next_rule_id(self) -> str:
        nums = [int(m.group(1)) for r in self.rules if (m := re.fullmatch(r"R(\d+)", r.id))]
        return f"R{max(nums, default=0) + 1:02d}"


def path_for(version: int, directory: Path = PLAYBOOK_DIR) -> Path:
    return directory / f"v{version}.yaml"


def latest_version(directory: Path = PLAYBOOK_DIR) -> int:
    versions = [int(m.group(1)) for p in directory.glob("v*.yaml") if (m := re.fullmatch(r"v(\d+)\.yaml", p.name))]
    return max(versions, default=0)


def load_playbook(version: int | None = None, directory: Path = PLAYBOOK_DIR) -> Playbook:
    v = latest_version(directory) if version is None else version
    return Playbook.model_validate(yaml.safe_load(path_for(v, directory).read_text()))


def save_playbook(pb: Playbook, directory: Path = PLAYBOOK_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = path_for(pb.version, directory)
    path.write_text(yaml.safe_dump(pb.model_dump(), sort_keys=False, allow_unicode=True, width=100))
    return path
