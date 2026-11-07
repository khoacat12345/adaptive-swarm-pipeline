# Open Problems — Adaptive Swarm Pipeline

> Last updated: 2026-11-07
> Current release: `v2.1.0` (opt-in; `v1.0.5` remains default)
> Purpose: capture known gaps now, triage them once, and fix them in batches
> instead of scattering fixes through unrelated sessions.

## Rules

- Append new problems; do not silently delete history.
- Mark a problem resolved only with a commit/test reference.
- Priority:
  - **P0** — blocks production or can lose money/data.
  - **P1** — important before daily use or release-candidate confidence.
  - **P2** — polish/scale, not a safety blocker.
- Status: `open`, `investigating`, `blocked`, `resolved`.
- Prefer a measurable exit criterion over "make it better".

## Current triage summary

| Batch | Theme | Why first |
|---|---|---|
| B1 | Cost + provider truth | Budget is a hard promise; estimates are not enough |
| B2 | Live free pilot + soak | v2.1 is still opt-in until exercised |
| B3 | Spec Kit bridge | Turn external process artifacts into v2 plans |
| B4 | Runtime breadth/sandbox | OpenCode-first is fine now; avoid lock-in |
| B5 | UI/reporting polish | Operator experience after safety is stable |

---

## P0 — blocks production confidence

### P0-01 — v2 live execution has not been run end-to-end

- **Status:** resolved — live free-model v2 phase passed on 2026-11-07
- **Batch 5 evidence:** `prepare -> plan -> usage-approve -> execute -> verify -> checkpoint -> handoff` on a temporary repo; `HANDOFF_READY`, 1 request, 4 approvals, checkpoint `c7ac85c...`, main `VALUE = 1` unchanged, worktree `VALUE = 2`, handoff patch/report produced. Full detail: [`LIVE_PILOT_REPORT.md`](LIVE_PILOT_REPORT.md).
- **Area:** M5 pilot
- **Evidence:** M1–M5 acceptance tests use fake planner/coder/reviewers; `execute --free` has not completed a real live phase.
- **Risk:** Adapter/tool-calling/session issues can hide until a real model is used.
- **Exit criterion:** one real free-model phase completes:
  `prepare → plan → signoff → execute → verify → checkpoint → handoff`,
  with main worktree unchanged.
- **Suggested first target:** one small target file, no network in verify, one phase.

### P0-02 — actual spend/cost is not reconciled

- **Status:** resolved — per-call reconciliation is wired into v2 `_call`
- **Area:** budget/cost
- **Batch 2 progress (2026-11-06):** added before/after snapshots, `Reconciler`, over-budget detection and an OpenCode DB source adapter. `test_cost_reconciliation.py`: 7/7 PASS.
- **Deep-wiring evidence (2026-11-07):** `ExecutionEngine._call()` snapshots usage before/after every provider call, settles actual cost/tokens into `BudgetController`, and stops the run on overrun. Paid seats are blocked when the actual usage source is unavailable.
- **Evidence:** `ExecutionSeat.cost_usd` is an estimate; `BudgetController` settles the estimated cost, not measured provider billing. `pricing` is `null` for many catalog entries.
- **Risk:** a paid run can stay under the estimated budget while actual provider spend is higher.
- **Exit criterion:** after each call, actual tokens/cost are read from OpenCode stats or 9router usage, compared with the reservation, and an overrun stops the run before the next call.
- **Fallback:** if no usage source exists, paid models remain blocked; document the limitation explicitly in `/v2`.

### P0-03 — 9router/provider capability data is incomplete

- **Status:** resolved — offline fallback metadata covers the main model families
- **Area:** providers
- **Batch 2 progress (2026-11-06):** added `model_registry.py` with normalized model_id/provider/family/context/tools/pricing/auth/health.
- **Capability metadata evidence (2026-11-07):** `FALLBACK_METADATA` supplies context/tool/json/pricing for DeepSeek V4.1, GLM-5.3, Nemotron 3.5 and Qwen 3.8 when the live catalog/9router is unavailable. `test_capability_registry.py`: 20/20 PASS. 9router remains optional; missing live data no longer blocks metadata.
- **Evidence:** current `config.json` reports `9router: unavailable`; catalog entries have `pricing: null`; some providers expose only a generic model ID.
- **Risk:** cross-family checks, tool-calling checks, context limits, cost caps, and fallback selection can be wrong.
- **Exit criterion:** a capability registry exists with:
  `model_id`, provider, family, context window, tools, JSON mode, input/output price, auth/health, quota source.
- **Need:** decide whether 9router remains a local gateway or a shared/team gateway.

### P0-04 — live paid-model guard is only an estimate gate

- **Status:** resolved — actual usage settlement stops paid overruns in v2
- **Area:** cost safety
- **Batch 2 progress (2026-11-06):** `model_registry.py` exposes pricing/health/tool metadata; `spend_reconcile.py` computes actual-vs-estimate deltas and over-budget flags.
- **Deep-wiring evidence (2026-11-07):** `_call` passes actual cost into `BudgetController.settle`; `BudgetOverrun` sets `stop_reason=budget_overrun` before the next call. Paid seats fail closed if the usage source is unavailable.
- **Evidence:** paid models are blocked when `cost_usd == 0`; with `--paid-cost N`, each call reserves `N` but actual usage is not verified.
- **Risk:** a wrong `N` under-reserves.
- **Exit criterion:** either real usage settlement (P0-02) or a hard provider-level spend cap enforced at 9router/gateway.

### P0-05 — long-run crash/resume is not process-tested

