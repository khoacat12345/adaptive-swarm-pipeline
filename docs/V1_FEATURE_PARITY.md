# V1 → V3 Feature Parity Matrix

> Status: **closed for v3.0.0**. Every user-facing row is ported, replaced by an
> equivalent, or owner-approved deferred.
> Last updated: 2026-11-10
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
| Planner levels P1–P3 | `planner.generate_council` | `v2/planner_adapter.run_planning_council`: one P1 seat, two P2 seats (proposal + critique), three P3 seats; optional model backends | **PORTED** | `test_planner_adapter.py`; `test_unified_orchestrator.py` council cases |
| Usage/spend approval | `approval_policy.py`, `usage_request.py`, v1 dashboard | v2 approval + Unified War Room approval card | **PORTED** | `test_phase4.py`, hash-bound |
| Budget/quota | `quota.py`, v1 recipe caps | v2 budget/actual reconcile | **REPLACED** | v2 budget remains authoritative |
| Model routing/fallback | `router.py`, `fallback.py`, `model_selector` | v2 `models.py`/adapters; fallback not identical | **DEFERRED (owner-approved v3.0.0)** | Keep v2 routing; v1 fallback semantics are a post-v3 follow-up |
| Provider/catalog preflight | `preflight.py`, `scout.py` | `unified/orchestrator.py` runs `doctor` before planning/model calls; live `preflight.check_live` route checks are not yet unified | **DEFERRED (owner-approved v3.0.0)** | Keep doctor gate; live route cross-check follows post-v3 |
| Git worktree isolation | not in v1 | v2 isolation | **PORTED** | Keep v2 as stronger behavior |
| Safe writer/transactions | `safe_writer.py` | used by v2 execution | **SHARED** | No bypass; regression suite |
| Secret scanning | `secret_scan.py` | used in workers/v2 | **SHARED** | Keep scanning all prompts/targets |
| Permissions/roles | `permissions.py` | shared | **SHARED** | Role/scope tests |
| Repo lock | `run_lock.py` | v2 uses | **SHARED** | Concurrency tests |
| Adaptive repo map | v1 partial | v2 capability runtime | **PORTED** | E1–E4 context injection tests |
| ACI read-only tools | v1 partial | v2 capability runtime | **PORTED** | ACI integration tests |
| Failure lessons | `memory.py` + v2 lessons | v2 lesson runtime | **PORTED** | Lesson integration tests |
| Bounded memory | v1 memory | v2 context runtime | **PORTED** | Memory cap tests |
| Event/observation stream | v1 ledger | v2 event runtime + `unified/state.py` read model | **PORTED** | `test_phase4.py`; v1 ledger read-only |
| Dashboard run view | `dashboard.py`, `team_view.py` | Unified War Room at 8501 (v2 state + v1 history) | **REPLACED** | `test_phase4.py`, `test_v2_web_ui.py` |
| Cost/token report | `cost_report.py`, quota | War Room spend panel (estimated vs actual, tokens, calls) | **PORTED** | War Room spend panel |
| Replay / history | `replay.py`, ledger | War Room run history + paged event stream + read-only v1 ledger adapter | **PORTED** | `test_phase4.py`, `test_v2_web_ui.py` |
| Draft/apply preview | v1 drafts/backups | v2 handoff diff + explicit merge/rollback | **REPLACED** | Handoff preview + War Room delivery |
| Pause/resume/stop | v1 job controls | v2 runner controls + War Room control API | **PORTED** | Control API tests |
| Telegram alerts | `telegram_bot.py`, `notify.py` | v2 safe alerts | **PORTED** | Redaction + event tests |
| Two-way Telegram bot | `telegram_bot.py` | not in v2 | **DEFERRED (owner-approved v3.0.0)** | v3 keeps safe one-way alerts; two-way controls are post-v3 |
| Blackboard meeting | `core/meeting.py`, v1 wave | v2 E4 meeting | **PORTED** | E4 parity tests |
| Merge to main | manual/v1 apply | v2 `merge` explicit | **REPLACED** | v2 merge + rollback tests |
| Handoff diff | v1 diff/backups | v2 handoff diff + War Room Merge/Rollback | **PORTED** | War Room diff/delivery |
| Presets | not in v1 | v2 free-only/strict | **PORTED** | Preset tests |
| Doctor/prune | not in v1 | v2 doctor/prune | **PORTED** | CLI tests |
| Benchmark/eval tooling | `benchmark.py`, `eval_suite.py` | not in v2 | **DEFERRED** | Owner approves non-blocker |
| Profile detection | `profile_detector.py` | not in v2 | **DEFERRED** | Owner approves non-blocker |
| Plan markdown rendering | `plan_md.py` | not in v2 | **DEFERRED** | Optional reporting |
| Soak harness | `soak_runner.py` | v2 runner supports fake/live | **SHARED/REFERENCE** | Keep for release evidence |

