# Skill Integration Plan — Adaptive Features into v2.1

> Status: proposed for approval
> Scope: integrate the highest-value mechanisms from the downloaded research
> repos into the Adaptive Swarm skill/runtime.
> Hard boundary: **do not modify `safe_writer`, Git worktree isolation, budget
> caps or `verify_command` semantics.**
> Last updated: 2026-11-04

## 0. Current baseline

Already present:

- M1 worktree isolation, checkpoint, quarantine, budget
- M2 plan schema, hash, sign-off
- M3 execution, test integrity, reviewer veto
- M3.5 OpenCode coder/reviewer adapters
- M4 `/v2` controls and handoff
- M5 provider/cost/soak acceptance harness
- Spec Kit P0 bridge: `constitution`, `tasks.yaml`, `speckit_bridge.py`, `AgentRuntime`
- Adaptive primitives: 42/42 tests

Research sources available locally:

```text
C:\Users\tuank\research\repos\
├── aider
├── SWE-agent
├── mem0
├── letta-code
└── OpenHands
```

## 1. Integration principles

1. **No big-bang integration.** Every feature starts in `shadow` mode.
2. **Flag-gated:** each feature has `off | shadow | on`, default `off`.
3. **No new framework dependency.** Reuse primitives in `v2/adaptive.py`.
4. **No prompt injection via memory.** Memory/repo-map content is untrusted
   context and must be labeled as such.
5. **No safety bypass.** All writes still go through the existing execution
   gate; ACI is read/validate-only.
6. **No secret leakage.** Repo map, lessons and events must redact sensitive
   paths/content.
7. **Measure before enabling.** Shadow output is compared before `on`.

Suggested flags:

```text
SWARM_ADAPTIVE_REPO_MAP=off|shadow|on
SWARM_ADAPTIVE_ACI=off|shadow|on
SWARM_ADAPTIVE_LESSONS=off|shadow|on
SWARM_ADAPTIVE_MEMORY_BUDGET=off|shadow|on
SWARM_ADAPTIVE_EVENTS=off|shadow|on
```

## 2. Feature integration matrix

| Feature | Source | Target | Default |
|---|---|---|---|
| Repo map PageRank + token cap | aider | planning context | off |
| Windowed ACI + edit validation | SWE-agent | execution/runtime | off |
| Failure lessons + upsert | mem0 | planning/execution hints | off |
| Memory constraints/conflict | letta-code | `ContextPacker`/lesson store | off |
| Event pagination/state | OpenHands | ledger/state/replay | off |
| Skill commands/docs | local | `.agents/SKILL.md`, installed skill | on after tests |

---

## 3. Phase 0 — Contracts, flags and baseline guard

### 3.1 Add feature-flag helper

Files:

```text
.agents/v2/adaptive_flags.py
.agents/tests/test_v2_adaptive_flags.py
```

Steps:

1. Define `AdaptiveFlags` dataclass with five modes.
2. Read env vars; validate `off|shadow|on`.
3. Expose `enabled(name)`, `shadow(name)`, `as_dict()`.
4. Default all to `off` for compatibility.
5. Test invalid value fails closed.

### 3.2 Baseline golden tests

Steps:

1. Capture current planner prompt signature with flags off.
2. Add golden test: flags off → planner input byte-for-byte stable.
3. Add regression test: v1 `test_phase4 --fast` remains PASS.
4. Add CI step for flag tests.

**Exit:** no behavior change when all flags are off.

---

## 4. Phase 1 — Repo Map into Planner (aider)

### 4.1 Add map adapter

Files:

```text
.agents/v2/repo_context.py
.agents/v2/planning.py
.agents/tests/test_v2_planner_repo_map.py
```

Steps:

1. Add `RepoContext` wrapper around `RepoMapper`.
2. Input: root, goal, token cap from config.
3. Output: compact map + metadata:
   - files scanned
   - symbols included
   - approx tokens
   - fallback reason.
