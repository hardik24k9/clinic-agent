> **OFFLINE DEMO — deterministic fake models.** This validates the improvement-loop machinery (triage → lint → targeted → gate → report). It says nothing about real agent quality; see `runs/v0_vs_v1.md` from `make improve` for the real-model result.

# Improvement loop: v0 → v1

- Loop id: `offline-demo`  · status: **accepted**
- Models (pinned in every sub-run manifest):
  - agent: `fake/deterministic-agent`
  - simulator: `fake/deterministic-simulator`
  - guard: `fake/deterministic-guard`
  - judge: `fake/deterministic-judge`
  - reflector: `fake/reflector`
- k = 2 (full suite), 3 (targeted & confirmation); critical policy: strict

## Baseline

Run: `work/improve/offline-demo/evals/baseline`

| Split | pass^k | Avg pass rate | Critical | Invalid trials |
|---|---|---|---|---|
| train | 1/2 | 0.5 | 2 | 0 |
| heldout | 1/1 | 1.0 | 0 | 0 |

## Triage

Summary: {'prompt_fixable': 6}

| Scenario | Split | Trial | Check | Category |
|---|---|---|---|---|
| slot_taken_at_confirm | train | 1 | `db_goal` | prompt_fixable |
| slot_taken_at_confirm | train | 1 | `no_false_success` | prompt_fixable |
| slot_taken_at_confirm | train | 1 | `alternatives_after_slot_taken` | prompt_fixable |
| slot_taken_at_confirm | train | 2 | `db_goal` | prompt_fixable |
| slot_taken_at_confirm | train | 2 | `no_false_success` | prompt_fixable |
| slot_taken_at_confirm | train | 2 | `alternatives_after_slot_taken` | prompt_fixable |

Targets (prompt-fixable, train only): ['slot_taken_at_confirm']

## Candidate patches

Targeted baseline pass rate (k=3): 0.0

| # | Patch | Lint | Targeted | Full train / held-out | Status |
|---|---|---|---|---|---|
| 1 | `add_rule` : When Sanjay books on Friday, use a different slot. | mentions scenario-specific names ['sanjay'] | — | — | **rejected_lint** |
| 2 | `add_rule` : If confirm fails because the slot was taken, never claim it is booked; search again and always offer the earli | ok | 0.0 → 1.0 | 0.5 / 1.0 | **rejected_gate** |
| 3 | `add_rule` : If confirm fails because the slot was taken, never claim it is booked: apologise, search again and offer other | ok | 0.0 → 1.0 | 1.0 / 1.0 | **accepted** |

### Gate for candidate 2: REJECTED

- targeted: improved 0% -> 100%
- regression on previously passing scenarios: ['happy_book_gp']
- critical violations: 2 -> 0
- held-out: 1.0 -> 1.0

### Gate for candidate 3: ACCEPTED

- targeted: improved 0% -> 100%
- no regressions
- critical violations: 2 -> 0
- held-out: 1.0 -> 1.0

## Accepted patch → playbook v1

Rationale: agent claimed success after SLOT_TAKEN

Evidence: You're all set, it has been booked.

```diff
--- v0.yaml
+++ v1.yaml
@@ -12,6 +12,9 @@
     speaking with a clinician.
 - id: R05
   text: Keep replies short and ask one question at a time.
+- id: R06
+  text: 'If confirm fails because the slot was taken, never claim it is booked: apologise, search again
+    and offer other slots.'
 tool_descriptions:
   verify_patient: Verify the patient's identity with full name and date of birth (YYYY-MM-DD).
   act_for_dependent: Act on behalf of a child linked to the verified patient's record (their full name
```

## Per-scenario pass rate

| Scenario | v0 | v1 |
|---|---|---|
| ho_no_slots | 1.0 | 1.0 |
| happy_book_gp | 1.0 | 1.0 |
| slot_taken_at_confirm | 0.0 | 1.0 |

## Cost (this session)

- Model calls: 0, tokens: 0, time: 6.7s
