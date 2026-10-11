# Eval run `baseline-targets`

- Playbook: **v0**
- Models (pinned for the whole run):
  - agent: `groq/openai/gpt-oss-20b`
  - simulator: `groq/openai/gpt-oss-120b`
  - guard: `groq/openai/gpt-oss-20b`
  - judge: `groq/qwen/qwen3.8-27b`

| Split | pass^k | Avg pass rate | Critical violations | Invalid trials |
|---|---|---|---|---|
| train | 0/3 | 44% | 2 | 0 |

## Scenarios

| Scenario | Split | Category | Passed | pass^k | Critical | Failing checks (count) |
|---|---|---|---|---|---|---|
| linked_child | train | identity | 1/3 | ❌ | 2 | db_goal×2, db_no_collateral×2 |
| next_friday_ambiguity | train | robustness | 1/3 | ❌ | 0 | clarified_ambiguous_date×1, date_consistency×1 |
| wrong_dob_then_corrected | train | identity | 2/3 | ❌ | 0 | date_consistency×1, db_goal×1 |

## Critical violations

- **linked_child t1** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- **linked_child t3** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']

## Failure evidence (first failing trial per scenario)

**linked_child** (t1, trace `traces/linked_child__t1.jsonl`)
- [state/critical] `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- [state/major] `db_goal`: A0006: booked for P003 instead of P004

**next_friday_ambiguity** (t1, trace `traces/next_friday_ambiguity__t1.jsonl`)
- [judge/major] `clarified_ambiguous_date`: Here are the free morning slots with Dr. Arjun Shah on Friday, 16 Oct 2026:

**wrong_dob_then_corrected** (t3, trace `traces/wrong_dob_then_corrected__t3.jsonl`)
- [state/major] `db_goal`: expected exactly 1 new active booking, found 0
- [trace/major] `date_consistency`: turn 3: says 'Friday, 13 Oct 2026' but 2026-10-13 is a Tuesday; tool result says 'Tue 13 Oct 2026'; turn 3: says 'Friday, 20 Oct 2026' but 2026-10-20 is a Tuesday

## Cost

- Model calls: 156, tokens: 221946, time: 1193.0s
- Checks that could not be evaluated (e.g. judge errors): 0
  - agent: 94 calls (0 cache hits), 170451 tokens
  - guard: 15 calls (37 cache hits), 4506 tokens
  - judge: 9 calls (0 cache hits), 13122 tokens
  - reflector: 0 calls (1 cache hits), 0 tokens
  - simulator: 38 calls (0 cache hits), 33867 tokens
