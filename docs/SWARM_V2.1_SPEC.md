# Adaptive Swarm Pipeline v2.1 — Technical Specification

> **Status:** Draft for implementation; M1-M4 core implemented
> **Supersedes:** `SWARM_V2_ARCHITECTURE_DRAFT.md`
> **Scope:** autonomous overnight execution with strict isolation and per-phase budget control
> **Priority order:** safe isolation → budget enforcement → resumability → planning quality → UI
> **Implementation:** `.agents/v2/` (`isolation.py`, `budget.py`, `state.py`, `plan.py`, `planning.py`, `signoff.py`, `execution.py`, `adapters.py`, `web.py`, `runner.py`, `run.py`)

---

## 0. Executive summary

v2.1 is not a bigger prompt and it is not "7 agents in one loop". It is a
**supervised run engine** with four hard boundaries:

1. **Isolation boundary** — every write happens in a dedicated Git worktree.
   The user's working tree is never written to, reset, or cleaned.
2. **Budget boundary** — every model call must reserve budget before it starts.
   The runner stops before a call that would exceed the phase or run cap.
3. **Evidence boundary** — a phase is complete only when a deterministic verify
   command exits `0` inside the isolated worktree and the checkpoint is recorded.
4. **Approval boundary** — the user signs off one immutable plan hash, one budget
   envelope, and one model snapshot before the overnight run starts.

The system may stop safely at any time. Stopping is a valid outcome; silently
continuing with a broken assumption is not.

### Non-negotiable invariants

- No write to tracked project files in the user's main worktree. The only
  allowed main-tree writes are ignored run-state files under
  `.agents/tmp/v2/`; project targets are written only in the isolated worktree.
- No `git reset --hard`, `git clean`, or checkout in the main worktree.
- No model call without an accepted budget reservation.
- No phase transition without a verify result.
- No plan change after sign-off without a new sign-off.
- No automatic merge to the user's branch.
- No hardcoded model IDs in the execution path.
- No reviewer may approve its own generated code or tests.
- The runner must be able to stop and resume without duplicating writes.

---

## 1. Goals and non-goals

### 1.1 Goals

- Run a multi-phase plan for up to 10 hours without human supervision.
- Keep the user's repository and uncommitted work untouched.
- Enforce per-phase request, cost, token and wall-clock budgets.
- Produce a reviewable handoff branch/patch at the end.
- Preserve the existing v1 strengths: `safe_writer`, machine verify, append-only
  ledger, preflight, quota tracking.
- Make every phase reproducible from recorded evidence.

### 1.2 Non-goals for v2.1

- A full OS/container security sandbox. Git worktree is file isolation, not a
  security boundary against arbitrary code run by tests.
- Automatic merge into the user's branch.
- Multiple concurrent runs in the same repository.
- Supporting non-Git projects.
- Replacing v1 for interactive runs. v1 remains the default until v2.1 passes
  its acceptance gates.
- Fixed model assignments. Models are resolved from the live catalog.

> **Important:** "safe tuyệt đối" is not a valid claim. The correct claim is
> "bounded, observable, reversible risk". Tests can still perform OS-level
> actions; stronger isolation is a later container/VM milestone.

---

## 2. Baseline: what v2.1 reuses from v1

| v1 component | v2.1 role |
|---|---|
| `safe_writer.py` | Still the only writer; root is redirected to the isolated worktree. |
| `work_order.py` | Reused for per-subtask validation; extended with phase metadata. |
| `ledger.py` | Append-only evidence; extended with phase/budget/worktree events. |
| `preflight.py` | Per-phase model/auth/health gate; must fail closed. |
| `quota.py` | Actual usage source when available; fallback to conservative estimate. |
| `job.py` | Reference for approval/run flow; v2 gets a new runner, not a giant flag. |
| `dashboard_ui.html` | Adds sign-off, budget, worktree and handoff views. |
| `recipe_c.py` | Existing request caps inform the default budget model. |

Current v1 limits (16 requests, 600 seconds, sequential subtasks) are useful
safety defaults but are **not sufficient** for an 8–10 hour run. v2.1 must
introduce a real run engine rather than only raising those constants.

---

## 3. Roles and lifecycle

The original draft mixed planning and execution into one "7-agent" table. v2.1
separates them explicitly.

### 3.1 Planning Council — 3 seats

| Seat | Responsibility | Must not |
|---|---|---|
| Planner-Architect | Propose phases, dependencies, architecture, risks | Write project files |
| Planner-Pragmatist | Cut scope, challenge feasibility, estimate effort | Approve its own proposal |
| Planner-Verification | Define measurable acceptance criteria and verify commands | Weaken tests to make a plan pass |

The **Lead** facilitates the council and records the final decision, but is not
a fourth planning vote. The council produces exactly one `MASTER_PLAN.json`.

### 3.2 Execution Council — 7 seats

