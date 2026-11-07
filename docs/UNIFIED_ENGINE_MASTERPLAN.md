# Unified Engine Master Plan — V1 Compatibility + V2 Core

> Status: **proposal / planning**, no engine behavior changes yet.
> Last updated: 2026-11-07
> Owner decision required before the default engine is flipped.
>
> This is the staged plan for the macro architecture:
>
> **V2 engine as the default heart + V1 UX/CLI/dashboard as the face.**
>
> The current release remains `v1.0.5` default and `v2.1.0` opt-in until the
> final gates in Phase 7 pass.

---

## 1. Executive decision

The architecture direction is approved as a target:

```text
                 Unified operator surface
       swarm CLI / OpenCode skill / War Room
                          |
                          v
                  UnifiedOrchestrator
       preflight -> planner -> usage -> execution
                          |
                          v
              v2 Runner (default engine)
       Git worktree + budget + capability bundle
                          |
                          v
          verify -> checkpoint -> handoff / not merge
                          |
             +------------+------------+
             v                         v
       V1 compatibility          Unified dashboard
       job.py / recipe_c.py     one War Room at 8501
```

The migration must use the **strangler pattern**:

1. Add the unified layer beside the current code.
2. Route one surface at a time through it.
3. Keep a `--legacy` escape hatch.
4. Prove parity with deterministic tests.
5. Flip the default only at the end.
6. Keep `v1.0.5` available for rollback.

Do **not** start by editing `job.py`/`recipe_c.py` internals or by changing
`README` to say “v2 default”. Those are consequences of the migration, not the
first implementation step.

---

## 2. Current baseline (what is true today)

### 2.1 Ready

- v2 guarded Git worktree.
- v2 reserve/settle budget and actual usage reconciliation.
- Usage approval with plan/estimate hash binding.
- v2 four-reviewer execution path and E4 blackboard meeting path.
- Five adaptive capabilities through `capability_runtime.py`.
- `v2 merge` and `v2 rollback`.
- `free-only` and `strict` presets.
- `/v2` spend dashboard, handoff preview, alerts, doctor, prune.
- 48 Python test files pass locally.
- `v1.0.5` remains the default; `v2.1.0` GitHub release/tag is old.

### 2.2 Gaps that block a default flip

| Gap | Current evidence | Consequence |
|---|---|---|
| E1/E2 not executable | `ExecutionEngine` requires all 4 reviewer roles before running | “auto E1–E4” is not true yet |
| No planner adapter | v2 `prepare` + external `plan` file | cannot run goal → plan in one command |
| No shell `swarm` command | opencode `/swarm` is a skill command, not a terminal binary | plan’s one-command flow does not exist |
| v1 commands are independent | `job.py`, `recipe_c.py` use v1 engine/direct writes | wrappers need a real adapter, not aliases |
| State is split | v1 ledger vs `.agents/tmp/v2/runs` events | dashboard/status cannot be unified without projection |
| Dashboard is split | v1 `/` and v2 `/v2`; default port 8080 | no single War Room at 8501 |
| Parity is narrow | current v2 parity covers E3/E4, not v1-vs-v2 | no proof that merge preserves behavior |
| Release tag is old | `v2.1.0` points to old commit | must not retag; new release is required |

---

## 3. Non-goals and invariants

### Non-goals

- Do not remove v1 files in this migration.
- Do not add DSPy/Mem0/Letta/LiteLLM/AutoGen runtime dependencies.
- Do not auto-merge during `execute`.
- Do not claim OS/container sandbox isolation.
- Do not move or force-push the public `v2.1.0` tag.
- Do not support concurrent runs in one repository beyond the existing lock.

### Invariants

1. `safe_writer` remains the only project writer.
2. v2 execution writes only inside an isolated Git worktree.
3. Main branch changes only through explicit `v2 merge`.
4. Every work order needs a machine `verify_command`.
5. Budget and usage gates cannot be bypassed by compatibility wrappers.
6. Native v1 and v2 state stay readable during migration.
7. Every phase has a rollback path and a test gate.
8. The default engine flips only after all Phase 7 gates pass.

---

## 4. Target contracts

### 4.1 Unified request

Add a normalized contract (implementation name is illustrative):

