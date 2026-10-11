# Eval run `cand2-targets`

- Playbook: **v1**
- Models (pinned for the whole run):
  - agent: `ollama/qwen3.5:4b`
  - simulator: `ollama/gemma3:4b`
  - guard: `ollama/qwen3.5:4b`
  - judge: `groq/openai/gpt-oss-120b`

| Split | pass^k | Avg pass rate | Critical violations | Invalid trials |
|---|---|---|---|---|
| train | 1/4 | 42% | 2 | 2 |

## Scenarios

| Scenario | Split | Category | Passed | pass^k | Critical | Failing checks (count) |
|---|---|---|---|---|---|---|
| happy_book_gp | train | happy_path | 1/1 (+2 invalid) | ✅ | 0 | — |
| linked_child | train | identity | 1/3 | ❌ | 2 | db_goal×2, db_no_collateral×2 |
| next_friday_ambiguity | train | robustness | 0/3 | ❌ | 0 | db_goal×3, no_invented_slots×1 |
| tool_timeout | train | robustness | 1/3 | ❌ | 0 | db_goal×2, no_step_limit×1 |

## Critical violations

- **linked_child t1** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- **linked_child t3** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']

## Failure evidence (first failing trial per scenario)

**linked_child** (t1, trace `traces/linked_child__t1.jsonl`)
- [state/critical] `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- [state/major] `db_goal`: A0006: booked for P003 instead of P004

**next_friday_ambiguity** (t1, trace `traces/next_friday_ambiguity__t1.jsonl`)
- [state/major] `db_goal`: A0006: PR2-20261016-1030 (general_medicine, Fri 16 Oct 10:30): date 2026-10-16 != 2026-10-23

**tool_timeout** (t2, trace `traces/tool_timeout__t2.jsonl`)
- [state/major] `db_goal`: expected only A0005 to move; created=[], modified={}
- [trace/major] `no_step_limit`: step limit / model error in turns [3, 5]

## Invalid trials (simulator off-script — excluded from scores)

- happy_book_gp t2: sim_followed_goal: Morning only then.
- happy_book_gp t3: sim_followed_goal: Let’s look at Thursday instead.

## Cost

- Model calls: 597, tokens: 1158447, time: 1950.6s
- Checks that could not be evaluated (e.g. judge errors): 0
  - agent: 344 calls (0 cache hits), 973050 tokens
  - guard: 79 calls (91 cache hits), 12596 tokens
  - judge: 24 calls (0 cache hits), 55279 tokens
  - reflector: 1 calls (0 cache hits), 5876 tokens
  - simulator: 149 calls (0 cache hits), 111646 tokens
