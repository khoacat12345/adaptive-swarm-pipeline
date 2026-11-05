# Shadow Pilot Report — Adaptive Phase 0-6

> Date: 2026-11-05
> Mode: all adaptive flags = `shadow`
> API requests: **0** (fake planner/coder/reviewer backends)
> Task: tiny temporary Git repo, change `src.py` from `VALUE = 1` to `VALUE = 2`

## Configuration

```text
SWARM_ADAPTIVE_REPO_MAP=shadow
SWARM_ADAPTIVE_ACI=shadow
SWARM_ADAPTIVE_LESSONS=shadow
SWARM_ADAPTIVE_MEMORY_BUDGET=shadow
SWARM_ADAPTIVE_EVENTS=shadow
```

## Flow exercised

```text
V2Runner.prepare
→ PlanningCouncil (3 fake planners)
→ repo-map shadow event
→ lessons shadow event
→ submit plan
→ signoff
→ ExecutionEngine
→ ACI shadow validation
→ verify
→ checkpoint
→ handoff
```

## Results

| Check | Result |
|---|---|
| Feature flags | All 5 = `shadow` |
| Repo-map event | `adaptive.repo_map`, 3 files, 23 tokens |
| Repo-map fallback | `false` |
| Lessons event | `adaptive.lessons`, count 0 before seeding |
| Plan submitted | `AWAITING_SIGNOFF` |
| Sign-off | `PROVISIONING` |
| Execution | `HANDOFF_READY` |
| Checkpoints | 1 |
| Repairs | 0 |
| Main worktree unchanged | `true` |
| Worktree value | `VALUE = 2` |
| ACI line window | `1: VALUE = 2` |
| Invalid syntax edit | rejected (`false`) |

## Lessons behavior

Direct controlled failure seed:

```text
count      : 1
revision   : 1
hint       : verify_failure:c3885101ebca: split the change and retry
```

Duplicate signature replaced the prior lesson; only one current lesson remains.

## Memory constraints

| Check | Result |
|---|---|
| Overlong file blocked | `true` |
| Constitution read-only preserved | `true` |
| Corrupt JSONL recovered | `true` |
| Corrupt lines quarantined | `1` |

## Event pagination

```text
page sizes: [2, 2, 1]
total     : 5
```

Pages reconstruct exactly 5 events with no duplicates.

## Important findings

1. **Shadow mode did not inject repo map or lessons into the planner prompt.**
   It emitted only `adaptive.repo_map` and `adaptive.lessons` events.
2. **ACI shadow validation did not block the valid execution path.**
   A separate invalid-syntax proposal was correctly rejected by
   `validate_proposed_files`.
3. **Main worktree stayed untouched**; the change was applied only in the
   guarded worktree and then handed off.
4. `constitution_hash` is `null` in this temp repo because it has no
   `constitution.md`; this is expected.
5. `MEMORY_BUDGET` and `EVENTS` are still module-level checks, not fully wired
   into every live runner path. That remains P2-11 debt.

## Verification command

```bash
python -m unittest discover -s .agents/tests -p "test_v2_*.py"
```

Expected local result: **34/34 PASS**.

## Rollback

Adaptive flags are not persisted in the project:

```powershell
Remove-Item Env:SWARM_ADAPTIVE_REPO_MAP
Remove-Item Env:SWARM_ADAPTIVE_ACI
Remove-Item Env:SWARM_ADAPTIVE_LESSONS
Remove-Item Env:SWARM_ADAPTIVE_MEMORY_BUDGET
Remove-Item Env:SWARM_ADAPTIVE_EVENTS
```

All features then return to `off`.