```json
{
  "schema_version": "1.0",
  "source": "swarm-cli|opencode|dashboard|job-v1|recipe-v1",
  "goal": "human-readable task",
  "plan": null,
  "order": null,
  "preset": "free-only|strict|null",
  "tier": "E1|E2|E3|E4|null",
  "planner_level": "P1|P2|P3|null",
  "mode": "panel|wave|meeting",
  "reviewer": "heuristic|wave|meeting",
  "merge": false,
  "dry_run": false,
  "root": ".",
  "state_root": null,
  "legacy": false
}
```

Rules:

- `merge=false` by default for every source.
- A v1 wrapper can accept `--merge` only as an explicit flag; it must print the
  handoff and require confirmation unless `--yes` is supplied.
- `tier` is selected by policy if absent; do not hardcode E3 in the adapter.

### 4.2 Unified result

```json
{
  "schema_version": "1.0",
  "engine": "v2",
  "source": "swarm-cli",
  "run_id": "v2_...",
  "tier": "E2",
  "planner": "P1",
  "status": "HANDOFF_READY",
  "main_unchanged": true,
  "worktree_path": "...",
  "verify": {"exit_code": 0, "commands": []},
  "usage": {"estimate": {}, "approval": "approved|not_required|waiting"},
  "handoff": {"patch": "...", "report": "..."},
  "merge": null,
  "legacy": false
}
```

### 4.3 Normalized event projection

The unified dashboard must not require rewriting v1 or v2 storage. Add a
read-only projector:

```text
v1: .agents/ledger.jsonl
v2: .agents/tmp/v2/runs/<run>/events.ndjson
          |
          v
UnifiedRunProjector -> one UI shape:
{
  run_id, source_engine, status, tier, planner,
  phase, started_at, updated_at, budget, verify,
  handoff, meeting, warnings
}
```

The projector must be deterministic and idempotent. It must never mutate
either ledger.

---

## 5. Target workstreams

| ID | Workstream | Main outcome |
|---|---|---|
| U0 | Baseline and flags | freeze behavior, feature-gate the migration |
| U1 | Unified contracts and state | shared request/result/state projection |
| U2 | Tier-aware execution | real E1–E4 runtime |
| U3 | Planner + orchestrator | goal → plan → run |
| U4 | One-command `swarm` | terminal one-command flow |
| U5 | V1 compatibility | old CLI inputs route to v2 safely |
| U6 | Unified War Room | one dashboard at 8501 |
| U7 | Parity, security, release | prove and publish the flip |

Dependency order:

```text
U0 -> U1 -> U2 -> U3 -> U4 -> U5 -> U6 -> U7
                         \________/
                         parity starts here
```

---

## 6. Phase-by-phase plan

## Phase 0 — Baseline, flags, and guardrails

### Goal

Make the current behavior measurable and reversible before changing routing.

### Tasks

#### U0.1 Freeze the baseline

- Record current `main` SHA and test counts.
- Run all 48 test files sequentially.
- Record v1 default behavior and v2 opt-in behavior.
- Add `docs/UNIFIED_ENGINE_BASELINE.md` with commands/results.

**Acceptance:** baseline test run has 0 failures and is reproducible.

#### U0.2 Add engine flags

- Add `SWARM_ENGINE=legacy|v2` (default `legacy` for now).
- Add `SWARM_UNIFIED=dashboard|api` feature flag if needed.
- Ensure unknown values fail closed with a clear error.
- Log the selected engine in one event field.

**Acceptance:** setting no flag preserves current behavior exactly.

#### U0.3 Add compatibility contract tests

- Snapshot current `job.py`, `recipe_c.py`, and v2 CLI JSON outputs.
- Snapshot exit codes.
- Add tests that detect accidental output/state changes.

**Acceptance:** future phases can prove they did not silently change old
interfaces.

### Phase 0 exit gate

```text
[ ] all 48 test files pass
[ ] v1 default path unchanged
[ ] v2 path unchanged when explicitly selected
[ ] rollback flag documented
```

---

## Phase 1 — Unified contracts and state projection

### Goal

Create one internal language for requests/results and one read-only view of
v1 + v2 state. No routing changes yet.

### Tasks

#### U1.1 `unified/contracts.py`