- **Status:** resolved — real v2 phase process kill + recover + handoff passed
- **Area:** overnight reliability
- **Batch 3 progress (2026-11-06):** `test_resilience.py` kills a child process after a transaction write and proves `recover_open_transactions()` restores exact bytes (8/8 PASS).
- **Process-kill evidence (2026-11-07):** `test_phase_process_crash.py` spawns a real v2 phase, kills it with `taskkill /F`, calls `v2 recover`, re-executes, and proves checkpoint/handoff/main isolation/no extra worktree files (10/10 PASS).
- **Evidence:** state transitions and checkpoint records are unit-tested; there is no test that kills the runner process mid-phase and resumes it in a new process.
- **Risk:** duplicate writes, stuck locks, or lost checkpoints under a real crash.
- **Exit criterion:** kill runner after write/before verify and after verify/before checkpoint; new process resumes safely; no duplicate file version.

### P0-06 — Replace plan approval with conditional Usage approval

- **Status:** resolved — implementation + docs + regression complete
- **Plan:** [`PLAN_P0_06_USAGE_APPROVAL.md`](PLAN_P0_06_USAGE_APPROVAL.md)
- **Progress (2026-11-06):**
  - Added shared `.agents/approval_policy.py` (estimate, policy, hash-bound approval record).
  - `job.py` auto-runs quick plans and gates expensive plans on Usage approval.
  - `recipe_c --order` blocks expensive direct runs; `--wait-usage` opens the dashboard and waits for approval.
  - v2 `usage-approve` / `assert-usage` added; `/v2` button is **Approve Usage**; `execute` asserts Usage approval.
  - Dashboard Usage API/UI added; Plan page is preview-only.
  - Operator guide: [`OPERATOR_GUIDE.md`](OPERATOR_GUIDE.md).
  - Tests: approval policy 28/28; usage gate 2/2; Phase 6A 82/82; wave 32/32; meeting UI 31/31; preflight 38/38; v2 M1 43/43, M2 47/47, M3 25/25, M5 14/14, P0 15/15; skill 16/16; unittest discover 34/34.
  - README/INSTALL/SKILL/README.vi/.agents README updated for the new flow.
- **Priority rationale:** super-urgent product rule. Current behavior makes quick tasks require plan approval while expensive paths can bypass it. The owner’s intended rule is different and must become explicit.
- **Area:** approval / cost control / UI
- **Owner decision:**
  - **Plan approval is removed as the default human gate.** The planner always runs automatically; a validated plan may start without a plan-approval click.
  - Human approval is **only for quota/spend**. The trigger is an estimated request/cost threshold, not the existence of a plan.
  - The approval UI lives in **Usage**, not Plan. It must show the plan summary, selected agents/models, estimated requests, estimated cost, wall-clock estimate and remaining budget.
  - Approving Usage approves that exact plan + estimate; if the plan or estimate changes, approval is invalidated.
- **Required rule:**
  ```text
  usage_approval_required =
      mode == "meeting"
      OR reviewer == "wave"
      OR overnight/multi_phase
      OR paid_model
      OR estimated_requests >= threshold
      OR estimated_cost >= threshold
  ```
  Quick path:
  ```text
  usage_approval_required = false
  auto-run with verify_command + budget cap + reviewer + rollback
  ```
  “War Room session” in this document means the expensive multi-agent execution path, not the dashboard tab. The Plan page is preview-only.
- **Current inconsistent behavior:**
  - `job.py --plan ...` requires an approved plan for every plan, including a quick task.
  - `recipe_c.py --order ...` has no approval check, even for wave/meeting.
  - `.agents/SKILL.md` recommends direct `recipe_c --order`, so skill-driven runs can accidentally take the no-approval path.
  - `--decision approve` is headless plan approval, not usage approval.
  - v2 `signoff` is currently a plan/hash approval before `execute`; it must be redefined as Usage approval or replaced by the Usage gate.
  - `Accept handoff` is a separate end-of-run acknowledgement, not the start/apply gate.
- **Risk:**
  - Approval fatigue: users click through quick tasks until approval means nothing.
  - Quota burn: an expensive wave/meeting starts without a spend check through the direct recipe path.
  - False confidence: operators think every task passed a human gate.
  - Duplicate gates: Plan approval and Usage approval can disagree; the Plan page and README already show contradictory commands.
- **Exit criterion:**
  - One shared `usage_approval_policy`; `job.py`, `recipe_c.py`, dashboard API and v2 consume it.
  - Plan page is preview-only; no default Approve button there.
  - Usage approval is triggered only by the rule above and is bound to `plan_hash + estimate_hash`.
  - Quick path auto-runs and emits `approval.skipped` with reason `below_threshold`.
  - Expensive path blocks before the first model call until `usage.approved` exists, including direct `recipe_c`.
  - Approval screen shows plan, agents/models, estimated usage/cost, budget and rollback policy.
  - Tests cover: quick auto-run, threshold trigger, paid trigger, meeting trigger, direct-recipe bypass blocked, plan-change invalidates approval.
  - README has one approval matrix and no plan-approval instruction left.
- **Threat-model note:** if the agent has unrestricted shell access, it can bypass workflow gates entirely. Usage approval is cost control, not an OS sandbox; a real boundary needs separate OS/user/container permissions or an external approval service.
- **Related:** P0-07, P1-13; `.agents/SKILL.md`, `.agents/recipe_c.py`, `.agents/job.py`, `.agents/v2/execution.py`, `.agents/dashboard_ui.html`, `README.md`.

### P0-07 — Four-tier execution/planner model, mandatory planner, and M2 tracking

