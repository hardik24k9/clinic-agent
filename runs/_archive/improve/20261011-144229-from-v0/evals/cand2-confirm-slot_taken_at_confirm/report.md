# Eval run `cand2-confirm-slot_taken_at_confirm`

- Playbook: **v1**
- Models (pinned for the whole run):
  - agent: `ollama/qwen3.5:4b`
  - simulator: `ollama/gemma3:4b`
  - guard: `ollama/qwen3.5:4b`
  - judge: `groq/openai/gpt-oss-120b`

| Split | pass^k | Avg pass rate | Critical violations | Invalid trials |
|---|---|---|---|---|
| train | 1/1 | 100% | 0 | 0 |

## Scenarios

| Scenario | Split | Category | Passed | pass^k | Critical | Failing checks (count) |
|---|---|---|---|---|---|---|
| slot_taken_at_confirm | train | robustness | 3/3 | ✅ | 0 | — |

## Critical violations

None.

## Failure evidence (first failing trial per scenario)

## Cost

- Model calls: 1455, tokens: 2665650, time: 4592.9s
- Checks that could not be evaluated (e.g. judge errors): 0
  - agent: 816 calls (0 cache hits), 2206543 tokens
  - guard: 212 calls (225 cache hits), 33876 tokens
  - judge: 64 calls (0 cache hits), 144906 tokens
  - reflector: 1 calls (0 cache hits), 5876 tokens
  - simulator: 362 calls (0 cache hits), 274449 tokens
