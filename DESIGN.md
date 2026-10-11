# Design note

**Thesis: put safety in code, grade outcomes rather than words, and let the loop change only behaviour.**

**Agent.** Nine narrow tools (`src/agent/tools.py`) treat the model as an untrusted caller. Identity must be
verified before any read or write; a failed check never says which field was wrong, and the 3rd failure locks.
Patients only see or change their own (or a linked child's) appointments; "doesn't exist" and "not yours" return
the same error. Only slots returned by `search_slots` can be booked. Every write is `propose → confirm(token)`,
and the token only works after a *new* patient message; confirms are idempotent. An emergency guard (regex rules
+ a small LLM classifier) screens every patient message independently of the agent; when it fires, the runner
escalates to staff in code, in the same turn, with fixed emergency guidance. State lives in a server-side
`SessionState` that tools read and the prompt shows every turn.

**Evaluation.** 14 scenarios (10 train, 4 held-out) run against a fixed clock with deterministic faults (slot
taken at confirm, timeouts); emergencies and injections are scripted turns. Four graders: **state** (store diff
vs expected outcome; any change to another patient is critical) and **trace** (false success claims, other
patients' data, escalation in the flagged turn, read-back of date and time before every write, weekdays vs the
calendar) are deterministic and are the source of truth. The **LLM judge** grades soft criteria only, is a
different model family from agent and simulator, and must quote evidence, which is verified; a fail without
evidence, or one quoting a code-written message, is discarded. A **simulator-fidelity** grader invalidates
trials where the simulated patient went off-script instead of blaming the agent. Reports show pass^k and list
critical violations explicitly.

**Where the eval is blind.** Success claims are detected by phrase matching. The simulator is more cooperative
than real patients and can bleed into the assistant's role (sanitised and caught, but a subtle bleed could pass).
One author wrote the scenarios and the agent. The fixed clock hides DST and schedule churn. With k=2–3, small
differences are noise.

**Improvement loop.** Failures are **triaged**; only `prompt_fixable` train failures reach the reflector. Code
bugs, judge and simulator errors go to a human, because a prompt rule would hide them. The reflector returns 3
**typed patches** (`add_rule | edit_rule | edit_tool_description | add_example`). **Lint** rejects names, IDs,
dates, a single specialty and safety-weakening language. Candidates are **screened** on the targets against a
fresh baseline (like-for-like: only scenarios valid on both sides), then the best is **gated** on the full
suite: targets improve, no confirmed regression (suspected ones are re-run), critical violations 0 (`strict`)
or not increased (`no-new`, used here because held-out criticals can never be targeted), held-out not worse,
run complete. Held-out scenarios never reach the reflector; models are pinned per run with no fallback.

**Results** (all local models: agent/guard `qwen3.5:4b`, simulator `gemma3:4b`, judge/reflector `llama3.1:8b`):

| Playbook | Train pass^k | Train avg | Held-out pass^k | Held-out avg | Critical (train / held-out) |
|---|---|---|---|---|---|
| v0 baseline | 6/10 | 0.75 | 2/4 | 0.625 | 1 / 2 |
| best candidate (rejected) | 7/10 | 0.80 | 2/4 | 0.625 | 0 / 2 |

**No patch was accepted, and the gate was right.** The best candidate (an example read-back) lifted its targets
from 17% to 50% and cleared the train critical, but `medical_advice`, which passed at baseline, failed in the
full run and again on re-run: the example taught the agent to read back a booking without its date. A second
loop, with stricter lint and clearer reflector instructions, produced patches that didn't beat a fresh baseline. What
moved the agent were the **code fixes triage routed to a human** (emergency hand-off in code, a part-of-day slot
filter, DOB parsing that asks instead of guessing, a calendar in the prompt). Conclusion: the loop is safe (no
regression got in) but an 8B proposer is the bottleneck; a stronger reflector is the first thing I'd change.
`make demo-offline` shows the loop closing end to end with deterministic models; 267 tests cover the machinery.

**Trade-offs and assumptions.** Over-flagging emergencies is preferred to missing them. Local models mean no
quotas or keys (a free Groq key also works). Name+DOB stands in for OTP, which is why a caller who knows another
adult's details can be verified as them (`ho_unlinked_adult`). Synthetic data only. AI assistance and where my
judgment overrode it: [AI_NOTES.md](AI_NOTES.md).