- **Status:** resolved — E3 wave and E4 meeting parity pass on v2; tier/planner/timeline policy landed
- **Batch 5 parity evidence (2026-11-07):** `test_v2_parity.py` 11/11 PASS: E3 uses four independent wave reviewers; E4 opens the blackboard meeting, records transcript/votes, and runs Round 2 on conflict. Planner P2/P3 and compact E2 timeline are recorded in state/ledger.
- **Batch 1 progress (2026-11-06):**
  - Added `tier_policy.py` (E1–E4, P1–P3, deterministic selection).
  - Added `capability_registry.py` for per-tier capability policy/wiring truth.
  - `job.py` and `recipe_c.py` now record `tier.selected` + `planner.selected` and expose tier/capabilities in state/report.
  - `planner.py` supports `--tier` / `--planners` and `generate_council` for P1/P2/P3.
  - Dashboard `/api/job` and Usage card expose tier/planner and compact E2 timeline.
  - `test_tiers.py`: 44/44 PASS.
  - Remaining: mandatory-planner guard at the skill boundary, full E3/E4 execution parity tests, and live capability wiring (P1-14).
- **Priority rationale:** the tiering is now the core product model; without it, approval, planner count and UI tracking cannot be implemented consistently.
- **Area:** orchestration / UX
- **Owner decision:** use four **execution tiers** (separate from the existing classifier `Level 0-3`, which describes task scope):
  ```text
  E1  coder only
  E2  coder + 1 reviewer
  E3  coder + reviewer + additional specialist reviewer(s)
  E4  meeting (full multi-agent round-based review)
  ```
  Exact E3 roster is still to confirm (candidate roles: security, QA, perf, docs; choose by risk). E4 is the existing meeting mode with all four reviewer roles.
- **Planner tiers:**
  ```text
  P1  1 planner   -> simple tasks; E1/E2; no debate
  P2  2 planners  -> proposal + critique; E3
  P3  3 planners  -> full council; E4 meeting
  ```
  Planner is **always invoked when the OpenCode skill handles a coding task**. There is no skill path that goes straight to `recipe_c` without a planner. Direct `recipe_c` remains an internal/developer tool, not a skill entry point.
- **M2 tracking requirement:**
  - Current M2 (coder + reviewer) does not have a dedicated process view; the full War Room is the only detailed UI and direct recipe runs may not produce dashboard state.
  - Every tier must write the same baseline events: `tier.selected`, `planner.selected`, `coder.started`, `reviewer.started`, `verify.started`, `verify.passed/failed`, `apply.committed/rolled_back`.
  - E2 gets a lightweight Run timeline (plan → coder → reviewer → verify → apply) in Usage/Activity/Chat, without the full War Room graph.
  - E3/E4 use the full War Room graph and meeting transcript.
  - It must be possible to follow an E2 run without opening War Room.
- **Required behavior:**
  - Classifier + risk/cost policy selects E1-E4; the user can override explicitly.
  - E1/E2 do not require Usage approval unless the cost/request threshold fires.
  - E3/E4 follow P0-06 Usage approval rules.
  - Planner count follows the tier; E4 always uses P3.
  - The dashboard shows the chosen tier and why it was chosen (`tier.reason`).
- **Exit criterion:**
  - One `tier_policy` and one `planner_policy` module, with deterministic rules and explicit override.
  - Planner invocation is mandatory on the skill entry path; tests prove a skill task cannot bypass the planner.
  - E1-E4 each have an end-to-end test and a UI state.
  - E2 has a compact timeline visible outside War Room.
  - Tier/planner selection is recorded in the ledger and visible in Usage.
- **Open question:** whether E1 (coder only) is allowed in production or should be restricted to dry-run/read-only tasks. Product owner to confirm.
- **Related:** P0-06, P1-13, P1-14; `.agents/classifier.py`, `.agents/planner.py`, `.agents/job.py`, `.agents/workers.py`, `.agents/dashboard_ui.html`.

---

## P1 — before daily use / release-candidate confidence

### P1-01 — 8–10 hour soak not executed

- **Status:** resolved — 459 overnight cycles PASS (2026-11-07 00:23 → 08:23)
- **Soak evidence:** `docs/SOAK_LOG.md` ends with 459 successful `HANDOFF_READY` cycles and a clean stop. All cycles had `ok=True`; no stuck process or worktree pollution was observed.
- **Area:** M5 soak
- **Soak evidence:** `.agents/soak_runner.py` started in background (`--mode fake --hours 8 --interval 60 --keep-awake`), heartbeat in `docs/SOAK_LOG.md`. Live pilot remains separately proven. Tool: [`../.agents/soak_runner.py`](../.agents/soak_runner.py).
- **Evidence:** M5 uses a multi-phase synthetic soak and fake clock; no wall-clock long run.
- **Risk:** memory/disk/log growth, sleep/resume, auth expiry and quota drift are untested at night.
- **Exit criterion:** one night run with phase budget caps, periodic heartbeat, disk/log checks and a morning handoff report.

### P1-02 — Windows sleep/lock/session behavior

- **Status:** resolved — keep-awake overnight run completed 459 cycles without process death
- **Evidence:** the 8-hour run used `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)` and finished cleanly; see `docs/SOAK_LOG.md`.
- **Area:** overnight reliability
- **Soak evidence:** start command used `--keep-awake`; log in `docs/SOAK_LOG.md`. A physical lock/sleep test still needs operator observation.
- **Evidence:** worktree/verify are tested on Windows in CI; active overnight sleep/power policy is not.
- **Risk:** laptop sleeps, OpenCode session dies, or network drops at 03:00.
- **Exit criterion:** documented power policy + resume test; runner pauses safely or prevents sleep explicitly.

### P1-03 — real OpenCode adapter coverage