### 1.1 Evidence for the current port (2026-11-08)

> Validation snapshot (2026-11-10): parity fixtures 45/45, Phase 4B 14/14,
> process crash/resume 11/11, contracts 40/40, orchestrator 63/63, planner
> adapter 38/38, swarm CLI 44/44, v2 tiers 40/40, v2 M3 25/25, Phase 4 36/36,
> v2 web UI 23/23. Full repository: 56 test files, 0 failures. Live free-model
> smoke passed and explicit merge restored `src.py`; short fake soak 3/3. All
> commands exit 0; no existing test file was edited.

| Capability | Code | Deterministic evidence |
|---|---|---|
| E1 low-risk execution | `v2/execution.py` `resolve_tier_profile` | `python .agents/tests/test_v2_tiers.py` |
| E2 security-only review | `v2/execution.py`, `v2/run.py --tier E2` | `test_v2_tiers.py` E2 cases |
| E3 four-reviewer wave | unchanged default for old plans | `test_v2_tiers.py`, `test_v2_parity.py` |
| E4 blackboard meeting | `v2/execution.py` meeting path | `test_v2_tiers.py`, `test_v2_parity.py` |
| Goal → v2 plan | `v2/planner_adapter.py` | `python .agents/tests/test_planner_adapter.py` |
| P1–P3 planning council | `v2/planner_adapter.run_planning_council` | `test_planner_adapter.py`, `test_unified_orchestrator.py` |
| v1 `order.json` shim | `v2/planner_adapter.from_order`, `swarm run --order` | `test_unified_orchestrator.py` order cases |
| v1-vs-v2 parity fixtures | `test_v1_v2_parity.py` (single-file, multi-file, security-sensitive) | `python .agents/tests/test_v1_v2_parity.py` (45 checks) |
| Unified contracts | `unified/contracts.py` | `python .agents/tests/test_unified_contracts.py` (40 checks) |
| Unified orchestrator | `unified/orchestrator.py` (CLI delegates) | `python .agents/tests/test_unified_orchestrator.py` (63 checks) |
| One-command CLI | `v2/swarm_cli.py`, `swarm.cmd`, `swarm.sh` | `python .agents/tests/test_swarm_cli.py` |
| Unified state reader | `unified/state.py` (v2 + read-only v1 ledger) | `python -m compileall .agents/unified/state.py`; dashboard smoke |
| Unified War Room API | `unified/api.py`, `dashboard.py` routes | `python .agents/tests/test_phase4.py` |
| One-page War Room | `dashboard_ui.html`, default port 8501 | `test_v2_web_ui.py`, `test_meeting_ui.py` |

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
| CLI version surface | `swarm --version` (3.0.0) | Wrappers and operators can identify the installed entrypoint. |

**Honest gaps still open:** fixture-level parity now passes, but live
free-model smoke and soak evidence still need a real run; `clarify.py` ambiguity
checks are still advisory; the unified preflight uses `doctor` but the live route
catalog check (`preflight.check_live`) is not wired into the orchestrator yet;
and the CLI caps are explicit operator flags, not a model-driven cost forecast.
The War Room is implemented; its v1 rows are a read-only history projection,
not a second execution path.
`swarm` defaults to a guarded worktree and never auto-merges; the orchestrator
has no merge path at all.

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
| `tier_policy.py` | E1–E4 / P1–P3 policy | v2 execution resolves E1–E4; adapter executes P1–P3 councils | SHARED | keep tests |
| `capability_registry.py` | capability policy matrix | v2 capability runtime | SHARED | expose in War Room |
| `model_registry.py` | normalized model metadata | v2 models/doctor | SHARED | keep |
| `context_guard.py` | context fitting | workers/v2 prompts | SHARED | keep |
| `spend_reconcile.py` | actual cost/token reconciliation | v2 `_call` | SHARED | keep |
| `workers.py` | coder/reviewers/fallback | v2 adapters | SHARED | keep |

### Planning

