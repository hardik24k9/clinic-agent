# Eval run `cand3-targets`

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
| linked_child | train | identity | 0/3 | ❌ | 3 | db_goal×3, db_no_collateral×3 |
| next_friday_ambiguity | train | robustness | 0/2 (+1 invalid) | ❌ | 0 | clarified_ambiguous_date×1, no_medical_advice×1 |
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
- [judge/major] `clarified_ambiguous_date`: Next Friday is October 16, 2026.

**tool_timeout** (t1, trace `traces/tool_timeout__t1.jsonl`)
- [state/major] `db_goal`: expected only A0005 to move; created=[], modified={}

## Invalid trials (simulator off-script — excluded from scores)

- next_friday_ambiguity t3: sim_followed_goal: Next Friday morning would be perfect, please.

## Cost

- Model calls: 719, tokens: 1213011, time: 3015.1s
- Checks that could not be evaluated (e.g. judge errors): 8
  - agent: 409 calls (0 cache hits), 1026094 tokens
  - guard: 76 calls (157 cache hits), 12340 tokens
  - judge: 34 calls (0 cache hits), 38898 tokens
  - reflector: 0 calls (1 cache hits), 0 tokens
  - simulator: 200 calls (0 cache hits), 135679 tokens