- **Status:** resolved — real free-model coder + four reviewer calls completed
- **Area:** M3.5
- **Batch 5 evidence:** `WorkersCoderAdapter` + four `WorkersReviewerAdapter` seats ran live: all four approved, diff verified, requests=1, checkpoint/handoff created. See [`LIVE_PILOT_REPORT.md`](LIVE_PILOT_REPORT.md).

### P1-04 — CleanArchitect seat is defined but not wired

- **Status:** resolved — unused `cleaner` parameter removed; cleanup deferred in spec
- **Area:** execution roster
- **Batch 3 progress (2026-11-06):** `ExecutionEngine.execute` no longer accepts a silent `cleaner`; `SWARM_V2.1_SPEC.md` documents CleanArchitect as deferred with re-verify requirements.

### P1-05 - Spec Kit bridge is partially implemented

- **Status:** resolved — real Git my-project signed v2 execution passed end-to-end
- **Area:** process/integration
- **Batch 4 progress (2026-11-06):** created `C:\Users\tuank\my-project\specs\000-p0-06-pilot` with spec.md + plan.md + tasks.yaml; `bridge_spec_kit()` produced a valid `MasterPlan.json` with phase `P1`.
- **End-to-end evidence (2026-11-07):** initialized Git, ran `prepare -> bridge -> usage-approve -> execute -> verify -> checkpoint -> handoff` on `my-project`; run `v2_20261107T045042_561bf2`, checkpoint `58ac699e...`, main `demo.txt=value=1` unchanged, worktree `value=2`, verify exit 0, converge `ok=true`. See [`SPEC_KIT_ADOPTION.md`](SPEC_KIT_ADOPTION.md).
- **Exit criterion:** satisfied.

### P1-06 — constitution / converge are missing

- **Status:** resolved — deterministic `v2 converge` added and tested
- **Area:** process
- **Batch 4 progress (2026-11-06):** `v2/converge.py` checks source-artifact hashes, allowed paths, changed files, target coverage and verify exit codes; `v2/run.py converge --run-id` exposes it. `test_speckit_converge.py`: 10/10 PASS.
- **Evidence:** v2 has plan hash/sign-off and now an explicit spec↔code convergence report.

### P1-07 — runtime is OpenCode-only

- **Status:** resolved — second-runtime stub registered and tested; production adapter remains future work
- **Area:** architecture
- **Batch 4 progress (2026-11-06):** `ClaudeCodeRuntimeStub` implements the `AgentRuntime` shape and fails closed; `test_speckit_converge.py` verifies registry/capabilities/health. No production Claude/Codex/Gemini adapter is claimed.

### P1-08 — provider fallback and cross-family are not tied to a capability registry

- **Status:** resolved — tool filtering and context-window enforcement are live
- **Area:** routing
- **Batch 2 progress (2026-11-06):** `model_registry.py` normalizes tooling/context/pricing/auth/health.
- **Context evidence (2026-11-07):** `v2/models.py` filters known `tooling < 0.5`; `workers.py` applies `context_guard.fit_prompt` before coder/reviewer dispatch. `test_context_guard.py` 7/7 PASS.
- **Risk:** a free model can pass family checks but fail tool calling mid-phase.
- **Exit criterion:** role selection requires `supports_tools` (and required context) unless explicitly overridden.

### P1-09 — v1 planner still prefers paid models

- **Status:** resolved — planner is free-only by default; paid requires `--allow-paid`
- **Area:** v1 cost
- **Evidence:** fixed in Batch 2: `_models(free_only=True)` and `generate(free_only=True)` now choose free models first; paid needs `--allow-paid`.
- **Risk:** none remaining for the default path; paid is still explicit.
- **Exit criterion:** satisfied by `planner.py --allow-paid` opt-in and `test_tiers.py` default checks.

### P1-10 — 9router trust boundary and secret handling

- **Status:** resolved — provider-bound prompts and reviewer prompts are secret-scanned
- **Area:** security
- **Evidence:** `secret_scan.py` masks keys/secrets and blocks sensitive files; coder/reviewer prompts now call it before dispatch, including RoleReviewer; capability context is also scanned.
- **Exit criterion:** satisfied for prompt/log sanitization. 9router remains optional and is not required for the local free-model path.

### P1-11 — concurrent runs / stale plan state

- **Status:** resolved — repo lock + stale reclaim tested; job/recipe/v2 CLI use it
- **Area:** concurrency
- **Batch 3 progress (2026-11-06):** `.agents/run_lock.py` writes `.agents/tmp/run.lock` with PID/owner; `job.py`, `recipe_c.py` and `v2/run.py` acquire it. `test_resilience.py` verifies second-process blocking and stale-lock reclaim.
- **Evidence:** v1 plan state is a single shared `.agents/tmp/plan.json`; a second session/planner can replace it. v2 has a run lock only inside its own state model.
- **Risk:** two sessions overwrite each other’s plan/approval.
- **Exit criterion:** per-repo lock is enforced before planner/run; stale lock detection; no shared mutable plan.
- **Note:** this already happened during M3 planning: a separate planner process was writing a different plan.

### P1-12 - Spec Kit adoption plan

- **Status:** resolved — adoption flow documented and closed
- **Area:** process/integration
- **Adoption doc:** [`SPEC_KIT_ADOPTION.md`](SPEC_KIT_ADOPTION.md) records `Specify -> Plan -> Tasks -> Bridge -> v2 Execution -> Converge`, artifact hashes, commands and safety boundaries.
- **Decision:** borrow Spec Kit's process contracts, not its execution model.
- **Take now (P0):**
  - `constitution.md` + constitution hash bound into sign-off.
  - canonical `tasks.yaml` schema with `verify_command`, `target_files`, dependencies and acceptance criteria.
  - `speckit_bridge.py` converting Spec Kit artifacts into a validated `MasterPlan.json`.
  - `AgentRuntime` interface and registry for OpenCode / Claude Code / Antigravity.
