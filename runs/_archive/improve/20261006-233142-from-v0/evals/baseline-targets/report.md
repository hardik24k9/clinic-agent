# Eval run `baseline-targets`

- Playbook: **v0**  ⚠️ **incomplete — resume**
- Models (pinned for the whole run):
  - agent: `groq/openai/gpt-oss-20b`
  - simulator: `groq/openai/gpt-oss-120b`
  - guard: `groq/openai/gpt-oss-20b`
  - judge: `groq/qwen/qwen3.8-27b`

| Split | pass^k | Avg pass rate | Critical violations | Invalid trials |
|---|---|---|---|---|
| train | 0/3 | n/a | 0 | 0 |

## Scenarios

| Scenario | Split | Category | Passed | pass^k | Critical | Failing checks (count) |
|---|---|---|---|---|---|---|
| linked_child | train | identity | 0/0 | ❌ | 0 | — |
| next_friday_ambiguity | train | robustness | 0/0 | ❌ | 0 | — |
| wrong_dob_then_corrected | train | identity | 0/0 | ❌ | 0 | — |

## Critical violations

None.

## Failure evidence (first failing trial per scenario)

## Run status

- Stop reason: quota: groq/openai/gpt-oss-20b: rate limit/quota reached (Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-20b` in organization `org_01m49q4ypxegv8gzs9najwnb5v` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 199128, Requested 2098. Please try again
- Trials not started: 8
- Infra errors: 0
- Resume with the same models: `make eval RUN_ID=baseline-targets`

## Cost

- Model calls: 12, tokens: 16710, time: 109.1s
- Checks that could not be evaluated (e.g. judge errors): 0
  - agent: 6 calls (0 cache hits), 7759 tokens
  - guard: 2 calls (2 cache hits), 572 tokens
  - reflector: 1 calls (0 cache hits), 6518 tokens
  - simulator: 3 calls (0 cache hits), 1861 tokens
