"""Per-role model configuration, read from the environment (.env).

Every provider is reached through its OpenAI-compatible endpoint, so one small client covers
Groq, Gemini, OpenRouter and (optionally) Ollama. API keys are read here and nowhere else;
`redact()` scrubs them from any text before it is printed, logged or traced.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

ROLES = ("agent", "simulator", "guard", "judge", "reflector")

PROVIDERS: dict[str, dict[str, str | None]] = {
    "groq":       {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY"},
    "gemini":     {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "key_env": "GEMINI_API_KEY"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY"},
    "ollama":     {"base_url": None, "key_env": None},  # base from OLLAMA_API_BASE; no key
}

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_dotenv(path: Path = ENV_FILE) -> None:
    """Minimal .env loader (no extra dependency). Never overrides variables already set.

    In Docker, compose already injects .env; this covers running outside Docker.
    Tests set CLINIC_NO_DOTENV=1 so a developer's real .env (and keys) are never touched.
    """
    if os.environ.get("CLINIC_NO_DOTENV") == "1" or not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) and key not in os.environ:
            os.environ[key] = value


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class RoleConfig:
    role: str
    provider: str
    model: str
    base_url: str
    key_env: str | None
    # Provider-specific knobs. Only Gemini sets them (Groq/OpenRouter/Ollama requests are unchanged):
    reasoning_effort: str | None = None   # Gemini 3.x thinking level, sent as `reasoning_effort`
    thinking_headroom: int = 0            # Gemini counts thinking tokens against max_tokens; add this much

    @property
    def key_present(self) -> bool:
        return self.key_env is None or bool(os.environ.get(self.key_env))

    def api_key(self) -> str:
        if self.key_env is None:
            return "ollama"  # Ollama ignores the key, the SDK just needs a non-empty string
        value = os.environ.get(self.key_env, "")
        if not value:
            raise ConfigError(f"{self.key_env} is not set (needed by role '{self.role}')")
        return value

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}"

    def public(self) -> dict[str, str]:
        """Safe to print or store in a run manifest: no secrets. Includes the thinking level when one is set,
        because it changes behaviour and must be pinned like the model (unchanged for Groq)."""
        out = {"role": self.role, "provider": self.provider, "model": self.model}
        if self.reasoning_effort:
            out["reasoning_effort"] = self.reasoning_effort
        return out


def role_config(role: str) -> RoleConfig:
    if role not in ROLES:
        raise ConfigError(f"unknown role {role!r}")
    load_dotenv()
    provider = os.environ.get(f"{role.upper()}_PROVIDER", "").strip().lower()
    model = os.environ.get(f"{role.upper()}_MODEL", "").strip()
    if provider not in PROVIDERS:
        raise ConfigError(f"{role.upper()}_PROVIDER must be one of {sorted(PROVIDERS)}, got {provider!r}")
    if not model or model.startswith("<"):
        raise ConfigError(f"{role.upper()}_MODEL is not set")
    return make_role_config(role, provider, model)


# Verified against https://ai.google.dev/gemini-api/docs/models (Oct 2026): both listed as STABLE.
GEMINI_MAIN_MODEL = "gemini-3.8-flash"        # agent, simulator, judge, reflector
GEMINI_LITE_MODEL = "gemini-3.5-flash-lite"   # lightweight classifier (emergency guard)
GEMINI_PROFILE = {"agent": GEMINI_MAIN_MODEL, "simulator": GEMINI_MAIN_MODEL, "guard": GEMINI_LITE_MODEL,
                  "judge": GEMINI_MAIN_MODEL, "reflector": GEMINI_MAIN_MODEL}
GEMINI_EFFORTS = {"low", "medium", "high", "default"}  # Gemini 3.x levels; "default": don't send (model default)
# Ollama's OpenAI-compatible endpoint accepts reasoning_effort; "none" turns thinking off for thinking models
# (docs.ollama.com/api/openai-compatibility). Off by default: faster, and short budgets aren't spent on thinking.
OLLAMA_EFFORTS = {"none", "low", "medium", "high", "default"}  # "default": don't send it (non-thinking models)


def make_role_config(role: str, provider: str, model: str) -> RoleConfig:
    if provider not in PROVIDERS:
        raise ConfigError(f"provider must be one of {sorted(PROVIDERS)}, got {provider!r}")
    spec = PROVIDERS[provider]
    base = spec["base_url"]
    if provider == "ollama":
        base = os.environ.get("OLLAMA_API_BASE", "http://host.docker.internal:11434").rstrip("/") + "/v1"
    effort, headroom = None, 0
    if provider == "gemini":
        effort = (os.environ.get(f"{role.upper()}_REASONING_EFFORT") or
                  os.environ.get("GEMINI_REASONING_EFFORT") or "low").strip().lower()
        if effort not in GEMINI_EFFORTS:
            raise ConfigError(f"Gemini reasoning effort must be one of {sorted(GEMINI_EFFORTS)}, got {effort!r}")
        if effort == "default":
            effort = None  # omitted: Gemini 3.x still thinks by default, so the headroom below still applies
        try:
            headroom = int(os.environ.get("GEMINI_THINKING_HEADROOM", "1024"))
        except ValueError:
            raise ConfigError("GEMINI_THINKING_HEADROOM must be an integer") from None
    if provider == "ollama":
        effort = (os.environ.get(f"{role.upper()}_REASONING_EFFORT") or
                  os.environ.get("OLLAMA_REASONING_EFFORT") or "none").strip().lower()
        if effort not in OLLAMA_EFFORTS:
            raise ConfigError(f"Ollama reasoning effort must be one of {sorted(OLLAMA_EFFORTS)}, got {effort!r}")
        if effort == "default":
            effort = None  # parameter omitted: the model's own behaviour (for models without a thinking mode)
        elif effort != "none":  # thinking on: like Gemini, thinking tokens count against max_tokens
            try:
                headroom = int(os.environ.get("OLLAMA_THINKING_HEADROOM", "1024"))
            except ValueError:
                raise ConfigError("OLLAMA_THINKING_HEADROOM must be an integer") from None
    return RoleConfig(role=role, provider=provider, model=model, base_url=base, key_env=spec["key_env"],
                      reasoning_effort=effort, thinking_headroom=headroom)


def gemini_profile_config(role: str) -> RoleConfig:
    """The Gemini model for a role in the optional Gemini profile (override with GEMINI_<ROLE>_MODEL)."""
    load_dotenv()
    model = os.environ.get(f"GEMINI_{role.upper()}_MODEL", GEMINI_PROFILE[role]).strip()
    return make_role_config(role, "gemini", model)


def _secret_values() -> list[str]:
    out = []
    for spec in PROVIDERS.values():
        env = spec["key_env"]
        if env and len(os.environ.get(env, "")) >= 8:
            out.append(os.environ[env])
    return out


def redact(text: str) -> str:
    """Remove any configured API key (and anything that looks like one) from text."""
    for secret in _secret_values():
        text = text.replace(secret, "***")
    return re.sub(r"\b(gsk_|sk-|AIza)[A-Za-z0-9_\-]{8,}", r"\1***", text)
