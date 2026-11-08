# V1 → V2 Feature Parity Matrix

> Status: **inventory / decision gate**, not a promise that every implementation
> file will be copied.
> Last updated: 2026-11-08
>
> Purpose: make sure the zero-user migration to a v2-only product does **not**
> silently lose capabilities that v1 already provides.

## Decision being audited

The target is:

```text
v2 = the only product engine
swarm = the only new-user entrypoint
War Room = the only dashboard
v1.0.5 = frozen fallback / test oracle / emergency tag
```

Removing v1 **compatibility code** is allowed. Removing a v1 **capability**
requires an explicit owner decision recorded here.

---

## Status legend

| Status | Meaning |
|---|---|
| `SHARED` | The module is shared by v1/v2 or v2 already consumes it. |
| `PORTED` | The capability already exists in v2 with acceptable behavior. |
| `PORT_REQUIRED` | Must be ported or reimplemented in v2 before v3. |
| `REPLACED` | v2 has a better implementation; preserve the capability, not the old code shape. |
| `PARTIAL` | A safe subset is ported; the remaining behavior is listed explicitly. |
| `DEFERRED` | Not a v3 blocker, but must have owner approval and a follow-up issue. |
| `DROP_CANDIDATE` | Obsolete/duplicate/unsafe; requires explicit owner approval. |
| `REFERENCE` | Kept only as tests/historical oracle; not a product surface. |

**Release rule:** v3 cannot ship while any user-facing row is
`PORT_REQUIRED`, `REPLACED` without an equivalent, or `DROP_CANDIDATE` without
written approval.

---

## 1. User-facing capability matrix

| Capability | V1 source | V2 today | Decision | Target owner / acceptance |
|---|---|---|---|---|
| Quick single-file task | `job.py`, `recipe_c.py`, classifier/tier | `swarm` + v2 E1/E2 execution | **PORTED** | `test_v2_tiers.py`, `test_swarm_cli.py` |
| General goal → plan | `planner.py`, `clarify.py` | `v2/planner_adapter.py` (goal or v1 plan → validated v2 plan) | **PORTED** | `test_planner_adapter.py`; clarify still advisory |
| Plan validation | `plan_validator.py`, `inspect_plan.py` | v2 `plan.py` validation + adapter summary | **REPLACED** | v2 plan validator + human-readable summary |
| Four execution tiers | `tier_policy.py`, v1 job/recipe | v2 E1/E2/E3/E4 runtime; E1 opt-in and sensitive-path gate | **PORTED** | E1–E4 runtime + `test_v2_tiers.py` |
| Planner levels P1–P3 | `planner.generate_council` | Tier policy proposes P1/P2/P3; only P1 plan generation is wired | **PORT_REQUIRED** | P2/P3 council execution still missing |
| Usage/spend approval | `approval_policy.py`, `usage_request.py`, v1 dashboard | v2 approval + `/v2` page | **REPLACED** | Unified War Room approval; hash-bound |
| Budget/quota | `quota.py`, v1 recipe caps | v2 budget/actual reconcile | **REPLACED** | v2 budget remains authoritative |
| Model routing/fallback | `router.py`, `fallback.py`, `model_selector` | v2 `models.py`/adapters; fallback not identical | **PORT_REQUIRED** | Model routes + fallback behavior tests |
| Provider/catalog preflight | `preflight.py`, `scout.py` | `doctor.py` environment; live model preflight not unified | **PORT_REQUIRED** | doctor + preflight before expensive run |
| Git worktree isolation | not in v1 | v2 isolation | **PORTED** | Keep v2 as stronger behavior |
| Safe writer/transactions | `safe_writer.py` | used by v2 execution | **SHARED** | No bypass; regression suite |
| Secret scanning | `secret_scan.py` | used in workers/v2 | **SHARED** | Keep scanning all prompts/targets |
| Permissions/roles | `permissions.py` | shared | **SHARED** | Role/scope tests |
| Repo lock | `run_lock.py` | v2 uses | **SHARED** | Concurrency tests |
| Adaptive repo map | v1 partial | v2 capability runtime | **PORTED** | E1–E4 context injection tests |
| ACI read-only tools | v1 partial | v2 capability runtime | **PORTED** | ACI integration tests |
| Failure lessons | `memory.py` + v2 lessons | v2 lesson runtime | **PORTED** | Lesson integration tests |
| Bounded memory | v1 memory | v2 context runtime | **PORTED** | Memory cap tests |
| Event/observation stream | v1 ledger | v2 event runtime + per-run events | **PORT_REQUIRED** | Unified history projection |
| Dashboard run view | `dashboard.py`, `team_view.py` | v1 `/` and v2 `/v2` separate | **REPLACED** | One War Room at 8501 |
| Cost/token report | `cost_report.py`, quota | v2 `/v2` spend panel | **PORT_REQUIRED** | Spend panel includes v2 actuals |
| Replay / history | `replay.py`, ledger | v2 events only, no replay UI | **PORT_REQUIRED** | Unified history + replay |
| Draft/apply preview | v1 drafts/backups | v2 handoff diff | **REPLACED** | Handoff preview + optional draft history |
| Pause/resume/stop | v1 job controls | v2 runner controls | **PORTED** | Control API tests |
| Telegram alerts | `telegram_bot.py`, `notify.py` | v2 safe alerts | **PORTED** | Redaction + event tests |
| Two-way Telegram bot | `telegram_bot.py` | not in v2 | **PORT_REQUIRED** | Keep `/status`, `/approve`, controls or explicitly drop |
| Blackboard meeting | `core/meeting.py`, v1 wave | v2 E4 meeting | **PORTED** | E4 parity tests |
| Merge to main | manual/v1 apply | v2 `merge` explicit | **REPLACED** | v2 merge + rollback tests |
| Handoff diff | v1 diff/backups | v2 `/v2` diff button | **PORTED** | War Room diff |
| Presets | not in v1 | v2 free-only/strict | **PORTED** | Preset tests |
| Doctor/prune | not in v1 | v2 doctor/prune | **PORTED** | CLI tests |
| Benchmark/eval tooling | `benchmark.py`, `eval_suite.py` | not in v2 | **DEFERRED** | Owner approves non-blocker |
| Profile detection | `profile_detector.py` | not in v2 | **DEFERRED** | Owner approves non-blocker |
| Plan markdown rendering | `plan_md.py` | not in v2 | **DEFERRED** | Optional reporting |
| Soak harness | `soak_runner.py` | v2 runner supports fake/live | **SHARED/REFERENCE** | Keep for release evidence |

