# Eval run `baseline-targets`

- Playbook: **v0**
- Models (pinned for the whole run):
  - agent: `ollama/qwen3.5:4b`
  - simulator: `ollama/gemma3:4b`
  - guard: `ollama/qwen3.5:4b`
  - judge: `groq/openai/gpt-oss-120b`

| Split | pass^k | Avg pass rate | Critical violations | Invalid trials |
|---|---|---|---|---|
| train | 0/4 | 22% | 2 | 3 |

## Scenarios

| Scenario | Split | Category | Passed | pass^k | Critical | Failing checks (count) |
|---|---|---|---|---|---|---|
| happy_book_gp | train | happy_path | 0/0 (+3 invalid) | ❌ | 0 | — |
| linked_child | train | identity | 1/3 | ❌ | 2 | db_goal×2, db_no_collateral×2 |
| next_friday_ambiguity | train | robustness | 1/3 | ❌ | 0 | db_goal×2 |
| tool_timeout | train | robustness | 0/3 | ❌ | 0 | db_goal×2, no_step_limit×2 |

## Critical violations

- **linked_child t1** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- **linked_child t3** `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']

## Failure evidence (first failing trial per scenario)

**linked_child** (t1, trace `traces/linked_child__t1.jsonl`)
- [state/critical] `db_no_collateral`: appointments changed for unexpected patient(s): ['P003']
- [state/major] `db_goal`: A0006: booked for P003 instead of P004

**next_friday_ambiguity** (t2, trace `traces/next_friday_ambiguity__t2.jsonl`)
- [state/major] `db_goal`: A0006: PR2-20261016-1000 (general_medicine, Fri 16 Oct 10:00): date 2026-10-16 != 2026-10-23

**tool_timeout** (t1, trace `traces/tool_timeout__t1.jsonl`)
- [state/major] `db_goal`: expected only A0005 to move; created=[], modified={}

## Invalid trials (simulator off-script — excluded from scores)

- happy_book_gp t1: sim_followed_goal: Thursday, October 15th, please.
- happy_book_gp t2: sim_followed_goal: Different day, please.
- happy_book_gp t3: sim_followed_goal: Let’s check for Thursday.

## Cost

- Model calls: 316, tokens: 620657, time: 1056.2s
- Checks that could not be evaluated (e.g. judge errors): 0
  - agent: 180 calls (0 cache hits), 518797 tokens
  - guard: 45 calls (44 cache hits), 7197 tokens
  - judge: 12 calls (0 cache hits), 28066 tokens
  - reflector: 1 calls (0 cache hits), 5876 tokens
  - simulator: 78 calls (0 cache hits), 60721 tokens