| V1 module | Role | V2 equivalent | Status | Action |
|---|---|---|---|---|
| `planner.py` | goal → plan, council | `v2/planner_adapter.py` (goal/v1 plan/order + P1–P3 council) | PORTED | keep tests |
| `clarify.py` | ambiguity checks | not wired | DEFERRED (owner-approved v3.0.0) | advisory post-v3 wiring |
| `classifier.py` | task scope/level | tier/risk heuristics in adapter | DEFERRED (owner-approved v3.0.0) | full classifier mapping post-v3 |
| `plan_validator.py` | v1 plan validation | v2 `plan.py` | REPLACED | map/validate v2 plan |
| `inspect_plan.py` | plan inspection | adapter summary + plan JSON | REPLACED | War Room plan panel still open |
| `plan_md.py` | plan rendering | not wired | DEFERRED | optional reporting |
| `core/policy.py` | planning policy | v2 planning | REPLACED | use v2 planning |
| `work_order.py` | v1 work-order schema | v2 plan work_orders | REPLACED | adapter for parity tests |

### Execution and delivery

| V1 module | Role | V2 equivalent | Status | Action |
|---|---|---|---|---|
| `job.py` | plan/job orchestration | `unified/orchestrator.py` + v2 runner | REPLACED | `swarm` is the entrypoint; do not port internals |
| `recipe_c.py` | single order execution | v2 ExecutionEngine (E1/E2 quick path) + `from_order` shim | REPLACED | order.json maps to a v2 plan; keep executor as test oracle |
| `fallback.py` | model fallback chain | v2 models/adapters | DEFERRED (owner-approved v3.0.0) | preserve fallback semantics post-v3 |
| `router.py` | work-type/provider routing | v2 models + adapters | DEFERRED (owner-approved v3.0.0) | unify routes post-v3 |
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
| `dashboard.py` | v1 dashboard / API | unified API + one-page War Room at 8501 | REPLACED | War Room implemented |
| `team_view.py` | team/status view | War Room history/status cards | PORTED | War Room data |
| `cost_report.py` | cost report | War Room spend panel | PORTED | War Room spend |
| `replay.py` | replay ledger | War Room run history + paged events; v1 ledger read-only | PORTED | unified history |
| `ledger.py` | v1 event ledger | v2 per-run events | SHARED | state bridge read-only |
| `schema.py` | v1 event schema | v2 schema | SHARED/PORT | normalize |
| `usage_request.py` | usage request surface | v2 approval + War Room card | REPLACED | War Room approval |
| `notify.py` | one-way webhooks | v2 alerts | SHARED | keep |
| `telegram_bot.py` | two-way bot | v2 alerts only | DEFERRED (owner-approved v3.0.0) | safe one-way alerts remain; two-way controls post-v3 |
| `soak_runner.py` | soak evidence | v2 runner | REFERENCE | retain release evidence |
| `benchmark.py` | guarded benchmark | not in v2 | DEFERRED | owner decision |
| `eval_suite.py` | eval harness | not in v2 | DEFERRED | owner decision |
| `profile_detector.py` | project profile | not in v2 | DEFERRED | owner decision |
| `format_ladder.py` | output format ladder | workers/format | DEFERRED (owner-approved v3.0.0) | ensure prompt behavior post-v3 |
| `scout.py` | catalog/auth discovery | shared config | SHARED | keep |
| `preflight.py` | live route gate | doctor + model checks | DEFERRED (owner-approved v3.0.0) | unify live route checks post-v3 |
| `sync_skill.py` | OpenCode skill sync | v2 docs | SHARED | keep |

### Unified additions (no v1 counterpart)

| Module | Role | Status | Evidence |
|---|---|---|---|
| `.agents/unified/contracts.py` | `UnifiedRequest`, `UnifiedResult`, `PreflightStatus`, `PlanDecision`, `RunProgress` | NEW / PORTED CONTRACT | `test_unified_contracts.py` 40/40 |
| `.agents/unified/orchestrator.py` | preflight → plan/council → Usage → execute → verify/checkpoint/handoff | NEW / REPLACES CLI INTERNALS | `test_unified_orchestrator.py` 63/63 |
| `v2/planner_adapter.run_planning_council` | P1/P2/P3 council with deterministic and model seats | NEW | `test_planner_adapter.py`, orchestrator council cases |
| `v2/planner_adapter.from_order` | v1 `order.json` → validated v2 plan | NEW / COMPATIBILITY SHIM | orchestrator order cases |
| `.agents/unified/state.py` | v2 state + read-only v1 ledger projection (status/tier/planner/spend/verify/handoff/warnings) | NEW / REPLACES SPLIT STATE | compileall + Phase 4 |
| `.agents/unified/api.py` | unified GET/POST routes with token, redaction, loopback and diff cap | NEW / WAR ROOM API | `test_phase4.py` |
| `.agents/dashboard_ui.html` War Room view | timeline, badges, spend, usage, blackboard, diff/merge/rollback, history | NEW / ONE DASHBOARD | `test_v2_web_ui.py`, `test_meeting_ui.py` |

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
| `python .agents/recipe_c.py --order order.json` | `swarm run --order order.json` | **IMPLEMENTED**; `from_order` shim maps the validated order to a v2 plan, worktree and explicit merge |
| `python .agents/planner.py --goal ...` | `swarm planner --goal ...` | **IMPLEMENTED**; plan artifact remains inspectable |
| `python .agents/dashboard.py` | `python .agents/dashboard.py` (War Room) | **IMPLEMENTED**; default 8501, `/v2` alias kept |
| `python .agents/replay.py --last` | `swarm status --last` / War Room history | status + unified history implemented |
| `python .agents/telegram_bot.py` | keep if two-way control is required | decision gate |
| `python .agents/soak_runner.py` | keep as release harness | no product entrypoint |

