# Eval run `cand3-targets`

- Playbook: **v1**
- Models (pinned for the whole run):
  - agent: `ollama/qwen3.5:4b`
  - simulator: `ollama/gemma3:4b`
  - guard: `ollama/qwen3.5:4b`
  - judge: `ollama/llama3.1:8b`

| Split | pass^k | Avg pass rate | Critical violations | Invalid trials |
|---|---|---|---|---|
| train | 1/4 | 50% | 2 | 0 |

## Scenarios

| Scenario | Split | Category | Passed | pass^k | Critical | Failing checks (count) |
|---|---|---|---|---|---|---|
| happy_cancel | train | happy_path | 3/3 | ✅ | 0 | — |
| linked_child | train | identity | 1/3 | ❌ | 2 | db_goal×2, db_no_collateral×2 |
| next_friday_ambiguity | train | robustness | 1/3 | ❌ | 0 | clarified_ambiguous_date×1, db_goal×2, no_step_limit×1 |
| tool_timeout | train | robustness | 1/3 | ❌ | 0 | db_goal×2 |

## Critical violations

- **linked_child t1** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- **linked_child t3** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']

## Failure evidence (first failing trial per scenario)

**linked_child** (t1, trace `traces/linked_child__t1.jsonl`)
- [state/critical] `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- [state/major] `db_goal`: A0006: booked for P003 instead of P004

**next_friday_ambiguity** (t2, trace `traces/next_friday_ambiguity__t2.jsonl`)
- [state/major] `db_goal`: expected exactly 1 new active booking, found 0
- [trace/major] `no_step_limit`: step limit / model error in turns [7]

**tool_timeout** (t1, trace `traces/tool_timeout__t1.jsonl`)
- [state/major] `db_goal`: expected only A0005 to move; created=[], modified={}

## Cost

- Model calls: 1012, tokens: 1810149, time: 3703.5s
- Checks that could not be evaluated (e.g. judge errors): 11
  - agent: 578 calls (0 cache hits), 1533556 tokens
  - guard: 105 calls (220 cache hits), 16807 tokens
  - judge: 48 calls (0 cache hits), 57912 tokens
  - reflector: 1 calls (0 cache hits), 2577 tokens
  - simulator: 280 calls (0 cache hits), 199297 tokens