### 1.1 Evidence for the current port (2026-11-08)

| Capability | Code | Deterministic evidence |
|---|---|---|
| E1 low-risk execution | `v2/execution.py` `resolve_tier_profile` | `python .agents/tests/test_v2_tiers.py` |
| E2 security-only review | `v2/execution.py`, `v2/run.py --tier E2` | `test_v2_tiers.py` E2 cases |
| E3 four-reviewer wave | unchanged default for old plans | `test_v2_tiers.py`, `test_v2_parity.py` |
| E4 blackboard meeting | `v2/execution.py` meeting path | `test_v2_tiers.py`, `test_v2_parity.py` |
| Goal → v2 plan | `v2/planner_adapter.py` | `python .agents/tests/test_planner_adapter.py` |
| One-command CLI | `v2/swarm_cli.py`, `swarm.cmd`, `swarm.sh` | `python .agents/tests/test_swarm_cli.py` |

**Hardening added after the first port (same date):**

| Behavior | Code | Why it matters |
|---|---|---|
| Signed-tier guard | `execution.assert_tier_matches_signoff` | A runtime `--tier` that differs from the plan tier would run seats the hash-bound Usage estimate never approved; it is now rejected with a re-plan message. |
| `tier.selected` records `signed_tier` | `ExecutionEngine.execute` event | The event log proves which tier was signed and which tier actually ran. |
| `ExecutionResult.low_risk_opt_in` | result payload | E1 handoffs expose whether the low-risk opt-in was plan-level or runtime. |
| Wider sensitive-path class | `SENSITIVE_SEGMENT_RE` | Adds token/password files, `.npmrc`, `.pypirc`, `.netrc`, `.ssh`, keystores and certificate files to the E1 block list. |
| Declared v1 scopes | `planner_adapter._extra_allowed_paths` | A v1 plan that declared `allowed_paths` no longer loses them; test paths are still excluded. |
| New-file goals | `planner_adapter.detect_targets(--allow-new-files)` | `swarm "create docs/new.md" --allow-new-files` can plan a file that does not exist yet. |
| Job-level verify | `planner_adapter.from_v1_plan` | A v1 plan with one `verify_command` no longer produces work orders with a weaker fallback verify. |
| CLI budget caps | `swarm --max-cost/--max-requests/--max-wall-seconds` | Caps lower the plan envelope *before* the Usage decision; they never increase it, and phase allocations are down-scaled to keep validation sound. |
| CLI version surface | `swarm --version` (2.2.0) | Wrappers and operators can identify the installed entrypoint. |