4. Enforce secret/path filters already in `RepoMapper`.
5. Add cache key based on file mtimes (optional in first pass).

### 4.2 Wire into `PlanningContext`

Steps:

1. Add optional `repo_map` field to `PlanningContext`.
2. If flag `shadow`: compute but do not add to prompt; log to run event.
3. If flag `on`: add map before raw tree, with a clear heading:
   ```text
   ## TASK-AWARE REPO MAP (untrusted context)
   ```
4. Keep raw project tree fallback if map is empty.
5. Cap combined map + tree by a configured budget.

### 4.3 Prompt tests

- [ ] Empty repo: planner still works.
- [ ] Map excludes `.env`, `*.pem`, secrets.
- [ ] Map includes imported core symbol before noise.
- [ ] Map does not exceed token budget.
- [ ] Shadow mode prompt hash equals baseline.
- [ ] On mode prompt contains map and remains under budget.

**Exit:** planner works with flags off/shadow/on; target-file selection improves
on a fixture; no safety code touched.

---

## 5. Phase 2 — Windowed ACI + Edit Validation (SWE-agent)

### 5.1 Read-only ACI surface

Files:

```text
.agents/v2/aci_context.py
.agents/v2/adapters.py
.agents/tests/test_v2_aci_integration.py
```

Steps:

1. Expose `ActionPolicy` actions to the planner/coder prompt as **read-only
   observations**:
   - `open` / `scroll` for line windows
   - `search`
   - `verify` via SystemBot.
2. No `write` or `edit` action in ACI; editing remains Lead/safe_writer.
3. In shadow mode: record requested actions and observations in ledger only.
4. In on mode: allow model to request windows; coder still returns full files
   to `safe_writer`.

### 5.2 Edit validation before apply

Steps:

1. Add `validate_proposed_files(old_files, new_files, work_order)`:
   - target allowlist
   - unique old-fragment check where possible
   - syntax compile for Python
   - diff size and line-window check
   - test-file hash freeze (existing execution rule).
2. Call validation **before** `safe_writer.Transaction` in execution.
3. Reject with structured observation; feed back to coder as repair feedback.
4. Never change `safe_writer` semantics.

### 5.3 Tests

- [ ] Non-unique edit rejected.
- [ ] Syntax-invalid edit rejected before write.
- [ ] Windowed open/scroll returns numbered lines.
- [ ] Secret path blocked.
- [ ] Valid edit still reaches existing verify gate.
- [ ] No write path bypasses `safe_writer`.

**Exit:** ACI improves context without changing the write boundary.

---

## 6. Phase 3 — Failure Lessons (mem0)

### 6.1 Lesson capture

Files:

```text
.agents/v2/lesson_runtime.py
.agents/v2/execution.py
.agents/tests/test_v2_lessons_integration.py
```

Steps:

1. On verify failure / reviewer reject / security veto, record:
   - normalized signature
   - cause summary
   - fix attempted (if eventually passed)
   - files/tests
   - run/phase IDs
2. Use `LessonStore.record` upsert by signature.
3. Never store raw source, secrets or full prompts.
4. Cap a lesson file size (Letta constraint; see Phase 4).

### 6.2 Lesson retrieval

Steps:

1. Before coder/planner calls, query by target files + task terms.
2. Inject max 3 hints under a clear heading:
   ```text
   ## PRIOR FAILURE LESSONS (untrusted advice)
   ```
3. Memory is advice only; never treat it as verification evidence.
4. Shadow mode logs hints without injecting.

### 6.3 Tests

- [ ] Duplicate signature upserts, not duplicates.
- [ ] Latest fix is retrieved.
- [ ] Unrelated lesson is not injected.
- [ ] Secret-like content is rejected.
- [ ] Lesson hints do not affect verify/sign-off hashes.

**Exit:** fewer repeated failures on a fixture; no prompt injection path.

