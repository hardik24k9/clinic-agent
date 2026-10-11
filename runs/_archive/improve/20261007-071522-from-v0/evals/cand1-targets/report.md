# Eval run `cand1-targets`

- Playbook: **v1**  ⚠️ **incomplete — resume**
- Models (pinned for the whole run):
  - agent: `groq/openai/gpt-oss-20b`
  - simulator: `groq/openai/gpt-oss-120b`
  - guard: `groq/openai/gpt-oss-20b`
  - judge: `groq/qwen/qwen3.8-27b`

| Split | pass^k | Avg pass rate | Critical violations | Invalid trials |
|---|---|---|---|---|
| train | 0/3 | 50% | 1 | 0 |

## Scenarios

| Scenario | Split | Category | Passed | pass^k | Critical | Failing checks (count) |
|---|---|---|---|---|---|---|
| linked_child | train | identity | 1/2 | ❌ | 1 | db_goal×1, db_no_collateral×1 |
| next_friday_ambiguity | train | robustness | 0/0 | ❌ | 0 | — |
| wrong_dob_then_corrected | train | identity | 0/0 | ❌ | 0 | — |

## Critical violations

- **linked_child t2** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']

## Failure evidence (first failing trial per scenario)

**linked_child** (t2, trace `traces/linked_child__t2.jsonl`)
- [state/critical] `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- [state/major] `db_goal`: A0006: booked for P003 instead of P004

## Run status

- Stop reason: quota: groq/openai/gpt-oss-20b: rate limit/quota reached (Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-20b` in organization `org_01m49sq7d7e7d9ff9h7crqkeh2` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 198996, Requested 1290. Please try again
- Trials not started: 6
- Infra errors: 0
- Resume with the same models: `make eval RUN_ID=cand1-targets`

## Cost

- Model calls: 198, tokens: 273898, time: 1466.8s
- Checks that could not be evaluated (e.g. judge errors): 0
  - agent: 117 calls (0 cache hits), 208944 tokens
  - guard: 20 calls (47 cache hits), 6120 tokens
  - judge: 11 calls (0 cache hits), 15800 tokens
  - reflector: 0 calls (1 cache hits), 0 tokens
  - simulator: 50 calls (0 cache hits), 43034 tokens
