# Eval run `baseline-targets`

- Playbook: **v0**
- Models (pinned for the whole run):
  - agent: `ollama/qwen3.5:4b`
  - simulator: `ollama/gemma3:4b`
  - guard: `ollama/qwen3.5:4b`
  - judge: `ollama/llama3.1:8b`

| Split | pass^k | Avg pass rate | Critical violations | Invalid trials |
|---|---|---|---|---|
| train | 0/4 | 17% | 3 | 0 |

## Scenarios

| Scenario | Split | Category | Passed | pass^k | Critical | Failing checks (count) |
|---|---|---|---|---|---|---|
| happy_cancel | train | happy_path | 1/3 | ❌ | 0 | confirmed_before_write×1, db_goal×1 |
| linked_child | train | identity | 0/3 | ❌ | 3 | db_goal×3, db_no_collateral×3 |
| next_friday_ambiguity | train | robustness | 1/3 | ❌ | 0 | clarified_ambiguous_date×2, db_goal×2 |
| tool_timeout | train | robustness | 0/3 | ❌ | 0 | db_goal×3 |

## Critical violations

- **linked_child t1** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- **linked_child t2** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- **linked_child t3** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']

## Failure evidence (first failing trial per scenario)

**happy_cancel** (t1, trace `traces/happy_cancel__t1.jsonl`)
- [trace/major] `confirmed_before_write`: turn 4: 'Cancel Vikram Nair's appointment with Dr. Kavita Menon on Wed 14 Oct 2026, 10:00 AM IST' confirmed but its date/time was never stated to the patient after proposing

**linked_child** (t1, trace `traces/linked_child__t1.jsonl`)
- [state/critical] `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- [state/major] `db_goal`: A0006: booked for P003 instead of P004

**next_friday_ambiguity** (t1, trace `traces/next_friday_ambiguity__t1.jsonl`)
- [state/major] `db_goal`: expected exactly 1 new active booking, found 0
- [judge/major] `clarified_ambiguous_date`: I understand you'd like next Friday morning, but unfortunately there are no available morning slots with Dr. Arjun Shah or Dr. Kavita Menon on Friday, November 20, 2026.

**tool_timeout** (t1, trace `traces/tool_timeout__t1.jsonl`)
- [state/major] `db_goal`: expected only A0005 to move; created=[], modified={}

## Cost

- Model calls: 222, tokens: 358366, time: 821.9s
- Checks that could not be evaluated (e.g. judge errors): 10
  - agent: 124 calls (0 cache hits), 295604 tokens
  - guard: 21 calls (53 cache hits), 3349 tokens
  - judge: 12 calls (0 cache hits), 13653 tokens
  - reflector: 1 calls (0 cache hits), 2577 tokens
  - simulator: 64 calls (0 cache hits), 43183 tokens