- `UnifiedRequest`, `UnifiedResult`, validation, JSON serialization.
- Reject `merge=true` unless source explicitly opts in.
- Preserve unknown fields for forward compatibility? No: reject unknown
  security-sensitive flags; pass provenance metadata separately.

#### U1.2 `unified/state_bridge.py`

- Read v1 events from `.agents/ledger.jsonl`.
- Read v2 run manifests/events from `.agents/tmp/v2/runs/`.
- Normalize:
  - run id
  - engine/source
  - status
  - tier/planner
  - phases
  - requests/cost/tokens
  - verify result
  - handoff/merge/rollback
  - meeting transcript references
- Do not modify source files.
- Handle missing/corrupt events explicitly.

#### U1.3 `unified/state_bridge` tests

- Empty repo.
- v1-only run.
- v2-only run.
- Both present.
- Corrupt/partial events.
- Same run id collision across sources.

**Acceptance:** one API can list a mixed v1/v2 run history without writing to
either ledger.

### Phase 1 exit gate

```text
[ ] UnifiedRequest/Result round-trip
[ ] mixed-state projection test passes
[ ] no changes to job.py/recipe_c.py/dashboard behavior
```

---

## Phase 2 — Tier-aware execution (E1–E4)

### Goal

Make v2 genuinely tier-aware. This is the largest engine change and must be
isolated from the compatibility layer.

### Current blocker

`ExecutionEngine.execute()` currently does:

```python
missing = [role for role in REVIEW_ROLES if role not in reviewers]
if missing:
    raise ExecutionError(...)
```

It also cross-checks all four families before execution. `execution_tier` only
chooses E4 meeting vs the normal path after that.

### Target tier matrix

| Tier | Reviewers | Planner | Meeting | Use case |
|---|---:|---|---|---|
| E1 | 0 | P1 | no | tiny, low-risk, explicit opt-in |
| E2 | 1 | P1 | no | normal quick path |
| E3 | 4 | P2 | no | wave/security-sensitive change |
| E4 | 4 | P3 | yes | architecture/high-risk decisions |

### Tasks

#### U2.1 Define tier runtime profile

Add a single mapping consumed by execution and CLI:

```python
TierRuntimeProfile(
    tier="E1",
    reviewer_roles=(),
    required_planner="P1",
    meeting=False,
    minimum_risk="low",
)
```

Use `tier_policy.TIERS` as the source of truth; do not duplicate counts.

#### U2.2 Modify `ExecutionEngine.execute`

- Accept a resolved `execution_tier` from the plan/request.
- Validate only the reviewer roles required by that tier.
- Preserve cross-family checks for present reviewers.
- E1 must require an explicit `allow_unreviewed`/`risk=low` policy, not silently
  allow any change.
- E4 preserves the existing blackboard meeting path.
- Unknown/missing tier: default to E3 for backward compatibility of old v2
  plans; do not silently choose E1.

#### U2.3 Update `run.py execute`

- Accept `--tier` or use `plan.execution_tier`.
- Stop requiring all four reviewer models for E1/E2.
- Keep E3/E4 requiring four reviewers.
- Add `--legacy` to opt out of the unified adapter later.

#### U2.4 Tests

Add `test_v2_tiers.py`:

- E1: zero reviewer calls, verify still required.
- E2: one reviewer, family check.
- E3: four reviewer calls.
- E4: meeting transcript + Round 2 on conflict.
- E1 risk/secret change must fail closed.
- Missing reviewer for E3 must fail closed.
- Backward-compatible old plan without tier defaults to E3.

### Phase 2 exit gate

```text
[ ] E1–E4 execute with expected seat counts
[ ] E4 blackboard still passes existing parity test
[ ] old v2 plans behave as before
[ ] no changes to v1 entrypoints
```

---

## Phase 3 — Planner adapter and orchestrator

### Goal

Turn:

```text
goal -> plan -> prepare -> usage -> execute -> handoff
```

into one internal orchestrated flow.

### Tasks

#### U3.1 `unified/planner_adapter.py`

Input sources:

- v1 `planner.py` output (`plan.json`).
- Spec Kit `tasks.yaml` (reuse `v2/tasks.py` bridge).
- Existing v2 `MasterPlan.json`.

Output:

- validated v2 plan with `execution_tier`, `planner_level`, source artifacts.

