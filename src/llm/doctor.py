"""make doctor: show the model for each role, check keys (set/missing only) and test each role's real job.

  make doctor                         check the configured roles (.env)
  make doctor ARGS="--provider gemini"  check the optional Gemini profile instead (uses only GEMINI_API_KEY)

Role checks: agent -> returns a tool call; guard -> flags an obvious emergency as JSON;
judge/reflector -> returns parseable JSON; simulator -> returns text.
On HTTP 404 it lists the model IDs this key can use (IDs only, never the key). `--offline` skips network.
"""
from __future__ import annotations

import sys
import time

from .client import LLMClient, LLMError, QuotaExhausted, extract_json
from .config import ROLES, ConfigError, gemini_profile_config, redact, role_config

PING_TOOL = [{"type": "function", "function": {
    "name": "ping", "description": "Reply to a connectivity check.",
    "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}}]

CALLS_PER_CONVERSATION = {"agent": 12, "simulator": 6, "guard": 6, "judge": 1, "reflector": 0}


def probe(client: LLMClient, role: str, roundtrip: bool = False) -> str:
    """Raises LLMError/QuotaExhausted on provider errors; returns a status string otherwise."""
    if role == "agent":
        first = [{"role": "user", "content": "Call the ping tool with ok=true."}]
        r = client.complete(first, PING_TOOL, max_tokens=400)
        if not r.tool_calls:
            return "WARN: reachable but returned no tool call (agent needs tool calling)"
        if roundtrip:  # the agent loop is multi-turn: the tool result must go back with the assistant message
            msgs = [*first, r.to_message(), {"role": "tool", "tool_call_id": r.tool_calls[0].id,
                                              "content": '{"ok": true}'}]
            final = client.complete(msgs, PING_TOOL, max_tokens=400)
            if not (final.content or final.tool_calls):
                return "WARN: tool round-trip returned nothing"
        return "ok"
    if role == "guard":
        from agent.guard import GUARD_PROMPT
        r = client.complete([{"role": "system", "content": GUARD_PROMPT},
                             {"role": "user", "content": "I have crushing chest pain and my left arm is numb"}],
                            json_mode=True, max_tokens=400)
        try:
            flagged = extract_json(r.content).get("red_flag") is True
        except (ValueError, AttributeError):
            return "WARN: guard reply was not valid JSON (guard would run rules-only)"
        return "ok" if flagged else "WARN: guard did not flag an obvious emergency"
    if role in ("judge", "reflector"):
        r = client.complete([{"role": "user", "content": 'Reply with JSON only: {"ok": true}'}],
                            json_mode=True, max_tokens=600)
        try:
            return "ok" if extract_json(r.content).get("ok") is True else "WARN: unexpected JSON content"
        except (ValueError, AttributeError):
            return "WARN: reply was not valid JSON"
    r = client.complete([{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=400)
    return "ok" if r.content.strip() else "WARN: empty reply"


def available_models(client: LLMClient) -> list[str]:
    try:
        return sorted(m.id.removeprefix("models/") for m in client._client.models.list().data)
    except Exception:  # noqa: BLE001 - best effort, diagnostic only
        return []


def main() -> int:
    offline = "--offline" in sys.argv
    gemini_profile = "--provider" in sys.argv and sys.argv[sys.argv.index("--provider") + 1:][:1] == ["gemini"]
    ok = True
    listed: dict[str, list[str]] = {}
    if gemini_profile:
        print("Checking the optional Gemini profile (GEMINI_API_KEY only; your .env roles are not used or changed).\n")
    print("Role        Provider/model                              Key       Check")
    print("-" * 90)
    for role in ROLES:
        try:
            cfg = gemini_profile_config(role) if gemini_profile else role_config(role)
        except ConfigError as e:
            print(f"{role:<11} {'-':<43} {'-':<9} CONFIG ERROR: {e}")
            ok = False
            continue
        key = "n/a" if cfg.key_env is None else ("set" if cfg.key_present else "MISSING")
        status = "skipped (offline)"
        if not cfg.key_present:
            status, ok = f"set {cfg.key_env} in .env", False
        elif not offline:
            client = LLMClient(cfg)
            t0 = time.time()
            try:
                if cfg.provider == "gemini":
                    ids = listed.setdefault("gemini", available_models(client))
                    if ids and cfg.model not in ids:
                        raise LLMError(f"{cfg.label}: HTTP 404: model id not in this key's model list")
                status = probe(client, role, roundtrip=cfg.provider == "gemini")
                status += f" ({time.time() - t0:.1f}s)" if status == "ok" else ""
            except QuotaExhausted as e:
                status = f"QUOTA: {redact(str(e))[:110]}"
            except LLMError as e:
                msg = redact(str(e))
                status = f"ERROR: {msg[:110]}"
                if "HTTP 404" in msg and cfg.provider not in listed:
                    listed[cfg.provider] = available_models(client)
            ok = ok and status.startswith("ok")
        extra = f" [thinking: {cfg.reasoning_effort}]" if cfg.reasoning_effort else ""
        print(f"{role:<11} {cfg.label:<43} {key:<9} {status}{extra}")
    for provider, ids in (listed.items() if not ok else []):
        print(f"\nModel IDs available to your {provider} key: {', '.join(ids) or '(could not list)'}")
        print(f"Set the failing role's *_MODEL in .env to one of these (keep the judge a different family from the agent).")
        if provider == "ollama":
            print("For a local model that isn't listed, pull it on the host first:  ollama pull <model>")
    try:
        cfg_of = gemini_profile_config if gemini_profile else role_config
        same = lambda a, b: a.split("/")[0].split("-")[0] == b.split("/")[0].split("-")[0]  # noqa: E731
        if same(cfg_of("judge").model, cfg_of("agent").model):
            print("\nNote: judge and agent are the same model family; prefer a different family where available "
                  "(e.g. a Gemini agent with the Groq Qwen judge).")
    except ConfigError:
        pass
    n = 14 * 2
    est = {r: c * n for r, c in CALLS_PER_CONVERSATION.items()}
    print(f"\nEstimated calls for one `make eval` (14 scenarios x k=2): {est} (total ~{sum(est.values())}).")
    print("`make improve` runs roughly 3-4x that. Runs checkpoint, so a quota stop can be resumed.")
    print("\nConfiguration OK." if ok else "\nConfiguration has problems (see above).")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