| Seat | Responsibility | Write access |
|---|---|---|
| Lead Orchestrator | Phase coordination, work orders, final apply | Only through `safe_writer` |
| Primary Coder | Propose implementation patch | No direct write |
| Security Reviewer | Injection, auth, secrets, unsafe commands, hard veto | Read-only |
| QA Reviewer | Test evidence, regression, acceptance | Read-only |
| Perf Reviewer | Performance and resource risk | Read-only |
| Docs Reviewer | Documentation/API consistency | Read-only |
| Clean Architect | Optional post-verify cleanup/typing/docs | No write; proposal only |

Rules:

- Clean Architect is **post-verify only** and disabled by default. Any cleanup
  triggers a full re-verify and a new checkpoint.
- Security is a hard veto for exploitable behavior, secret exposure, or a
  disallowed write. It is not overridable by Lead.
- QA Planner (planning) and QA Reviewer (execution) are separate contexts even
  if they use the same model.
- No reviewer may review code it wrote. No role may edit a test file to make a
  failing implementation pass.

### 3.3 Model selection

Do not hardcode model names in the spec, plan, or runner. Use role aliases:

```json
{
  "planning.architect": "architect",
  "planning.pragmatist": "pragmatist",
  "planning.verification": "verifier",
  "execution.lead": "lead",
  "execution.coder": "coder",
  "execution.security": "security",
  "execution.qa": "qa",
  "execution.perf": "perf",
  "execution.docs": "docs",
  "execution.clean": "clean"
}
```

Resolution rules:

1. Resolve aliases through `scout`/catalog at run start and again before each
   phase.
2. Record a `model_snapshot` hash in the sign-off.
3. Reviewers must be from a different model family than the active coder. If
   that cannot be satisfied, fail closed unless the user explicitly overrides
   it in the signed plan.
4. Every provider invocation, including fallback attempts, counts against
   budget.
5. No silent heuristic fallback. If a required role is unavailable, the phase
   pauses with `BLOCKED`.

---

## 4. Isolation model (P0)

### 4.1 Threat model

The isolation layer protects against:

- accidental writes to the user's working tree;
- destructive Git operations in the user's repository;
- a broken phase polluting a later phase;
- a failed run leaving partial files mixed with user changes;
- multiple runs racing on the same files.

It does **not** protect against a malicious test process that intentionally
escapes the worktree. OS-level sandboxing is a separate milestone.

### 4.2 Workspace layout

```text
main repo root (never written by v2.1)
  .agents/tmp/v2/
    runs/<run_id>/
      run_manifest.json
      budget.json
      plan.json
      events.ndjson
      checkpoints.json
      quarantine/
      handoff/
        report.md
        handoff.patch
  (worktree is outside the repo)

worktree root (default)
  SWARM_WORKTREE_ROOT/
    <run_id>/
```

Default worktree root:

- Windows: `%LOCALAPPDATA%\AdaptiveSwarm\worktrees`
- Linux/macOS: `~/.local/state/adaptive-swarm/worktrees`

The worktree must be outside the main repository. This avoids nested-worktree
cleanup mistakes and makes the path guard unambiguous. The path may be
overridden only by the user and must be checked before every destructive action.

### 4.3 Preflight for a run

All must pass before any model call:

- `git --version` is available.
- The project root is a Git worktree root.
- `git status --porcelain` on the main worktree is clean. If dirty, abort with
  instructions to commit or stash. v2.1 does not silently include dirty work.
- The base commit is recorded as `base_sha`.
- Free disk space is at least the configured minimum.
- The worktree path does not exist or is an empty directory.
- The path is not the main repo, not a parent/child replacement of it, and not a
  symlink.
- A single-run lock can be acquired.
- Required roles and verify commands pass preflight.

Optional future feature: `--snapshot-dirty` may create a non-destructive snapshot
commit. It is not part of the v2.1 P0 acceptance path.

### 4.4 Create the worktree

```bash
git -C <main_root> worktree add \
  -b swarm/<run_id> \
  <worktree_path> \
  <base_sha>
```

Record:

```json
{
  "mode": "git-worktree",
  "main_root": "<absolute main root>",
  "worktree_path": "<absolute worktree>",
  "branch": "swarm/<run_id>",
  "base_sha": "<sha>",
  "created_at": "<iso8601>"
}
```

If branch creation fails because the name exists, fail; never reuse an old
branch silently.

### 4.5 Worktree guard

Every operation that could write or reset must call:

```text
assert_isolated_worktree(main_root, worktree_path):
  realpath(worktree_path) is under realpath(SWARM_WORKTREE_ROOT)
  realpath(worktree_path) != realpath(main_root)
  git -C worktree_path rev-parse --show-toplevel == realpath(worktree_path)
  git -C main_root rev-parse --show-toplevel == realpath(main_root)
  worktree_path is not a symlink
```

