# Live Free-Model Pilot — v2.1 Adaptive

> Date: 2026-11-05
> Goal: run a live free-model planning + execution pilot on a tiny temporary repo.
> Result: **planning succeeded; execution was blocked by free-model quota exhaustion.**
> No paid model was used. Main worktree was never modified.

## Task

Temporary repo:

```text
src.py : VALUE = 1 -> VALUE = 2
check.py : verifies VALUE == 2
```

All adaptive flags were `shadow`:

```text
REPO_MAP=shadow
ACI=shadow
LESSONS=shadow
MEMORY_BUDGET=shadow
EVENTS=shadow
```

## Attempts

### Attempt 1 — live planning PASS

Selected free models:

```text
coder     : opencode/deepseek-v4.1-flash-free
security  : opencode/nemotron-3.5-super-vl-free
qa        : opencode/glm-5.3-flash-free
perf      : opencode/nemotron-3.5-lightning-free
docs      : opencode-go/iceman-contributor-free
```

Planning result:

```text
source    : live-free-planner
phases    : P1
plan_hash : b92999a1c4eddffff537be20be03e3eae0fac7981c38c88aa2a9b420d1970645
```

Execution stopped because `security` hit its free quota.  
At that time the adapter incorrectly translated reviewer infrastructure errors into
`SecurityVeto`.

### Attempt 2 — live planning PASS

Planning result:

```text
source    : live-free-planner
phases    : P1
plan_hash : 5b541e4746f951ba5f0be95751f621fa61de52f926f3753c3ccf48d37c15f45b
```

Execution stopped at `perf` with another free quota error.  
This time the error was correctly classified as `ReviewerUnavailable`, not a
security veto.

### Attempt 3 — live planning PASS

Planning result:

```text
source    : live-free-planner
phases    : P1
plan_hash : 7418c4d824a3bc2c4dbb1e04eedc457d6b661bc020f88f94f72bcf83aab2f893
```

Execution stopped at `docs` with free quota exhaustion.

### Attempt 4 — fallback

Live planners also hit quota, so the pilot used the deterministic fallback plan.
Execution still stopped at `S1` (free coder quota unavailable).

## Useful findings

1. **Live planning works with free models** on this task (3 successful planning
   runs before quota was exhausted).
2. **Repo-map shadow event works in live flow**:
   ```text
   files  : 3
   tokens : 23
   fallback: false
   ```
3. **Lessons shadow event works**: count 0 because the temp repo had no prior
   lessons.
4. **Main worktree stayed unchanged** in every attempt.
5. **Reviewer quota bug was found and fixed**: infrastructure failure is now
   `ReviewerUnavailable` and pauses the run instead of raising `SecurityVeto`.
6. Free-tier quota is the current blocking environment issue, not a code-path
   failure.

## What is not proven yet

- A complete live coder + 4-reviewer + verify + checkpoint + handoff run.
- Actual token/cost savings from repo map or ACI.
- Live memory constraint and event integration.

## Next step

Wait for free quota reset / use another free model pool, then rerun this exact
pilot. Do not use paid models for the acceptance run unless explicitly approved.