- **Take later:** clarify/checklist/analyze gates, deterministic converge, preset profiles, optional agent-context sync.
- **Do not take:** Spec Kit Git extension, prose `tasks.md` parsing, or its free-form execution model.
- **Safety boundary:** do not change worktree isolation, `safe_writer`, budget caps or `verify_command`.
- **Exit criterion:** a Spec Kit-generated `tasks.yaml` becomes a signed v2 plan without bypassing any safety gate.
- **Reference:** `docs/SWARM_V2.1_SPEC.md`, `docs/CONSTITUTION.md`.

### P1-13 - Missing end-to-end operator guide for a new user

- **Status:** resolved — [`OPERATOR_GUIDE.md`](OPERATOR_GUIDE.md) covers install → quick path → Usage gate → direct recipe → v2 → recovery
- **Area:** onboarding / documentation
- **Evidence:** `docs/INSTALL.md` explains setup and individual commands, but there is no single runbook that walks a new operator from “nothing installed” to “task finished safely”. Current questions include:
  - When does the dashboard open?
  - What exactly happens when a task is assigned through the OpenCode skill?
  - When is it panel, wave, War Room or meeting?
  - Which command comes next after install, plan, approval, failure or quota exhaustion?
  - What is v1 versus v2, and which one should a beginner use?
- **Command-flow gap (current code):** `.agents/SKILL.md` still tells the agent to run `python .agents/planner.py --goal "..." --submit` and then wait for approval. Under P0-06 this changes: the planner always runs, a validated plan may auto-start, and only the conditional Usage gate can block. Also, current `--submit` only writes the plan/token and prints a URL; it does not start the dashboard or wait. The guide must describe the target flow, not the old plan-approval flow.
- **README inconsistency:** `README.md` “First task” shows `--decision approve` (headless plan approval) while the dashboard section shows `--wait --open`. Both will be obsolete under P0-06; the guide must present the new Usage-approval rule instead.
- **Risk:** a new user can install the repository and still not know the safe sequence; they may miss a Usage threshold, mistake E2 for the full War Room, run meeting without four reviewers, misread standby agents as active, or not know how to recover from verify/reviewer/budget failures.
- **Required guide:** one `docs/OPERATOR_GUIDE.md` with a linear narrative and a decision tree:
  1. **Install:** clone or copy `.agents/` + `.opencode/`; Python/Git/OpenCode checks.
  2. **Connect models:** `opencode auth list`, `opencode models`, `scout --write --verify` (free-only default), explain `config.json`.
  3. **Health gate:** `preflight --live`; what PASS, cooldown, quota and unavailable mean.
  4. **Load into OpenCode:** where the skill lives, how discovery works, `sync_skill.py`, and what changes when the user names `adaptive-swarm-pipeline` explicitly versus asking a normal coding question.
  5. **First safe run:** planner always runs; explain classifier Level 0–3 (task scope), execution tiers E1–E4, and `verify_command`.
  6. **Approval:** plan preview is not an approval gate; only Usage approval is used, and only when the quota/cost threshold fires. Show the exact screen, estimate, and what happens when no approval is needed.
  7. **Execution trace:** E1–E4, planner P1–P3, route/model selection, coder draft, draft verify, reviewer gate, apply, machine verify, rollback/report; list files and ledger events a user can inspect.
  8. **Modes:** E1 coder-only, E2 coder + one reviewer, E3 coder + additional reviewer(s), E4 meeting; War Room is a view, not a mode; meeting is explicit and fail-closed.
  9. **Failure recovery:** quota/`ReviewerUnavailable`, verify failure, reviewer reject, budget stop, pause/resume/stop, stale plan, rollback and cleanup.
  10. **New situations:** new project, new model/provider, new task type, new skill, switching v1/v2, changing adaptive flags.
  11. **Daily checklist:** before run, during run, after run, and what evidence to keep.
- **Style requirement:** beginner-readable, command-first, every command followed by expected output/state and “what to do if it fails”; include one complete worked example and one failure example.
- **Exit criterion:** a person with no prior knowledge of this repository can follow the guide from a fresh project to a verified result without asking for help, and can explain when the dashboard, War Room and meeting should be used.
- **Dependencies:** keep `docs/INSTALL.md` as the short setup reference; link both docs rather than duplicating commands.

### P1-14 — Extracted capabilities are not uniformly usable across E1–E4

- **Status:** resolved — one capability bundle is injected for every execution tier
- **Area:** research integration / capability wiring
- **Batch 1 progress (2026-11-06):** `capability_registry.py` defines `tiers`, `roles`, `wiring` and `report()`.
- **Uniform wiring evidence (2026-11-07):** `v2/capability_runtime.py` builds repo-map + ACI + lessons + event page + bounded memory; `ExecutionEngine` injects it into every E1–E4 work order and records `capability.context`. `test_capabilities_uniform.py` 23/23 PASS.
- **Owner question:** forget whether the upstream repos are usable. The real question is: the **skills/ideas extracted from those repos and put into our skill** — are they usable now, and can the coder, reviewer and meeting all use them across E1–E4?
- **Answer recorded:** **Not yet uniformly.**
  - The upstream repos are reference-only.
  - The extracted primitives are implemented at different levels.
  - E1–E4 does not exist as a runtime yet; current code still has v1 panel/wave/meeting plus a separate v2 planning/execution path.
  - A shared `SKILL.md` does **not** mean every runtime can use every capability. The primitives are split across v1 and v2.