Any failure raises `ISOLATION_GUARD_FAILED` and the run stops before a model
call. This guard is non-bypassable by plan or dashboard.

### 4.6 Write policy

- `safe_writer` always receives `root=worktree_path`.
- Target paths are relative, canonicalized, and must remain inside the
  worktree.
- Reject `..`, absolute paths, symlink escapes, `.git/`, and paths outside the
  plan's `allowed_paths`.
- Reject writes to `.agents/tmp/v2`, secrets, credentials, and model config
  unless explicitly listed as a target (normally forbidden).
- The Lead is the only caller allowed to invoke `safe_writer`.
- Reviewers and planners return proposals/findings only.

### 4.6.1 Worktree environment bootstrap

A fresh worktree contains tracked files only. The runner must not assume the
user's `.venv`, `node_modules`, `.env`, `config.json`, or local secrets exist.

- Never copy `.env`, credentials, auth files, or model config into a worktree.
- Machine config is read-only through `SWARM_CONFIG_PATH`; the verifier receives
  only the allowlisted environment variables, and logs redact secret values.
- Install dependencies from the project lockfile inside the worktree, or use an
  isolated environment under the run state root. Never mutate the user's venv.
- Ignored seed files are copied only when explicitly listed in the signed
  `seed_files`, with a SHA-256 and size limit. Secrets are never seedable.
- Verify commands run with `cwd=worktree`; absolute paths and path escapes are
  rejected.
- If the environment cannot be prepared, the run enters `BLOCKED` **before**
  any model call.

### 4.7 Checkpoints

After a phase is verified:

```bash
git -C <worktree> add -A
git -C <worktree> commit -m "swarm: <run_id> phase <phase_id> verified"
git -C <worktree> rev-parse HEAD
```

Store the commit SHA in `checkpoints.json` and update the phase state. Use refs,
not global tags:

```text
refs/swarm/<run_id>/<phase_id>
```

A checkpoint must include the verify result and the exact plan hash used for
that phase.

### 4.8 Failure and rollback

On a failed phase, the runner must:

1. Capture evidence **before** changing the worktree:
   - `git diff --binary <last_checkpoint>..HEAD`
   - `git status --porcelain`
   - untracked files
   - verify stdout/stderr
   - budget ledger
2. Write it to `runs/<run_id>/quarantine/<phase_id>/`.
3. Roll back **only inside the guarded worktree** to the last known-good
   checkpoint.
4. Run a checkpoint sanity check (`git rev-parse HEAD` equals recorded SHA).
5. Mark the run `QUARANTINED` or `ROLLED_BACK`.
6. Pause and notify. Never auto-merge and never continue to the next phase
   without a policy decision.

`git reset --hard` is permitted only after `assert_isolated_worktree` succeeds.
It is forbidden in the main worktree under all circumstances.

### 4.9 Handoff (default: no auto-merge)

At the end of a successful run:

- Keep the worktree and branch.
- Write `handoff/report.md` and `handoff/handoff.patch`.
- Record the final SHA, checkpoints, budget actuals and verification evidence.
- Set state to `HANDOFF_READY`; not `DONE` until the user accepts.
- The dashboard shows the diff summary and merge instructions.
- `DONE` means the run completed; it does not mean the user's branch changed.

Optional `auto_merge` is out of scope for v2.1. If added later, it requires a
separate explicit sign-off and a clean target branch.

### 4.10 Cleanup

- Do not delete worktrees on failure.
- Retain evidence for at least 7 days.
- Remove a worktree only after the user acknowledges the handoff or explicitly
  requests cleanup.
- Before removal, archive the report/patch/checkpoints.
- A stale lock is detected by PID + heartbeat; never assume a dead run based on
  elapsed time alone.

### 4.11 Isolation limitations

- Git worktree isolates files, not processes.
- Tests can still access network, environment variables and user-level paths if
  they deliberately do so.
- `system_bot` command guard and an environment allowlist are mandatory, but
  they are not a full sandbox.
- Container/VM isolation is a future option, not a v2.1 claim.

---

## 5. Budget model (P0)

### 5.1 Dimensions

Every run has four budgets:

1. **Requests** — number of provider invocations, including retries/fallbacks.
2. **Tokens** — input + output tokens, used for conservative cost estimation.
3. **Cost** — USD, only when a reliable price is known.
4. **Wall clock** — seconds from run start to stop.

Requests and wall clock are the primary hard gates. Cost is a second gate when
pricing is available. If pricing is unknown, paid models are blocked unless the
user explicitly allows them with a per-call ceiling.

### 5.2 Default envelope

Values are defaults for the sign-off screen, not promises:

