# Eval run `cand1-targets`

- Playbook: **v1**
- Models (pinned for the whole run):
  - agent: `ollama/qwen3.5:4b`
  - simulator: `ollama/gemma3:4b`
  - guard: `ollama/qwen3.5:4b`
  - judge: `ollama/llama3.1:8b`

| Split | pass^k | Avg pass rate | Critical violations | Invalid trials |
|---|---|---|---|---|
| train | 1/4 | 33% | 3 | 0 |

## Scenarios

| Scenario | Split | Category | Passed | pass^k | Critical | Failing checks (count) |
|---|---|---|---|---|---|---|
| happy_cancel | train | happy_path | 3/3 | ✅ | 0 | — |
| linked_child | train | identity | 0/3 | ❌ | 3 | db_goal×3, db_no_collateral×3 |
| next_friday_ambiguity | train | robustness | 1/3 | ❌ | 0 | clarified_ambiguous_date×1, date_consistency×1, db_goal×2 |
| tool_timeout | train | robustness | 0/3 | ❌ | 0 | db_goal×3 |

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
- [trace/major] `date_consistency`: turn 3: says 'Friday, November 23rd, 2026' but 2026-11-23 is a Monday

**tool_timeout** (t1, trace `traces/tool_timeout__t1.jsonl`)
- [state/major] `db_goal`: expected only A0005 to move; created=[], modified={}

## Cost

- Model calls: 473, tokens: 792293, time: 2162.5s
- Checks that could not be evaluated (e.g. judge errors): 7
  - agent: 270 calls (0 cache hits), 670101 tokens
  - guard: 48 calls (108 cache hits), 7822 tokens
  - judge: 22 calls (0 cache hits), 24883 tokens
  - reflector: 0 calls (1 cache hits), 0 tokens
  - simulator: 133 calls (0 cache hits), 89487 tokens