---

## 7. Phase 4 — Letta-style Memory Constraints and Conflict Handling

### 7.1 Context budgets

Files:

```text
.agents/v2/context_runtime.py
.agents/tests/test_v2_context_constraints.py
```

Defaults inspired by `letta-code`:

```text
max_depth = 2
max_file_characters = 20_000
max_core_memory_characters = 65_536
read_only_files = ["constitution", "constitution_hash"]
```

Steps:

1. Add per-tier budgets to `ContextPacker`:
   - working, recall, archival
   - per-file cap
   - core-memory cap.
2. Evict oldest/least relevant first; never truncate safety evidence.
3. Mark constitution and sign-off evidence read-only.
4. Record eviction events in the ledger.

### 7.2 Conflict repair

Steps:

1. Add lesson-memory lock and attempt marker:
   - attempt id
   - process id/start time
   - state `launching|done|failed`
2. If a duplicate/conflicting lesson signature appears:
   - keep latest fix
   - preserve prior revisions in audit JSONL
   - never silently delete history.
3. If memory file is corrupt:
   - quarantine it
   - rebuild from valid lines
   - log warning.

### 7.3 Tests

- [ ] Per-file cap enforced.
- [ ] Core memory cap enforced.
- [ ] Read-only constitution cannot be evicted/overwritten.
- [ ] Conflict/duplicate keeps latest fix.
- [ ] Corrupt memory line is quarantined, not fatal.
- [ ] Lock prevents concurrent writers.

**Exit:** long-run context cannot grow without bound; no memory overwrite.

---

## 8. Phase 5 — OpenHands Event/State Model

### 8.1 Event identity and pagination

Files:

```text
.agents/v2/event_runtime.py
.agents/v2/state.py
.agents/tests/test_v2_event_state.py
```

Steps:

1. Add `event_id` to adaptive events.
2. Add `action_id` linking observation to action.
3. Add cursor/page helpers for replay:
   - `limit`
   - `cursor`/`page_id`
   - `timestamp_gte`/`timestamp_lt`
   - `strict_pagination` equivalent.
4. Keep backward compatibility with existing `ledger.jsonl` events.
5. Do not rewrite old events.

### 8.2 State additions