The installed entrypoints are `swarm.cmd` (Windows) and `swarm.sh`
(POSIX). Both call `python .agents/v2/swarm_cli.py` and accept the same
arguments. `swarm` includes `doctor`, `planner`, `run`, `status`, `handoff` and
`merge`; `run` performs the Usage decision before any model call. `swarm
--version` prints the CLI version (3.0.0) and `run` accepts
`--max-cost`/`--max-requests`/`--max-wall-seconds` to lower the plan envelope
before that decision. Runtime `--tier` must match the signed plan tier; any
change requires re-planning so the Usage estimate is re-approved.
`swarm run --order order.json` uses the v1 order shim, and
`--council`/`--planner-model` execute the P2/P3 planning seats. The terminal
CLI delegates the whole pipeline to `.agents/unified/orchestrator.py`.

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
- One War Room at 8501 that reads v2 state and v1 history without a second
  execution path.

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

The Product Owner approved this drop/defer list for the v3.0.0 release. The v1
modules remain in the `v1.0.5` fallback and test oracle; they are not advertised
as the new-user surface.

---

## 6. Parity gates

Before v3:

- [x] Every user-facing row is `PORTED`, `REPLACED` with an equivalent, or
      approved `DEFERRED`.

> Owner approved the remaining v1 fallback/preflight/format/two-way-bot
> deferrals for the v3.0.0 release; they are not claimed as ported.
- [x] Every `DROP_CANDIDATE` has an owner decision.
- [x] Same three fixtures through v1 legacy and v2 produce the same target bytes.
- [x] v1 main worktree is unchanged by v2 until explicit merge.
- [x] verify commands pass in both paths.
- [x] E1/E2/E3/E4 have expected reviewer/meeting behavior.
- [x] A runtime tier override cannot bypass the signed plan/Usage estimate.
- [x] `swarm "task"` reaches `HANDOFF_READY` with fake seats.
- [x] `swarm` never writes main before explicit `merge`.
- [x] CLI budget caps lower the envelope before the Usage decision.
- [x] P2/P3 planning councils execute and validate before selection.
- [x] v1 `order.json` maps to a v2 plan without re-implementing recipe logic.
- [x] War Room shows v2 run/spend/usage/handoff/diff (one surface at 8501).
- [x] Crash/resume and merge/rollback pass through `swarm resume`/`swarm rollback`.
- [x] Free-model live smoke pass (`v2_20261110T070617_56c0a0`).
- [x] Full 8-hour soak passed (459/459 fake cycles, 2026-11-07); post-Phase 4 3-cycle smoke also passed.
- [x] `v1.0.5` tag remains available as rollback.

## 7. Release closeout

1. ~~Port tier-aware execution, planner adapter and `swarm` CLI.~~ Done in
   `test_v2_tiers.py`, `test_planner_adapter.py`, `test_swarm_cli.py`.
   Follow-up hardened the signed-tier guard, E1 sensitive-path classes, v1
   declared scopes, new-file goals and CLI budget caps.
2. ~~Wire P2/P3 planner councils.~~ Done in `run_planning_council`; optional
   model seats are available through `--planner-model`.
3. ~~Unify the War Room at 8501 and add replay/spend projections.~~ Done in
   `unified/state.py`, `unified/api.py` and the War Room view; verified by
   `test_phase4.py` (36/36), `test_v2_web_ui.py` (23/23) and
   `test_meeting_ui.py` (31/31).
4. ~~Run fixture-level v1-vs-v2 parity, free-model live smoke and soak.~~ Three
   parity fixtures pass 45/45; live free-model smoke passed; full 8-hour fake
   soak passed 459/459 and the post-Phase 4 short smoke passed 3/3.
5. ~~Review `DROP_CANDIDATE` rows with the owner.~~ Owner approved the list for
   v3.0.0; v1 remains frozen as the fallback/test oracle.