Rules:

- Planner is mandatory for natural-language goals.
- Do not bypass `validate_plan`.
- Preserve plan hash and source hashes.
- Reject a plan that cannot satisfy the tier/usage policy.

#### U3.2 `unified/orchestrator.py`

Flow:

```text
preflight
  -> normalize request
  -> select tier + planner level
  -> generate plan if needed
  -> v2 prepare
  -> v2 submit_plan
  -> usage approval decision
  -> v2 execute
  -> verify/checkpoint/handoff
  -> notify
  -> return UnifiedResult
```

Important:

- `prepare` must create a worktree before any model call.
- Usage approval is a gate, not a plan approval.
- `merge` is never automatic.
- On failure, preserve v2 quarantine/state; do not write main.

#### U3.3 Presets in orchestrator

- `free-only`: free planner/seats; paid approval never requested.
- `strict`: 4 reviewers, secret scanning, low Usage thresholds.
- Preset is persisted in the v2 manifest.

#### U3.4 Tests

- `test_unified_orchestrator.py`:
  - goal -> plan via fake planner
  - plan validation failure
  - usage required / not required
  - preset propagation
  - Telegram event emission (mocked)
  - failure leaves main unchanged.

### Phase 3 exit gate

```text
[ ] one service call can run a task end-to-end in a temp repo
[ ] no dashboard or v1 wrapper changes yet
[ ] merge remains explicit
```

---

## Phase 4 — One-command `swarm`

### Goal

Provide the surface from the macro plan:

```bash
swarm "Làm tính năng X"
```

while preserving the OpenCode skill entrypoint.

### Tasks

#### U4.1 `swarm.py` CLI

Proposed commands:

```text
python .agents/swarm.py "task" [--preset free-only|strict]
python .agents/swarm.py "task" --tier E2
python .agents/swarm.py "task" --merge          # explicit
python .agents/swarm.py "task" --dry-run
python .agents/swarm.py status <run_id>
python .agents/swarm.py doctor
```

Behavior:

1. doctor/preflight
2. plan
3. usage gate (interactive/`--wait`; print instructions in non-TTY)
4. v2 execute
5. verify
6. handoff
7. notify
8. stop before merge unless `--merge` was explicitly supplied

#### U4.2 Shell wrappers

```text
swarm.cmd   (Windows)
swarm.sh    (Linux/macOS)
```

Wrappers only forward to `python .agents/swarm.py`.

#### U4.3 OpenCode surface

Keep:

```text
.opencode/commands/swarm.md
.opencode/skills/adaptive-swarm-pipeline/SKILL.md
```

Make `/swarm` and the terminal `swarm` share the same printed workflow and
result shape. `/swarm` remains an agent instruction; terminal `swarm` is the
actual CLI.

#### U4.4 Tests

- `test_swarm_cli.py`:
  - no arguments
  - free-only
  - strict
  - dry-run
  - non-TTY usage gate
  - explicit `--merge`
  - status/doctor passthrough
  - failure exits non-zero
  - main unchanged before merge.

### Phase 4 exit gate

```text
[ ] `swarm "task"` reaches HANDOFF_READY
[ ] default does not merge
[ ] OpenCode `/swarm` still works
[ ] no v1 files changed yet
```

---

## Phase 5 — V1 compatibility layer

### Goal

Make old commands route to v2 without removing v1 internals.

### Compatibility rules

1. Wrappers live at CLI boundaries, not inside `recipe_c.run()`.
2. `--legacy` executes the old v1 path exactly.
3. No automatic merge.
4. Output must preserve documented fields and exit codes.
5. If the repo is not a Git worktree, fail with a clear message and suggest
   `--legacy` or `git init`.
6. Lock acquisition happens before either engine starts.

### Tasks

#### U5.1 `unified/compat.py`

- Normalize v1 plan to a v2 plan.
- Normalize a single v1 Work Order to a one-phase v2 plan.
- Preserve `verify_command`, target files, level, rounds, and acceptance
  criteria.
- Map v1 tier/reviewer semantics to unified tier policy.

#### U5.2 `job.py` wrapper

- Keep old argparse and output.
- Default route: v2 orchestrator.
- `--legacy`: old implementation.
- `--merge`: explicit optional post-handoff merge.
- Preserve `--plan`, `--resume`, token, control behavior where practical.
- Add a compatibility report section: `engine=v2`, `run_id`, `handoff`.