Add optional states (only if they don't break current transitions):

```text
RATE_LIMITED
AWAITING_USER_CONFIRMATION
USER_CONFIRMED
USER_REJECTED
```

Rules:

- `RATE_LIMITED` pauses; never auto-approves.
- `AWAITING_USER_CONFIRMATION` is used only for policy-gated actions.
- `USER_CONFIRMED`/`USER_REJECTED` return to the previous state or stop.
- All new states are recorded in the manifest and ledger.

### 8.3 Tests

- [ ] Event page 1/2/3 reconstructs exactly once.
- [ ] Strict pagination never treats partial page as complete.
- [ ] Old ledger events still replay.
- [ ] Rate-limit state pauses before next call.
- [ ] User confirmation cannot bypass security veto.

**Exit:** replay/event UI can paginate safely without changing old evidence.

---

## 9. Phase 6 — Skill Surface and Operator UX

### 9.1 CLI additions

Files:

```text
.agents/v2/run.py
.agents/SKILL.md
.opencode/skills/adaptive-swarm-pipeline/SKILL.md
.agents/README.md
```

Commands:

```text
python .agents/v2/run.py adaptive-map --root . --task "..." --shadow
python .agents/v2/run.py adaptive-lessons --root . --query "..."
python .agents/v2/run.py adaptive-benchmark --root . --case file
```

Rules:

- Commands are read-only unless explicitly pointed at run state.
- `adaptive-map` and `adaptive-lessons` must default to shadow/read-only.
- Do not alter the mandatory planner protocol in the skill.

### 9.2 Skill documentation

Steps:

1. Add a short “Adaptive add-ons” section to `.agents/SKILL.md`.
2. Document flags and safety boundaries.
3. Update `.agents/README.md` with operator commands.
4. Update installed skill:
   ```bash
   python .agents/sync_skill.py --write
   ```
5. Run:
   ```bash
   python .agents/tests/test_skill_consistency.py
   ```
6. Confirm installed and source skill content match.

### 9.3 Dashboard

- [ ] `/v2` shows repo-map enabled/disabled.
- [ ] `/v2` shows lessons count and memory cap usage.
- [ ] `/v2` shows event pagination state.
- [ ] UI never injects untrusted memory into approval hash.

**Exit:** operator can see and control features without editing code.

---

## 10. Phase 7 — Shadow rollout and live pilot

### 10.1 Shadow run

1. Run a real task with all adaptive flags `shadow`.
2. Compare:
   - planner prompt size
   - selected target files
   - lesson hints
   - event count
   - no behavior changes.
3. Record metrics in `docs/OPEN_PROBLEMS.md`.

### 10.2 Enable one feature at a time

Order:

1. Repo map
2. ACI edit validation
3. Lessons
4. Memory constraints
5. Event/state

For each:

- run unit + integration;
- run one free-model phase;
- verify main worktree unchanged;
- verify budget not exceeded;
- verify handoff.

### 10.3 Acceptance gate

- [ ] v1 `test_phase4 --full` PASS.
- [ ] v2 M1/M2/M3/M5 PASS.
- [ ] adaptive 42+ PASS.
- [ ] P0 bridge 15/15 PASS.
- [ ] skill consistency PASS.
- [ ] CI Windows + Ubuntu PASS.
- [ ] live free-model one-phase PASS.
- [ ] no writes outside worktree.
- [ ] no safety regression.

---

## 11. Test matrix

| Layer | Test |
|---|---|
| Unit | flags, repo map, ACI, lessons, context, events |
| Integration | planner prompt, execution edit validation, memory hints |
| Regression | v1 phase 4, v2 M1/M2/M3/M5, P0 bridge |
| Safety | path escape, secret redaction, no write through ACI |
| Cost | repo-map token cap, lesson hint cap, context caps |
| Fault | corrupt lesson, pagination gap, rate-limit pause |
| Skill | source/installed SKILL.md parity |
| CI | Windows + Ubuntu; macOS note if runner unavailable |

## 12. Risks and rollback

| Risk | Mitigation | Rollback |
|---|---|---|
| Repo map bloats prompt | token cap + shadow diff | flag off |
| ACI rejects valid edit | unique/syntax checks only; structured feedback | flag off |
| Memory poisoning | untrusted label + provenance + size cap | clear lessons file |
| Event schema breaks replay | additive fields only | ignore new fields |
| State additions break runner | optional transitions + tests | flag off |
| Skill docs drift | sync_skill + consistency test | revert doc commit |

## 13. Definition of Done

The integration is complete only when:

1. all five features are implemented behind flags;
2. flags off preserves current behavior;
3. flags on pass unit/integration/safety tests;
4. one live free-model phase passes;
5. skill source and installed copy are synchronized;
6. `docs/ADAPTIVE_TOOLKIT.md`, `docs/RESEARCH_BACKLOG.md` and
   `docs/OPEN_PROBLEMS.md` reflect the new state;
7. no change to `safe_writer`, worktree isolation, budget caps or
   `verify_command` semantics.

## 14. Suggested small-step order

```text
01  flags + baseline tests
02  repo map adapter
03  planner shadow
04  planner on
05  ACI read-only tools
06  edit validation before safe_writer
07  lesson capture
08  lesson injection shadow/on
09  context caps
10  lesson conflict/read-only
11  event IDs/pagination
12  new state guards
13  CLI commands
14  skill docs + sync
15  dashboard indicators
16  shadow pilot
17  enable one feature at a time
18  full acceptance + release note
```

Each numbered step should be one small commit, with tests and a rollback note.