```json
{
  "global": {
    "max_wall_seconds": 36000,
    "max_requests": 120,
    "max_cost_usd": 3.50,
    "emergency_reserve_pct": 10
  },
  "planning": {
    "max_requests": 12,
    "max_cost_usd": 0.25,
    "max_wall_seconds": 900
  },
  "per_phase": {
    "max_requests": 24,
    "max_cost_usd": 0.70,
    "max_wall_seconds": 7200,
    "repair_reserve_pct": 20
  },
  "limits": {
    "max_repair_attempts_per_phase": 3,
    "max_consecutive_no_progress": 2,
    "max_phases": 6
  }
}
```

The signed plan must allocate no more than the global cap. Example for 4 phases:

| Bucket | Requests | USD | Wall time |
|---|---:|---:|---:|
| Planning | 12 | 0.25 | 15m |
| Phase 1 | 24 | 0.70 | 2h |
| Phase 2 | 24 | 0.70 | 2h |
| Phase 3 | 24 | 0.70 | 2h |
| Phase 4 | 24 | 0.70 | 2h |
| Emergency reserve | 12 | 0.45 | remaining |
| **Total cap** | **120** | **3.50** | **10h** |

The planner may allocate differently, but the validator rejects any plan where
the sum of phase budgets exceeds the signed global envelope minus the emergency
reserve. For six phases, the per-phase request cap must be reduced below 24;
the example is for four phases and is not an invariant. Unused phase budget
returns to the global pool only after that phase is committed. It may never
raise a hard cap.

### 5.3 Reservation protocol

All model calls go through `BudgetedRunner`, never directly through workers.

```text
reserve(phase, role, model, estimate):
  lock budget ledger
  check global remaining
  check phase remaining
  check repair reserve
  check wall clock > call timeout + verify reserve
  if not enough: raise BudgetStop before provider call
  append "reserve" event
  return reservation_id

settle(reservation_id, actual):
  lock budget ledger
  append "settle" event with actual requests/tokens/cost
  release unused reservation
  if actual > reserved:
     append "overrun" event
     stop run
```

Required behavior:

- A reservation is created **before** the provider call.
- A failed provider call that still consumed a request is settled.
- Fallback attempts are separate provider invocations and count.
- Preflight checks that make no model call do not consume requests.
- If actual cost cannot be read, use a conservative token-based estimate and
  mark `cost_confidence: low`.
- If the remaining wall clock is less than the call timeout plus verify time,
  do not start the call.

### 5.4 Per-phase accounting

Each phase has:

```json
{
  "phase_id": "P2",
  "allocated": {"requests": 24, "cost_usd": 0.70, "wall_seconds": 7200},
  "reserved": {"requests": 0, "cost_usd": 0.0},
  "actual": {"requests": 0, "cost_usd": 0.0, "tokens": 0, "wall_seconds": 0},
  "repair_used": 0,
  "status": "running"
}
```

The dashboard must show **actual / allocated / remaining** per phase. A phase
cannot borrow from a future phase or from the emergency reserve.

### 5.5 Stop conditions

The runner stops before the next call when any of these is true:

- global or phase request cap reached;
- global or phase cost cap reached;
- wall clock expired or remaining time is insufficient;
- `max_repair_attempts_per_phase` reached;
- `max_consecutive_no_progress` reached;
- security veto;
- plan hash mismatch;
- model/auth/preflight failure that cannot be safely retried;
- user stop / pause;
- isolation guard failure;
- test-integrity failure.

Stop behavior:

- `PAUSED`: user can resume; no new model call yet.
- `BLOCKED`: external condition (auth/quota/catalog); resume after fix.
- `QUARANTINED`: evidence captured; user must decide.
- `STOPPED`: user-requested stop.
- `FAILED`: unrecoverable; evidence preserved.

### 5.6 No-progress detector

The runner must stop early when it detects a loop, not only when the budget is
exhausted:

- same normalized verify error signature twice in a row;
- same target file and same diff repeated;
- empty diff with a failed verify;
- same repair plan repeated;
- two consecutive reviews with no new objection/evidence.

Counters are per phase and are recorded in the ledger.

### 5.7 Budget changes and sign-off

- Budget is read-only during a run.
- The dashboard may lower the cap or stop; it may not raise it.
- Any increase requires a new sign-off with a new budget hash and a new plan
  hash. The runner must pause before accepting it.
- The sign-off screen must show:
  - plan hash;
  - total and per-phase budgets;
  - worst-case reserve;
  - model snapshot;
  - allowed paths;
  - worktree path and branch;
  - stop/rollback policy;
  - handoff policy.

---

## 6. State machine

### 6.1 States

```text
CREATED
  -> PRECHECKED
  -> PLANNING
  -> AWAITING_SIGNOFF
  -> PROVISIONING
  -> PHASE_EXECUTING
  -> PHASE_VERIFYING
  -> PHASE_COMMITTED
  -> ... next phase ...
  -> HANDOFF_READY
  -> DONE

Any active state -> PAUSED | BLOCKED | QUARANTINED | STOPPED | FAILED
```