**Honest gaps still open:** P2/P3 planner councils are proposed but not executed;
the unified War Room at 8501 and fixture-level v1-vs-v2 parity are not implemented
by this slice; live free-model smoke and soak evidence still need a real run;
`clarify.py` ambiguity checks are still advisory; and the CLI caps are explicit
operator flags, not a model-driven cost forecast.
`swarm` defaults to a guarded worktree and never auto-merges.

---

## 2. Module-level audit

This is the code-level inventory. A module is not necessarily a user feature;
some are tooling or tests.

### Safety and execution primitives

| V1 module | Role | V2 equivalent | Status | Action |
|---|---|---|---|---|
| `safe_writer.py` | atomic writes, transaction, rollback | used by v2 execution | SHARED | no change |
| `run_lock.py` | repo single-writer lock | v2 run | SHARED | keep tests |
| `secret_scan.py` | secret/path scanning | workers + v2 | SHARED | keep |
| `permissions.py` | role/path permissions | shared | SHARED | keep |
| `system_bot.py` | guarded command execution | shared by safe_writer | SHARED | keep |
| `approval_policy.py` | Usage gate | v2 runner uses | SHARED | keep |
| `tier_policy.py` | E1–E4 / P1–P3 policy | v2 execution resolves E1–E4; adapter proposes P1–P3 | SHARED | P2/P3 council execution remains open |
| `capability_registry.py` | capability policy matrix | v2 capability runtime | SHARED | expose in War Room |
| `model_registry.py` | normalized model metadata | v2 models/doctor | SHARED | keep |
| `context_guard.py` | context fitting | workers/v2 prompts | SHARED | keep |
| `spend_reconcile.py` | actual cost/token reconciliation | v2 `_call` | SHARED | keep |
| `workers.py` | coder/reviewers/fallback | v2 adapters | SHARED | keep |

### Planning

| V1 module | Role | V2 equivalent | Status | Action |
|---|---|---|---|---|
| `planner.py` | goal → plan, council | `v2/planner_adapter.py` (goal/v1 plan) | PORTED | P2/P3 council still open |
| `clarify.py` | ambiguity checks | not wired | PORT_REQUIRED | call before plan |
| `classifier.py` | task scope/level | tier/risk heuristics in adapter | PARTIAL | full classifier mapping |
| `plan_validator.py` | v1 plan validation | v2 `plan.py` | REPLACED | map/validate v2 plan |
| `inspect_plan.py` | plan inspection | adapter summary + plan JSON | REPLACED | War Room plan panel still open |
| `plan_md.py` | plan rendering | not wired | DEFERRED | optional reporting |
| `core/policy.py` | planning policy | v2 planning | REPLACED | use v2 planning |
| `work_order.py` | v1 work-order schema | v2 plan work_orders | REPLACED | adapter for parity tests |

### Execution and delivery