- **Legacy matrix before uniform wiring:** (kept as historical evidence; the v2 runtime now injects all five primitives for E1–E4 via `capability_runtime.py`)
  | Extracted capability | Source idea | Current wiring | Coder can use? | Reviewer can use? | Meeting can use? | Verdict |
  |---|---|---|---|---|---|---|
  | Repo map | aider | v2 `planning.py` prompt, `REPO_MAP=on`; not v1 planner | indirect only if planner uses v2 | indirect through plan | no (v1 meeting path) | **partial** |
  | Read-only ACI | SWE-agent | v2 `execution.py` validation + CLI; not coder tool loop | no, not wired into coder context | no, only post-edit validation | no | **validation only** |
  | Failure lessons | mem0 | v2 planning hints + v2 execution capture; not v1 | indirect through plan | indirect through plan | no | **partial** |
  | Memory budget | letta-code | module/CLI; not every live runner path | no | no | no | **not live** |
  | Event IDs/pagination | OpenHands | event runtime; not every runner | no | no | partial observability | **partial** |
  | Speaker selection | AutoGen concept | tested primitive only | no | no | not wired | **not wired** |
- **Why the tiers do not automatically inherit them:**
  - E1/E2 as currently imagined would use the v1 planner/recipe path, which does not load v2 repo-map/lessons.
  - E4 meeting uses the v1 meeting path, which does not load the v2 planning/ACI primitives.
  - v2 execution always uses four reviewers; it is not the E1/E2 path.
  - There is no single runtime that all four tiers share today.
- **Required work:**
  1. Choose one runtime for E1–E4 (recommended: v2) or build a bridge from v1 meeting to the v2 capabilities.
  2. Add a capability registry with explicit `wired_in: [E1, E2, E3, E4]` and `usable_by: [coder, reviewer, meeting]`.
  3. Wire repo map + lessons into the planner used by every tier (planner is mandatory on skill entry).
  4. Wire ACI read-only context into the coder prompt/tool loop; keep edit validation as a separate execution gate.
  5. Wire memory/event runtime into all live runners.
  6. Only advertise a capability in `SKILL.md` for tiers where it is wired and tested.
- **Exit criterion:** every claimed capability in every claimed tier has an adapter test or benchmark; a capability that is not wired is explicitly listed as unavailable. The operator guide must say “reference-only” for the upstream repos and “partial” for the extracted primitives.
- **Risk:** claiming “the skill works for E1–E4” today would be false. The current status is **partial v2 wiring, no unified tier runtime**.
- **Related:** P0-07, `docs/RESEARCH_BACKLOG.md`, `docs/ADAPTIVE_TOOLKIT.md`, `docs/SHADOW_PILOT_REPORT.md`, `C:\Users\tuank\research\repos`.

---

## P2 - polish / product experience

### P2-01 - notifications not wired to M4

- **Status:** resolved - safe v2 event alerts wired to Telegram/Discord/generic webhooks
- **Evidence:** `v2/alerts.py` emits `HANDOFF_READY`, `USAGE_WAITING`, `BUDGET_OVERRUN` and `CRASH`; every message is passed through `redact_secrets` (API keys and absolute/sensitive paths). `test_v2_alerts.py`: 5/5 PASS.
- **Area:** operations
- **Need:** Discord/Telegram on pause/quarantine/handoff; no secrets in message.

### P2-02 - `/v2` page is standalone, not integrated in main nav

- **Status:** resolved - main dashboard nav, recent handoff runs and web diff preview wired
- **Evidence:** dashboard sidebar links to `/v2`; `/v2` links back and shows recent handoff runs, per-phase spend and a diff preview button backed by `/api/v2/runs/<id>/diff`. `test_v2_web_ui.py`: 22/22 PASS (including Node JS syntax check).
- **Area:** UI
- **Need:** nav entry, run history, budget sparkline, phase detail, diff links.

### P2-03 — no auto-merge

- **Area:** delivery
- **Decision:** keep manual by default; if added, require separate sign-off and branch protection.
- **Need:** documented one-command merge/rollback UX.

### P2-04 - worktree cleanup/retention policy

- **Status:** resolved - `v2 prune [--days N] [--force]` implements worktree/branch retention
- **Evidence:** `v2/prune.py` removes completed or stale isolated worktrees and `swarm/*` branches, skips active runs, keeps manifest/checkpoints/events/handoff evidence, and supports `--dry-run`. `test_v2_prune.py`: 10/10 PASS.
- **Area:** storage
- **Need:** retention days, archive before remove, disk quota, stale branch cleanup.

### P2-05 — OS/container sandbox

- **Area:** security
- **Evidence:** Git worktree protects files, not processes; tests can touch OS.
- **Need:** optional container/VM runner; explicitly not a v2.1 claim.

### P2-06 — release/version story

- **Area:** release
- **Evidence:** `README` says v2.1.0 but v1 remains default; v2.1.0 is opt-in.
- **Need:** clear “stable v1 / preview v2.1” matrix; migration and rollback instructions.

### P2-07 - package excludes machine config by design

- **Status:** resolved - `v2 doctor` provides the first-run preflight
- **Evidence:** `python .agents/v2/run.py doctor` checks Git/worktree cleanliness, OpenCode CLI/model availability (DeepSeek/GLM/Nemotron free entries), Spec Kit CLI/config and prints a green/red table or JSON. `test_v2_doctor.py`: 17/17 PASS.
- **Area:** onboarding
- **Evidence:** zip excludes `config.json`, as intended.
- **Need:** first-run wizard/doctor that explains `scout`, provider auth, 9router, and expected free/paid state.
- **Related:** the narrative runbook gap is tracked in **P1-13**.