### 6.2 Transition table

| From | Event | Guard | To |
|---|---|---|---|
| CREATED | preflight_ok | all preflight checks | PRECHECKED |
| PRECHECKED | start_planning | budget available | PLANNING |
| PLANNING | plan_valid | schema/hash/budget valid | AWAITING_SIGNOFF |
| AWAITING_SIGNOFF | sign_off | hash matches | PROVISIONING |
| PROVISIONING | worktree_ready | isolation guard | PHASE_EXECUTING |
| PHASE_EXECUTING | verify_start | patch written | PHASE_VERIFYING |
| PHASE_VERIFYING | verify_pass | exit 0 + evidence | PHASE_COMMITTED |
| PHASE_COMMITTED | next_phase | budget remains | PHASE_EXECUTING |
| PHASE_COMMITTED | all_phases_done | no next phase | HANDOFF_READY |
| HANDOFF_READY | user_accepts | user action | DONE |
| any active | pause/stop | user or policy | PAUSED/STOPPED |
| PHASE_VERIFYING | verify_fail | retries > 0 | PHASE_EXECUTING (repair) |
| PHASE_VERIFYING | verify_fail | retries exhausted | QUARANTINED |
| PHASE_VERIFYING | isolation_guard_failed | — | FAILED |
| PHASE_VERIFYING | budget_stop | — | PAUSED/BLOCKED |
| PLANNING | plan_changed | hash mismatch | AWAITING_SIGNOFF |

Every transition must be written to the run manifest **before** the action that
follows it. A crash between the manifest write and the action must be recoverable
from the manifest + ledger.

---

## 7. Planning protocol

### 7.1 Inputs

- User goal.
- Project tree and allowed paths.
- Existing tests and verify commands.
- Model catalog snapshot.
- Global budget envelope.
- Isolation/worktree policy.

### 7.2 Rounds

- **Round 0:** Lead normalizes the goal and constraints; no plan yet.
- **Round 1:** Three planners independently produce structured proposals.
- **Round 2:** Each planner critiques the others; maximum one rebuttal.
- **Convergence:** Lead merges the strongest proposal with Pragmatist
  constraints and Verification acceptance criteria. If there is no agreement,
  Lead selects one and records the rationale. A plan does not need unanimous
  consensus; it needs a valid signed plan.

Hard limits: max 2 debate rounds, max 6 phases, max 12 work orders per phase,
max output tokens per planner call.

### 7.3 Plan validation

Before sign-off, the validator must reject:

- missing `verify_command` or `acceptance_criteria`;
- a verify command that `system_bot.check_command` rejects;
- a dependency cycle;
- a target path outside `allowed_paths`;
- writes to `.git`, secrets, or the state root;
- a per-phase budget exceeding the global envelope;
- a plan without rollback/checkpoint policy;
- a test/verify command that is agent-modifiable without re-signoff;
- a phase with no deterministic success signal.

### 7.4 Plan hash and changes

`plan_hash = sha256(canonical_json(plan))`.

The sign-off record stores:

```json
{
  "plan_hash": "sha256:...",
  "budget_hash": "sha256:...",
  "model_snapshot_hash": "sha256:...",
  "approved_by": "local-user",
  "approved_at": "<iso8601>",
  "nonce": "<random>"
}
```

Any change to the plan, budget, model snapshot, allowed paths, or isolation
policy invalidates the signature. The run must return to `AWAITING_SIGNOFF`.
There is no "minor plan edit" exception.

---

## 8. Execution protocol

### 8.1 Per phase

1. Preflight worktree, model snapshot, disk, budget, and verify command.
2. Lead creates phase work orders from the signed plan.
3. Coder proposes a patch (no direct write).
4. Lead applies the patch through `safe_writer(root=worktree)`.
5. Reviewers inspect the diff concurrently (read-only).
6. Security veto stops the phase immediately.
7. QA runs acceptance/verify evidence; hidden checks run outside agent control.
8. Deterministic verify must exit `0`.
9. On failure: repair loop, maximum 3 attempts, no-progress detector.
10. On success: checkpoint commit, settle budget, next phase.
11. Cleaner, if enabled, proposes cleanup only after verify; re-verify after any
    cleanup and checkpoint again.

### 8.2 Test integrity

- Acceptance criteria and verify command are part of the signed plan.
- Test files are write-protected unless explicitly listed in `allowed_paths`;
  changing a test requires a new sign-off.