#### U5.3 `recipe_c.py` wrapper

- `--order` -> one-phase MasterPlan.
- `--legacy`: old direct recipe.
- Preserve JSON report keys where possible.
- Do not auto-apply.

#### U5.4 Tests

- `test_v1_compat.py`:
  - same order via legacy and v2 produces same final worktree content.
  - job plan input accepted.
  - exit codes preserved.
  - `--legacy` truly uses v1.
  - main unchanged before merge.
  - output contains run id/handoff.
  - non-Git repo error is actionable.

### Phase 5 exit gate

```text
[ ] old commands still parse old arguments
[ ] default routes to v2
[ ] --legacy passes old golden output tests
[ ] no silent auto-merge
```

---

## Phase 6 — Unified War Room Dashboard

### Goal

One URL: `http://127.0.0.1:8501`.

### UX target

Main screen:

1. Run progress: Planning → Worktree → Verifying → Handoff.
2. Spend reconciliation: estimates vs actual, tokens, calls, remaining.
3. Usage approval: [Approve Usage] when required.
4. E4 blackboard: Round 1/2 messages/votes.
5. Handoff: diff preview + [Merge into Main] + [Rollback].
6. Run history across v1/v2 sources.

### Tasks

#### U6.1 Unified API

Add read-only endpoints first:

```text
GET /api/unified/runs
GET /api/unified/runs/<id>
GET /api/unified/runs/<id>/diff
GET /api/unified/runs/<id>/events
```

Then controlled actions:

```text
POST /api/unified/usage/approve
POST /api/unified/merge
POST /api/unified/rollback
```

Security:

- bind `127.0.0.1` by default;
- keep token/control rules;
- no remote network exposure;
- diff/report size caps;
- redact secrets in displayed messages.

#### U6.2 UI shell

Preferred approach: keep the v1 dashboard shell because operators already know
it, then mount unified panels into it.

- Replace the `/` ↔ `/v2` navigation with one War Room.
- Keep `/v2` as a redirect/alias for old bookmarks.
- Default port `8501`; support `--port 8080` for compatibility.
- Keep the dark mission-control visual language.

#### U6.3 Blackboard panel

- Read v2 meeting events from `events.ndjson`.
- Show Round 1, conflicts, Round 2, votes and final verdict.
- Never replay dangerous shell commands from event text.

#### U6.4 Delivery panel

- Show handoff patch.
- Merge button calls v2 delivery and requires explicit confirmation.
- Rollback button calls v2 rollback and records the revert.
- Show pre-merge/merge/rollback SHAs.

#### U6.5 Tests

- `test_unified_dashboard.py`:
  - mixed v1/v2 history
  - spend projection
  - usage approval
  - meeting panel data
  - handoff diff
  - merge/rollback endpoints with temp repo
  - 8501 default and 8080 explicit
  - local-only binding.
- Optional JS syntax check with Node for both legacy and unified UI scripts.

### Phase 6 exit gate

```text
[ ] one dashboard URL shows v1 + v2 runs
[ ] E4 blackboard visible
[ ] diff/merge/rollback available on web
[ ] no secrets exposed
[ ] existing v1 dashboard tests still pass
```

---

## Phase 7 — Parity, security, and release

### 7.1 Parity matrix

Run the same fixture through:

```text
A. v1 legacy
B. v2 unified
C. v2 unified + explicit merge
```

Compare:

- final target file bytes
- verify exit code
- main unchanged before merge
- patch contents
- no duplicate writes
- tier/planner selection
- cost/usage record
- exit codes and JSON compatibility.

### 7.2 Required test suites

New:

```text
test_unified_contracts.py
test_unified_state.py
test_v2_tiers.py
test_unified_orchestrator.py
test_swarm_cli.py
test_v1_compat.py
test_unified_dashboard.py
test_unified_parity.py
```

Existing mandatory:

```text
V2 DELIVERY
V2 PRESETS
V2 M1/M2/M3/M5
V2 P0
V2 PARITY
V2 WEB UI
V2 ALERTS
V2 DOCTOR
V2 PRUNE
CAPABILITIES UNIFORM
SPECKIT CONVERGE
RESILIENCE
PHASE 4/5/6
SKILL CONSISTENCY
```

