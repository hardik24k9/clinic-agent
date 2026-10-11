# Eval run `cand2-confirm-happy_cancel`

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
| happy_cancel | train | happy_path | 3/3 | ✅ | 0 | — |

## Critical violations

None.

## Failure evidence (first failing trial per scenario)

## Cost

- Model calls: 1374, tokens: 2512248, time: 4313.5s
- Checks that could not be evaluated (e.g. judge errors): 0
  - agent: 773 calls (0 cache hits), 2079557 tokens
  - guard: 198 calls (215 cache hits), 31643 tokens
  - judge: 61 calls (0 cache hits), 137667 tokens
  - reflector: 1 calls (0 cache hits), 5876 tokens
  - simulator: 341 calls (0 cache hits), 257505 tokens