### P2-08 - live usage/cost dashboard

- **Status:** resolved - `/v2` spend reconciliation table is live
- **Evidence:** `run_detail` exposes estimated/used/remaining requests and cost, actual tokens and per-phase usage; the page renders it beside handoff runs and the diff preview. `test_v2_web_ui.py`: 22/22 PASS.
- **Area:** UI
- **Need:** per-phase actual vs estimated, provider; tokens/cost; requests; remaining reserve.

### P2-09 — Spec Kit ecosystem ideas

- **Area:** product
- **Candidates:** presets (free-only, strict-security), extensions (custom verify gate), catalogs, bugfix/assessment workflows, multi-agent install/switch UX.

---

### P2-10 - research shortlist: adaptive open-source patterns

- **Status:** Phase 0-6 adaptive integration implemented; live pilot/wiring of some modules pending
- **Area:** research
- **Reference:** [`docs/RESEARCH_BACKLOG.md`](RESEARCH_BACKLOG.md)
- **Top picks after P0:** aider repo map, SWE-agent ACI, mem0-style failure memory, Letta-style tiered memory.
- **Rule:** extract mechanisms only; do not add DSPy/Mem0/Letta/LiteLLM/AutoGen as runtime dependencies without separate approval.

### P2-11 - Adaptive Phase 0-6 integration status

- **Status:** implemented + tested; shadow pilot passed; live wiring/pilot pending
- **Commits:** `29002e8`, `0892f49`, `8c57b93`, `85bc3b2`, `102b612`, `d6631d5`
- **Implemented:**
  - feature flags (`off|shadow|on`), default off
  - Phase 1 repo map with PageRank, token cap and secret filtering
  - Phase 2 windowed read-only ACI + pre-apply edit validation
  - Phase 3 failure lessons with signature upsert/dedupe and redaction
  - Phase 4 memory caps, read-only constitution, lock and corruption recovery
  - Phase 5 event IDs, pagination and rate-limit/user-confirmation states
  - Phase 6 `adaptive-map` / `adaptive-lessons` CLI and synced skill docs
- **Usefulness:** better target-file selection, safer edits, less repeated failure, bounded memory, safer replay/control.
- **Still open:**
  - live shadow pilot has not been run
  - `MEMORY_BUDGET` and `EVENTS` are module/CLI-ready but not wired into every live runner path
  - repo map is Python-AST first; tree-sitter and tags cache deferred
  - ACI submit gate/tool bundles are deferred
  - macOS CI runner has been queued/cancelled
- **Tests (local):** 34 unittest discover; Adaptive 42/42; M1 43/43; M2 38/38; M3 24/24; M5 14/14; P0 15/15; Phase 4 full 36/36; Phase 6A 72/72; skill 16/16.
- **Shadow pilot:** PASS with all flags `shadow`; see [`SHADOW_PILOT_REPORT.md`](SHADOW_PILOT_REPORT.md).
- **Live free-model pilot:** planning PASS; execution blocked by free-tier quota exhaustion; see [`LIVE_PILOT_REPORT.md`](LIVE_PILOT_REPORT.md).
- **Safety:** no changes to `safe_writer`, worktree isolation, budget caps or `verify_command`; ACI has no write path.

---

## Spec Kit pending checklist

When the repo is installed, inspect and record:

- [ ] `spec-driven.md` — artifact lifecycle and review gates
- [ ] `.specify/memory/constitution.md` — project principles
- [ ] `templates/` — spec/plan/tasks shape and required sections
- [ ] `src/specify_cli/` — command/registration model
- [ ] integration `opencode` — invocation syntax and command installation
- [ ] `converge` behavior — what counts as converged
- [ ] extension/preset/catalog schema — what is reusable without runtime dependency
- [ ] license/attribution obligations

## Suggested fix order

1. **P0-02 + P0-03**: actual usage + provider capability registry.
2. **P0-01 + P1-03**: one live free phase with real OpenCode adapter.
3. **P0-05 + P1-01 + P1-02**: crash/resume and overnight soak.
4. **P1-05 + P1-06**: Spec Kit bridge + constitution/converge.
5. **P1-09 + P1-11**: stop v1 planner from silently choosing paid models; lock plan state.
6. **P1-07**: AgentRuntime interface for future CLIs.
7. **P0-06**: replace plan approval with conditional Usage approval across every entry point; quick path stays approval-free.
8. **P0-07**: implement E1–E4, P1–P3 and E2 lightweight tracking.
9. **P1-13**: write the beginner end-to-end operator guide before inviting new users.
10. **P1-14**: publish the extracted-capability × E1–E4 matrix; wire only what is proven, and mark unwired capabilities unavailable.
11. **P2**: UI/notifications/release polish.

## Decision log

