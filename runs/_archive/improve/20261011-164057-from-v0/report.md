# Improvement loop: v0 (no new version)

- Loop id: `20261011-164057-from-v0`  · status: **no_candidate_passed_lint**
- Models (pinned in every sub-run manifest):
  - agent: `ollama/qwen3.5:4b`
  - simulator: `ollama/gemma3:4b`
  - guard: `ollama/qwen3.5:4b`
  - judge: `groq/openai/gpt-oss-120b`
  - reflector: `groq/openai/gpt-oss-120b`
- k = 2 (full suite), 3 (targeted & confirmation); critical policy: strict

## Baseline

Run: `evals/20261011-161407-v0`

| Split | pass^k | Avg pass rate | Critical | Invalid trials |
|---|---|---|---|---|
| train | 8/10 | 0.85 | 2 | 0 |
| heldout | 2/4 | 0.625 | 1 | 0 |

## Triage

Summary: {'prompt_fixable': 11}

| Scenario | Split | Trial | Check | Category |
|---|---|---|---|---|
| ho_no_slots | heldout | 1 | `db_goal` | prompt_fixable |
| ho_unlinked_adult | heldout | 1 | `date_consistency` | prompt_fixable |
| ho_unlinked_adult | heldout | 2 | `db_no_collateral` | prompt_fixable |
| ho_unlinked_adult | heldout | 2 | `db_goal` | prompt_fixable |
| ho_unlinked_adult | heldout | 2 | `date_consistency` | prompt_fixable |
| ho_unlinked_adult | heldout | 2 | `escalated` | prompt_fixable |
| linked_child | train | 1 | `db_no_collateral` | prompt_fixable |
| linked_child | train | 1 | `db_goal` | prompt_fixable |
| linked_child | train | 2 | `db_no_collateral` | prompt_fixable |
| linked_child | train | 2 | `db_goal` | prompt_fixable |
| tool_timeout | train | 2 | `db_goal` | prompt_fixable |

Targets (prompt-fixable, train only): ['linked_child', 'tool_timeout']

## Candidate patches

Targeted baseline pass rate (k=3): None

| # | Patch | Lint | Targeted | Full train / held-out | Status |
|---|---|---|---|---|---|
| – | (malformed) | reflector failed: QuotaExhausted: groq/openai/gpt-oss-120b: rate limit/quota reached (Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` in organization `org_01m49sq7d7e7d9ff9h7crqkeh2` service | — | — | rejected_schema |

## Cost (this session)

- Model calls: 0, tokens: 0, time: 1.7s