| V1 module | Role | V2 equivalent | Status | Action |
|---|---|---|---|---|
| `job.py` | plan/job orchestration | `v2/swarm_cli.py` + v2 runner | REPLACED | `swarm` is the entrypoint; do not port internals |
| `recipe_c.py` | single order execution | v2 ExecutionEngine (E1/E2 quick path) | REPLACED | compatibility only as test oracle |
| `fallback.py` | model fallback chain | v2 models/adapters | PORT_REQUIRED | preserve fallback semantics |
| `router.py` | work-type/provider routing | v2 models + adapters | PORT_REQUIRED | unify routes |
| `quota.py` | legacy quota tracker | v2 budget | REPLACED | read-only cost UI only if needed |
| `wave.py` | 4 reviewer wave | v2 execution wave | REPLACED | keep behavior tests |
| `workers.py` | coders/reviewers | v2 adapters | SHARED | keep |
| `core/meeting.py` | blackboard meeting | v2 E4 | SHARED/REPLACED | use v2 entrypoint |
| `core/blackboard.py` | meeting board | v2 E4 | SHARED | retain |
| `core/protocol.py` | meeting protocol | v2 E4 | SHARED | retain |
| `core/budget.py` | v1 budgets | v2 budget | REPLACED | retire after parity |

### Observability, UX, operations

| V1 module | Role | V2 equivalent | Status | Action |
|---|---|---|---|---|
| `dashboard.py` | v1 dashboard / API | v2 `/v2` + unified API | REPLACED | build War Room |
| `team_view.py` | team/status view | not unified | PORT_REQUIRED | War Room data |
| `cost_report.py` | cost report | v2 spend panel | PORT_REQUIRED | War Room spend |
| `replay.py` | replay ledger | v2 events | PORT_REQUIRED | unified history/replay |
| `ledger.py` | v1 event ledger | v2 per-run events | SHARED | state bridge read-only |
| `schema.py` | v1 event schema | v2 schema | SHARED/PORT | normalize |
| `usage_request.py` | usage request surface | v2 approval | REPLACED | War Room approval |
| `notify.py` | one-way webhooks | v2 alerts | SHARED | keep |
| `telegram_bot.py` | two-way bot | v2 alerts only | PORT_REQUIRED | decide keep/drop |
| `soak_runner.py` | soak evidence | v2 runner | REFERENCE | retain release evidence |
| `benchmark.py` | guarded benchmark | not in v2 | DEFERRED | owner decision |
| `eval_suite.py` | eval harness | not in v2 | DEFERRED | owner decision |
| `profile_detector.py` | project profile | not in v2 | DEFERRED | owner decision |
| `format_ladder.py` | output format ladder | workers/format | PORT_REQUIRED | ensure prompt behavior |
| `scout.py` | catalog/auth discovery | shared config | SHARED | keep |
| `preflight.py` | live route gate | doctor + model checks | PORT_REQUIRED | unify preflight |
| `sync_skill.py` | OpenCode skill sync | v2 docs | SHARED | keep |

### Tests / acceptance references

| V1 module | Role | V2 status | Action |
|---|---|---|---|
| `run_phase0_acceptance.py` | Phase 0 acceptance | keep as reference | REFERENCE |
| `run_phase05_acceptance.py` | Phase 0.5 acceptance | keep as reference | REFERENCE |
| `.agents/tests/test_phase*.py` | v1 acceptance | keep until v3 | REFERENCE |
| `.agents/tests/test_v2_tiers.py` | E1-E4 v2 runtime | added, 40 checks | PORTED |
| `.agents/tests/test_planner_adapter.py` | goal/v1 plan → v2 plan | added, 38 checks | PORTED |
| `.agents/tests/test_swarm_cli.py` | CLI E2E | added, 44 checks | PORTED |
| `.agents/tests/test_v2_parity.py` | E3/E4 parity | keep; E1/E2 covered by tier suite | PORTED |
| `.agents/tests/test_*.py` | regression corpus | keep | REFERENCE |

---

## 3. CLI surface mapping

The old commands are not going to be kept as long-term public APIs. Their
capabilities map as follows:

| Old | New target | Status / notes |
|---|---|---|
| `python .agents/job.py --plan plan.json` | `swarm run --plan plan.json` | **IMPLEMENTED**; v2 worktree, explicit merge |
| `python .agents/job.py --goal ...` (if used) | `swarm "goal"` | **IMPLEMENTED**; deterministic planner adapter |
| `python .agents/recipe_c.py --order order.json` | `swarm run --plan <adapted order>` | partial; order→v1 plan shim not built |
| `python .agents/planner.py --goal ...` | `swarm planner --goal ...` | **IMPLEMENTED**; plan artifact remains inspectable |
| `python .agents/dashboard.py` | `python .agents/dashboard.py` (War Room) | **OPEN**; 8501 unification not in this slice |
| `python .agents/replay.py --last` | `swarm status --last` / War Room history | status works; unified replay still open |
| `python .agents/telegram_bot.py` | keep if two-way control is required | decision gate |
| `python .agents/soak_runner.py` | keep as release harness | no product entrypoint |