- 2026-11-04: v2.1.0 packaged as opt-in; v1.0.5 remains default until P0-01 and P1-01 pass.
- 2026-11-04: Spec Kit is a process/artifact layer, not a replacement for v2 worktree/budget/verify.
- 2026-11-04: 9router is treated as a provider gateway; capability/cost metadata is a required gap, not a future nice-to-have.
- 2026-11-05: Phase 0-6 adaptive modules landed with flags default off; shadow pilot passed. Live free-model execution is blocked by free-tier quota exhaustion, not a code failure.
- 2026-11-06: Recorded P1-13: a new operator needs one end-to-end runbook covering install, OpenCode skill invocation, dashboard/War Room, meeting selection, approvals, execution trace and failure recovery.
- 2026-11-06: Promoted approval inconsistency to **P0-06** after owner clarification. Plan approval is removed as the default human gate; the planner runs automatically. Only **Usage approval** for quota/cost is kept, conditionally triggered in the Usage tab. Current `job.py` over-gates quick tasks while direct `recipe_c` under-gates expensive ones; the new policy must be enforced at every entry point.
- 2026-11-06: Recorded **P0-07**: four execution tiers (E1 coder-only, E2 coder+reviewer, E3 coder+additional reviewer(s), E4 meeting), three planner levels (P1=1, P2=2, P3=3), mandatory planner on skill entry, and lightweight E2 run tracking outside War Room.
- 2026-11-06: Corrected **P1-14**: the question is not whether the upstream repos are drop-in skills, but whether the **extracted capabilities** (repo map, ACI, lessons, memory, events, speaker selection) are wired for coder, reviewer and meeting across E1–E4. Current answer: partial v2 wiring, no unified tier runtime, and meeting does not inherit v2 planning/ACI.
- 2026-11-06: **P0-06 closed** after owner-confirmed default thresholds (8 requests / $0.50). v1 quick path, direct recipe dashboard wait, v2 `usage-approve`, `/v2` Approve Usage, docs and full regression are complete.
- 2026-11-06: Batch 1 landed `tier_policy.py`, `capability_registry.py`, `generate_council`, E2 compact timeline and tier/planner events. P0-07/P1-14 moved to in-progress pending skill-boundary guard and E3/E4 live parity.
- 2026-11-06: Batch 2 landed `model_registry.py` and `spend_reconcile.py`; planner is free-only by default (`--allow-paid` opt-in). P0-02/P0-03/P0-04/P1-08 remain in progress because the live per-call hook and full 9router metadata are not yet available.
- 2026-11-07: Batch 3 added `run_lock.py` and real child-process transaction crash recovery; P1-11 resolved, P1-04 resolved, P0-05 in progress.
- 2026-11-07: Batch 4 added `v2/converge.py`, `v2/run.py converge`, a my-project bridge pilot and `ClaudeCodeRuntimeStub`; P1-06/P1-07 resolved, P1-05 in progress.
- 2026-11-07: Batch 5 live free-model v2 phase passed: `prepare -> plan -> usage-approve -> execute -> verify -> checkpoint -> handoff`; main unchanged, worktree changed, 4 reviewer approvals, checkpoint `c7ac85c...`. P0-01/P1-03/P1-13 resolved. Full local regression: 637 checks/assertions PASS across the listed suites.
- 2026-11-07: E3/E4 v2 parity added: four independent wave reviewers; E4 blackboard meeting with Round 2 conflict path. `test_v2_parity.py` 11/11 PASS. P0-07 resolved.
- 2026-11-07: Real v2 phase process kill (`taskkill /F`) → `v2 recover` → re-execute → handoff passed; `test_phase_process_crash.py` 10/10 PASS. P0-05 resolved.
- 2026-11-07: Overnight soak completed **459 cycles** (`ok=True` each) and stopped cleanly; P1-01/P1-02 resolved.
- 2026-11-07: Provider fallback metadata, prompt secret sanitization, context-window guard and uniform E1–E4 capability bundles landed. P0-03/P1-08/P1-10/P1-14 resolved; all P0 items are now resolved.
- 2026-11-07: Spec Kit adoption closed end-to-end on real Git `my-project`: bridge → Usage approval → v2 worktree execution → verify 0 → checkpoint → handoff → converge. P1-05/P1-12 resolved; see `docs/SPEC_KIT_ADOPTION.md`.

- 2026-11-07: P2 operator batch: `/v2` nav + spend/handoff diff, safe v2 alerts, `v2 doctor` and `v2 prune` landed; P2-01/P2-02/P2-04/P2-07/P2-08 resolved.

## Batch commit ledger

| Batch | Commit | Evidence / status |
|---|---|---|
| P0-06 | `81f2b84` | Usage approval complete (resolved) |
| 1 | `165b8f8` | E1–E4/P1–P3 policy, capability registry, E2 timeline; P0-07/P1-14 in progress |
| 2 | `0c493d1` | Model registry + spend reconciliation; P1-09 resolved; P0-02/03/04/P1-08 in progress |
| 3 | `4bb8161` | Repo lock + crash transaction recovery; P1-11/P1-04 resolved; P0-05 in progress |
| 4 | `6740e82` | Converge + Spec Kit bridge pilot + runtime stub; P1-06/P1-07 resolved; P1-05 in progress |
| 5 | `fa2a359` | Live free-model v2 pilot; P0-01/P1-03/P1-13 resolved |
| Deep wiring | `5475b59` | Per-call spend reconciliation, tool metadata filter, soak runner PID 6236; P0-02/P0-04 resolved; soak in progress |
| Providers | `09788c2` | Offline fallback metadata + secret sanitization; P0-03/P1-10 resolved |
| Context | `d863ae6` | Context-window guard + overflow trim; P1-08 resolved |
| Capabilities | `4fab16b` | Uniform E1–E4 capability bundle; P1-14 resolved |
| Spec Kit | `b2af571` | Real Git my-project signed execution + adoption doc; P1-05/P1-12 resolved |
| P2 UI | `ec43e01` | `/v2` nav, live spend table, handoff history + diff; P2-02/P2-08 resolved |
| P2 UI tokens | `a71ae51` | Estimated vs actual token reconciliation; P2-08 complete |
| P2 Alerts | `4f2af99` | Safe v2 event notifications with redaction; P2-01 resolved |
| P2 Doctor | `61491d2` | `v2 doctor` environment preflight; P2-07 resolved |
| P2 Prune | `dc152f6` | `v2 prune` worktree/branch retention; P2-04 resolved |