- Test command cannot be replaced by the Coder or Lead mid-phase.
- Hidden checks (maintained outside the agent's write scope) are preferred for
  critical behavior.
- Diff allowlist + secret scan + forbidden-path scan run before verify.
- A passing test suite is evidence, not proof of correctness. The report must
  show what was tested and what was not.

### 8.3 Reviewer outputs

Reviewers return structured findings:

```json
{
  "role": "security",
  "verdict": "approve|reject|abstain",
  "severity": "blocker|high|medium|low",
  "evidence": ["..."],
  "required_change": "optional",
  "model": "resolved-model-id"
}
```

Security `reject` with `blocker` is a hard stop. QA/Perf/Docs rejects trigger a
repair attempt or a pause, depending on severity. No auto-approve.

### 8.4 Repair

- Maximum 3 attempts per phase.
- Each attempt must include a short repair plan and a new diff.
- The same error signature twice triggers no-progress stop.
- Repairs consume the same phase budget and the repair reserve.
- Exhaustion: quarantine, rollback to last checkpoint, pause, notify.

---

## 9. Data schemas

### 9.1 `run_manifest.json`

```json
{
  "schema_version": "2.1",
  "run_id": "run_20261101T...",
  "goal": "...",
  "status": "PHASE_EXECUTING",
  "created_at": "2026-11-01T...",
  "updated_at": "2026-11-01T...",
  "base_sha": "...",
  "plan_hash": "sha256:...",
  "budget_hash": "sha256:...",
  "model_snapshot_hash": "sha256:...",
  "workspace": {
    "mode": "git-worktree",
    "main_root": "...",
    "worktree_path": "...",
    "branch": "swarm/<run_id>",
    "base_sha": "..."
  },
  "current_phase": "P2",
  "checkpoints": [
    {"phase_id": "P1", "sha": "...", "verified_at": "..."}
  ],
  "last_error": null
}
```

### 9.2 `plan.json`

```json
{
  "schema_version": "2.1",
  "run_id": "...",
  "goal": "...",
  "base_sha": "...",
  "budget": {
    "global": {"requests": 120, "cost_usd": 3.5, "wall_seconds": 36000}
  },
  "environment": {
    "env_allow": ["PATH", "PYTHONPATH", "SWARM_CONFIG_PATH"],
    "network_for_verify": false,
    "seed_files": []
  },
  "phases": [
    {
      "id": "P1",
      "title": "Foundation",
      "depends_on": [],
      "allowed_paths": ["src/app.py", "tests/test_app.py"],
      "forbidden_paths": [".git", ".env", ".agents/tmp"],
      "budget": {"requests": 24, "cost_usd": 0.7, "wall_seconds": 7200},
      "work_orders": [
        {
          "id": "S1",
          "objective": "...",
          "acceptance_criteria": "...",
          "verify_command": "python tests/test_app.py",
          "target_files": ["src/app.py"],
          "level": 1,
          "max_rounds": 1
        }
      ],
      "rollback": {"mode": "last_checkpoint"},
      "cleanup": "disabled"
    }
  ]
}
```

### 9.3 Budget ledger event

```json
{
  "ts": "2026-11-01T...",
  "run_id": "...",
  "phase_id": "P1",
  "kind": "reserve|settle|release|overrun|stop",
  "reservation_id": "...",
  "role": "coder",
  "model": "resolved-model-id",
  "requests": 1,
  "tokens": 1234,
  "cost_usd": 0.02,
  "cost_confidence": "high|low",
  "reason": "..."
}
```

### 9.4 Sign-off record

```json
{
  "plan_hash": "sha256:...",
  "budget_hash": "sha256:...",
  "model_snapshot_hash": "sha256:...",
  "approved_by": "local-user",
  "approved_at": "...",
  "nonce": "...",
  "allow_dirty_snapshot": false,
  "auto_merge": false
}
```

### 9.5 Checkpoint and quarantine

Checkpoint:

```json
{"phase_id": "P1", "sha": "...", "tree": "...", "verified_at": "...", "verify_exit": 0}
```

Quarantine manifest:

```json
{
  "phase_id": "P2",
  "reason": "verify_failed",
  "patch": "quarantine/P2/patch.diff",
  "untracked": ["..."],
  "verify": {"exit_code": 1, "stderr_tail": "..."},
  "budget": {"requests": 17, "cost_usd": 0.42},
  "created_at": "..."
}
```

### 9.6 Event extensions

Existing ledger events gain optional fields:

```text
run_id, phase_id, role, model, worktree_sha, budget_request, budget_cost,
budget_remaining, verdict, evidence, quarantine_path
```

v1 events remain readable; v2 events are versioned (`schema_version: "2.1"`).

---

## 10. Dashboard and API

### 10.1 Sign-off screen

Must show:

- goal and plan hash;
- phase table with per-phase budget and dependencies;
- allowed/forbidden paths;
- worktree path and branch;
- model snapshot;
- worst-case stop conditions;
- handoff policy (`no auto-merge`);
- explicit `Approve exact plan & run` action.

### 10.2 Run screen

Must show:

- current state and phase;
- budget: allocated / reserved / actual / remaining, per phase and global;
- worktree path, branch, base/checkpoint SHA;
- verify evidence and quarantine link;
- pause/stop controls (no budget increase);
- next scheduled action and stop reason.

### 10.3 Handoff screen

- diff summary;
- patch download;
- checkpoint list;
- report and unresolved risks;
- merge instructions;
- explicit user acceptance.

### 10.4 API security

- Bind to `127.0.0.1` by default.
- Require a session token for mutating endpoints.
- Validate all budget and state changes server-side; never trust the browser.
- Do not expose secret values.
- Disable config changes while a run is active.
- Audit every control action (pause, stop, accept, cleanup).

---

## 11. Security and safety controls

- `system_bot` command guard for every verify/run command.
- Environment allowlist; no secrets in prompts or logs.
- Diff secret scan before checkpoint.
- Path/symlink escape checks.
- No network by default for verify; explicit allowlist when needed.
- Resource limits: per-call timeout, verify timeout, max output tokens, max diff
  size, max files.
- OS-level sandbox is deferred; do not claim it exists.
- Notifications (Discord/Telegram) are optional and must not contain secrets.

---

## 12. Failure taxonomy and recovery

| Code | Meaning | Default action |
|---|---|---|
| `PREFLIGHT_FAIL` | Model/auth/git/disk failure | BLOCKED |
| `BUDGET_EXHAUSTED` | Global/phase cap reached | PAUSED |
| `TIME_EXHAUSTED` | Wall clock cap | PAUSED |
| `NO_PROGRESS` | Repeated error/diff | QUARANTINED |
| `VERIFY_FAIL` | Deterministic test failed | repair → quarantine |
| `SECURITY_VETO` | Blocker finding | QUARANTINED |
| `PLAN_MISMATCH` | Plan changed after sign-off | AWAITING_SIGNOFF |
| `ISOLATION_GUARD_FAILED` | Worktree path unsafe | FAILED |
| `TEST_TAMPER` | Test/verify changed | QUARANTINED |
| `AUTH_EXPIRED` | OpenCode auth lost | BLOCKED |
| `QUOTA` | Provider 429/quota | fallback or BLOCKED |
| `CRASH` | Runner died | resume from manifest |
| `USER_STOP` | User requested stop | STOPPED |
| `EXTERNAL_EDIT` | User changed main tree | ignored by isolation; report |

Recovery rule: when uncertain, stop and preserve evidence. Never "fix" state by
deleting the worktree or resetting the main repo.

---

## 13. Acceptance tests (P0 gates)

### 13.1 Isolation

- [ ] Dirty main worktree: run refuses to start; file hashes unchanged.
- [ ] User edits a file in main root during a run: worktree unaffected; main file unchanged.
- [ ] `assert_isolated_worktree` rejects a path outside `SWARM_WORKTREE_ROOT`.
- [ ] `assert_isolated_worktree` rejects a symlink worktree.
- [ ] A reset attempt against the main root is blocked and logged.
- [ ] Crash during a phase leaves a recoverable manifest; resume does not duplicate writes.
- [ ] Handoff leaves the main branch unchanged; patch contains all changes.
- [ ] Worktree cleanup refuses while quarantine exists.

### 13.2 Budget

- [ ] A mock call that would exceed the per-phase request cap is not executed.
- [ ] A fallback attempt increments the request count.
- [ ] Actual cost over reserve triggers stop, not silent overrun.
- [ ] Unknown paid price is blocked unless explicitly allowed.
- [ ] Unused phase budget returns only after phase commit.
- [ ] Budget cannot be raised from the dashboard during a run.
- [ ] Wall-clock reserve prevents starting a call that cannot finish before deadline.
- [ ] No-progress detector stops after 2 identical errors.

### 13.3 State and planning

- [ ] Plan hash mismatch returns to `AWAITING_SIGNOFF`.
- [ ] Plan with a dependency cycle is rejected before model execution.
- [ ] Plan with a missing verify command is rejected.
- [ ] Verify command rejected by `system_bot` is rejected before sign-off.
- [ ] Phase > global budget is rejected.
- [ ] Crash before/after transition is recoverable and idempotent.

### 13.4 Execution and tests

- [ ] Test file modification without re-signoff is blocked.
- [ ] Secret in diff blocks checkpoint.
- [ ] Security blocker stops the phase.
- [ ] Verify exit 0 is required for checkpoint.
- [ ] Failed phase quarantines a patch and rolls back to the last checkpoint.
- [ ] Cleaner change triggers full re-verify.
- [ ] Handoff report includes actual budget, checkpoints, and unresolved risks.

### 13.5 Soak

- [ ] Synthetic 8–10 hour run with fake clock and mock models meets all caps.
- [ ] Real pilot limited to one phase and one free model before a multi-phase run.
- [ ] No unbounded process, disk, or log growth in the pilot.

---

## 14. Implementation roadmap

### M1 — Isolation, state, budget (P0)

- new v2 runner and run manifest;
- worktree create/guard/checkpoint/quarantine/handoff;
- budget controller and ledger events;
- state machine;
- isolation/budget tests;
- v1 remains default via feature flag.

**Exit:** all 13.1, 13.2, 13.3 isolation/state tests pass with mock models.

### M2 — Planning council and sign-off

- 3-planner structured protocol;
- plan schema and hash;
- sign-off screen and API;
- validation and reapproval rules.

**Exit:** plan validation and hash tests pass; no model call before sign-off.

### M3 — Execution council and test integrity

- 7 execution seats (Clean optional);
- cross-family enforcement;
- security veto and reviewer evidence;
- test/verify integrity checks;
- repair budget and no-progress.

**Exit:** reviewer/verify/security tests pass in a worktree with mock providers.

### M4 — Dashboard and handoff

- budget/worktree/phase panels;
- pause/stop/accept controls;
- patch/report handoff;
- notifications (optional).

**Exit:** UI cannot raise caps; handoff leaves main untouched.

### M5 — Pilot and soak

- one real phase with a free model;
- 8–10 hour synthetic soak;
- fault injection: crash, 429, timeout, disk full, test tamper.

**Exit:** no data loss, no main-tree write, all caps enforced, handoff reviewable.

### M1/M2 implementation map (current)

```text
.agents/v2/isolation.py   guarded worktree, checkpoint, quarantine, handoff
.agents/v2/budget.py      reserve/settle ledger, per-phase caps, wall clock
.agents/v2/state.py       manifest and state transitions
.agents/v2/plan.py        master plan schema, DAG/path/budget validation, hashes
.agents/v2/planning.py    3-seat council, critique round, Lead synthesis
.agents/v2/signoff.py     immutable signoff record and reapproval checks
.agents/v2/execution.py   apply, test integrity, cross-family review, veto/repair
.agents/v2/adapters.py    OpenCode coder/reviewer bridge for live M3.5 runs
.agents/v2/web.py         /v2 sign-off/run page + runs/signoff/control endpoints
.agents/v2/runner.py      prepare/plan/signoff/checkpoint/quarantine/handoff/control
.agents/v2/run.py         prepare/plan/signoff/execute/status/handoff CLI
.agents/tests/test_v2_m1.py  isolation/budget/state tests
.agents/tests/test_v2_m2.py  plan/council/signoff/web/control tests
.agents/tests/test_v2_m3.py  execution/review/veto/integrity/budget/adapter tests
```

M1-M4 tests use temporary Git repositories and deterministic fake
planners/coders/reviewers; no model is called. v1 remains the default execution
path. Current acceptance: M1 `43/43`, M2 `38/38`, M3 `24/24`. The dashboard
exposes `/v2` and `/api/v2/runs`, `/api/v2/runs/<id>`,
`/api/v2/runs/<id>/plan`, `/api/v2/signoff`, `/api/v2/control`; approval and
control use the run-local token. M3 uses `safe_writer.Transaction`, per-phase
budget reservations, test-file hashing, cross-family checks and a security hard
veto. M3.5 adds an OpenCode worker bridge; M4 adds pause/resume/stop/accept and
handoff status to the local page.

---

## 15. Open decisions

1. Require clean main worktree, or support dirty snapshots in v2.1? **Default:
   require clean.**
2. Keep 7 execution seats including Clean, or 6 with Clean disabled? **Default:
   7 seats defined, Clean disabled unless the plan enables it.**
3. Worktree root location on Windows? **Default: `%LOCALAPPDATA%`; if that path
   would be inside the repo (home-directory layout), use the sibling
   `<repo>.swarm-worktrees`; never inside the repo.**
4. Auto-merge? **Default: no.**
5. Exact default budget values? **Use the envelope in §5.2; sign-off may lower,
   not raise hard caps.**
6. OS-level sandbox (container/VM)? **Deferred; explicitly out of v2.1 scope.**

---

## 16. Appendix: operator commands

Create, plan and sign off:

```bash
python .agents/v2/run.py prepare --root . --goal "<goal>" --phase P1
python .agents/v2/run.py plan --run-id <id> --plan plan.json
python .agents/v2/run.py signoff --run-id <id> --approved-by "local-user"
python .agents/v2/run.py assert-signoff --run-id <id>
python .agents/v2/run.py execute --run-id <id> --free
```

Inspect and hand off:

```bash
python .agents/v2/run.py status --run-id <id>
python .agents/v2/run.py handoff --run-id <id>
git -C <worktree> log --oneline <base_sha>..HEAD
git diff <base_sha>..HEAD > handoff.patch
# review, then merge the swarm branch manually
```

M2 is plan/sign-off only; the `run`/execution command is intentionally absent
until M3. These commands are illustrative; the final CLI names may be adjusted,
but the safety semantics above are mandatory.