The installed entrypoints are `swarm.cmd` (Windows) and `swarm.sh`
(POSIX). Both call `python .agents/v2/swarm_cli.py` and accept the same
arguments. `swarm` includes `doctor`, `planner`, `run`, `status`, `handoff` and
`merge`; `run` performs the Usage decision before any model call. `swarm
--version` prints the CLI version (2.2.0) and `run` accepts
`--max-cost`/`--max-requests`/`--max-wall-seconds` to lower the plan envelope
before that decision. Runtime `--tier` must match the signed plan tier; any
change requires re-planning so the Usage estimate is re-approved.

`job.py` and `recipe_c.py` remain in the v1.0.5 tag and in the test oracle, but
are not the new-user interface.

---

## 4. What v2 already does better

These are not ports; they are intentional replacements:

- Git worktree isolation instead of direct-write.
- Per-call actual usage settlement instead of estimated quota only.
- Usage approval bound to plan + estimate hashes.
- Explicit `merge` / `rollback`.
- Four official E1–E4 policy with shared capability runtime.
- Adaptive repo-map/ACI/lessons/memory/events.
- `/v2` spend and handoff preview.
- Doctor, prune, alerts and presets.

## 5. What may be dropped only with owner approval

| Candidate | Why it may be dropped | Impact if dropped |
|---|---|---|
| `job.py` wrapper | no users, compatibility layer is expensive | old CLI name gone |
| `recipe_c.py` wrapper | v2 planner/worktree supersedes it | old order workflow gone |
| v1 dashboard shell | one War Room replaces it | old UI layout gone |
| plan approval | Usage approval is the real gate | old approval click gone |
| direct-write apply | worktree + explicit merge is safer | old instant apply gone |
| two-way Telegram bot | v2 alerts may be enough | chat controls gone |
| benchmark/eval/profile tools | not core to v3 | tooling gap |
| v1 event schema | unified state may replace it | old ledger shape gone |

**None of these are approved yet.** They require an owner decision before code
removal.

---

## 6. Parity gates

Before v3:

- [ ] Every user-facing row is `PORTED`, `REPLACED` with an equivalent, or
      approved `DEFERRED`.
- [ ] Every `DROP_CANDIDATE` has an owner decision.
- [ ] Same fixture through v1 legacy and v2 produces the same target bytes.
- [ ] v1 main worktree is unchanged by v2 until explicit merge.
- [ ] verify commands pass in both paths.
- [x] E1/E2/E3/E4 have expected reviewer/meeting behavior.
- [x] A runtime tier override cannot bypass the signed plan/Usage estimate.
- [x] `swarm "task"` reaches `HANDOFF_READY` with fake seats.
- [x] `swarm` never writes main before explicit `merge`.
- [x] CLI budget caps lower the envelope before the Usage decision.
- [ ] War Room shows v2 run/spend/usage/handoff/diff (still two surfaces).
- [x] Crash/resume and merge/rollback pass (existing v2 suites).
- [ ] Free-model live smoke pass (not run in this deterministic slice).
- [ ] 8-hour soak or agreed equivalent pass.
- [x] `v1.0.5` tag remains available as rollback.

## 7. Immediate next actions

1. ~~Port tier-aware execution, planner adapter and `swarm` CLI.~~ Done in
   `test_v2_tiers.py`, `test_planner_adapter.py`, `test_swarm_cli.py`.
   Follow-up hardened the signed-tier guard, E1 sensitive-path classes, v1
   declared scopes, new-file goals and CLI budget caps.
2. Wire P2/P3 planner councils; the adapter currently proposes the level only.
3. Unify the War Room at 8501 and add replay/spend projections.
4. Run fixture-level v1-vs-v2 parity, free-model live smoke and soak.
5. Review `DROP_CANDIDATE` rows with the owner before deleting v1 modules.