### 7.3 Release gates

Before flipping default:

- [ ] all deterministic suites pass
- [ ] v1 vs v2 parity passes on at least 3 fixtures
- [ ] E1/E2 risk policy reviewed
- [ ] live free-model run completes
- [ ] 8-hour soak or agreed equivalent passes
- [ ] crash/resume and merge/rollback pass
- [ ] dashboard local-only security review
- [ ] cross-platform CI passes
- [ ] migration and rollback docs complete
- [ ] owner explicitly accepts the default flip

### 7.4 Versioning

Do not retag or move the public `v2.1.0`.

Recommended:

```text
v3.0.0   if v2 becomes the default engine and v1 becomes compatibility
v2.2.0   if unified mode remains opt-in for one more release
```

Release assets must include:

- source zip
- SHA256
- release notes
- OpenCode skill setup
- v1 compatibility notes
- rollback instructions.

### 7.5 Rollback plan

- Set `SWARM_ENGINE=legacy`.
- Keep v1.0.5 tag and code path.
- Do not delete v1 entrypoints until a later major release.
- Keep v2 state/handoff evidence after rollback.
- If a merged change is wrong: `v2 rollback --run-id <id>`.

---

## 7. Commit / PR sequence

Suggested sequence, one review gate per step:

```text
01 docs: add unified engine master plan
02 test(unified): freeze baseline contracts
03 feat(unified): add request/result contracts
04 feat(unified): add read-only v1/v2 state bridge
05 feat(v2): make execution tier-aware E1-E4
06 feat(unified): add planner adapter and orchestrator
07 feat(cli): add swarm command and shell wrappers
08 feat(compat): route job.py and recipe_c.py through v2 with --legacy
09 feat(ui): add unified War Room API
10 feat(ui): merge v1/v2 dashboard panels and 8501 port
11 test(unified): add v1-vs-v2 parity and dashboard acceptance
12 docs: update release story and compatibility matrix
13 chore(release): tag the new version after owner approval
```

Do not collapse these into one commit. Each step needs its own test evidence and
rollback.

---

## 8. Risk register

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| v1 wrapper changes semantics | high | high | `--legacy`, output snapshots, explicit merge |
| E1 allows unsafe changes | medium | high | E1 low-risk-only policy; secret scanner; verify mandatory |
| Dual state diverges | high | medium | read-only projection first; no writes to old ledger |
| Dashboard port change breaks users | medium | medium | 8501 default + 8080 fallback + alias `/v2` |
| Merge applies unexpected files | low | critical | path allowlist + base check + verify + rollback |
| Release tag confusion | high | medium | new version, never retag v2.1.0 |
| Live provider quota | medium | medium | free-only preset, usage gate, deterministic tests |
| Windows path/shell differences | medium | medium | CI matrix + doctor + `.cmd` wrappers |

---

## 9. Decisions required from the owner

These must be answered before their phase starts:

1. **V1 wrapper default behavior**
   - Recommended: v2 engine by default, `--legacy` fallback, no auto-merge.
2. **Dashboard port**
   - Recommended: 8501 default, 8080 compatibility alias.
3. **E1 policy**
   - Recommended: E1 only with explicit low-risk opt-in; otherwise E2 minimum.
4. **Non-Git projects**
   - Recommended: unified v2 path requires Git; `--legacy` for non-Git.
5. **Release version**
   - Recommended: `v3.0.0` if the default changes.
6. **Default flip timing**
   - Recommended: only after Phase 7 gates, not during Phase 1–6.

---

## 10. Definition of done

The migration is complete only when:

- [ ] `swarm "task"` runs the full flow and leaves main untouched.
- [ ] E1–E4 are real, tested execution tiers.
- [ ] old v1 CLI commands still work through the compatibility layer.
- [ ] `--legacy` reproduces the old behavior.
- [ ] one War Room at 8501 shows both historical and new runs.
- [ ] usage, diff, merge and rollback are usable from the web.
- [ ] v1-vs-v2 parity tests pass.
- [ ] all prior suites pass.
- [ ] a new release tag exists; `v2.1.0` is untouched.
- [ ] rollback to v1 is documented and tested.
