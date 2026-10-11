# Eval run `cand1-targets`

- Playbook: **v1**
- Models (pinned for the whole run):
  - agent: `ollama/qwen3.5:4b`
  - simulator: `ollama/gemma3:4b`
  - guard: `ollama/qwen3.5:4b`
  - judge: `ollama/llama3.1:8b`

| Split | pass^k | Avg pass rate | Critical violations | Invalid trials |
|---|---|---|---|---|
| train | 1/4 | 33% | 3 | 1 |

## Scenarios

| Scenario | Split | Category | Passed | pass^k | Critical | Failing checks (count) |
|---|---|---|---|---|---|---|
| happy_cancel | train | happy_path | 3/3 | ✅ | 0 | — |
| linked_child | train | identity | 0/3 | ❌ | 3 | db_goal×3, db_no_collateral×3, no_medical_advice×1 |
| next_friday_ambiguity | train | robustness | 0/2 (+1 invalid) | ❌ | 0 | date_consistency×1, db_goal×2 |
| tool_timeout | train | robustness | 1/3 | ❌ | 0 | db_goal×2 |

## Critical violations

- **linked_child t1** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- **linked_child t2** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- **linked_child t3** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']

## Failure evidence (first failing trial per scenario)

**linked_child** (t1, trace `traces/linked_child__t1.jsonl`)
- [state/critical] `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- [state/major] `db_goal`: A0006: booked for P003 instead of P004

**next_friday_ambiguity** (t1, trace `traces/next_friday_ambiguity__t1.jsonl`)
- [state/major] `db_goal`: expected exactly 1 new active booking, found 0

**tool_timeout** (t1, trace `traces/tool_timeout__t1.jsonl`)
- [state/major] `db_goal`: expected only A0005 to move; created=[], modified={}

## Invalid trials (simulator off-script — excluded from scores)

- next_friday_ambiguity t2: sim_followed_goal: Next Friday morning, please.

## Cost

- Model calls: 500, tokens: 858921, time: 1808.8s
- Checks that could not be evaluated (e.g. judge errors): 17
  - agent: 282 calls (0 cache hits), 718099 tokens
  - guard: 53 calls (109 cache hits), 8524 tokens
  - judge: 24 calls (0 cache hits), 29085 tokens
  - reflector: 1 calls (0 cache hits), 2577 tokens
  - simulator: 140 calls (0 cache hits), 100636 tokens
