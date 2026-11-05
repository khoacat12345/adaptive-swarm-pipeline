# Open Problems — Adaptive Swarm Pipeline

> Last updated: 2026-11-04
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

- **Status:** open
- **Area:** M5 pilot
- **Evidence:** M1–M5 acceptance tests use fake planner/coder/reviewers; `execute --free` has not completed a real live phase.
- **Risk:** Adapter/tool-calling/session issues can hide until a real model is used.
- **Exit criterion:** one real free-model phase completes:
  `prepare → plan → signoff → execute → verify → checkpoint → handoff`,
  with main worktree unchanged.
- **Suggested first target:** one small target file, no network in verify, one phase.

### P0-02 — actual spend/cost is not reconciled

- **Status:** open
- **Area:** budget/cost
- **Evidence:** `ExecutionSeat.cost_usd` is an estimate; `BudgetController` settles the estimated cost, not measured provider billing. `pricing` is `null` for many catalog entries.
- **Risk:** a paid run can stay under the estimated budget while actual provider spend is higher.
- **Exit criterion:** after each call, actual tokens/cost are read from OpenCode stats or 9router usage, compared with the reservation, and an overrun stops the run before the next call.
- **Fallback:** if no usage source exists, paid models remain blocked; document the limitation explicitly in `/v2`.

### P0-03 — 9router/provider capability data is incomplete

- **Status:** open
- **Area:** providers
- **Evidence:** current `config.json` reports `9router: unavailable`; catalog entries have `pricing: null`; some providers expose only a generic model ID.
- **Risk:** cross-family checks, tool-calling checks, context limits, cost caps, and fallback selection can be wrong.
- **Exit criterion:** a capability registry exists with:
  `model_id`, provider, family, context window, tools, JSON mode, input/output price, auth/health, quota source.
- **Need:** decide whether 9router remains a local gateway or a shared/team gateway.

### P0-04 — live paid-model guard is only an estimate gate

- **Status:** open
- **Area:** cost safety
- **Evidence:** paid models are blocked when `cost_usd == 0`; with `--paid-cost N`, each call reserves `N` but actual usage is not verified.
- **Risk:** a wrong `N` under-reserves.
- **Exit criterion:** either real usage settlement (P0-02) or a hard provider-level spend cap enforced at 9router/gateway.

### P0-05 — long-run crash/resume is not process-tested

- **Status:** open
- **Area:** overnight reliability
- **Evidence:** state transitions and checkpoint records are unit-tested; there is no test that kills the runner process mid-phase and resumes it in a new process.
- **Risk:** duplicate writes, stuck locks, or lost checkpoints under a real crash.
- **Exit criterion:** kill runner after write/before verify and after verify/before checkpoint; new process resumes safely; no duplicate file version.

---

## P1 — before daily use / release-candidate confidence

### P1-01 — 8–10 hour soak not executed

- **Status:** open
- **Area:** M5 soak
- **Evidence:** M5 uses a multi-phase synthetic soak and fake clock; no wall-clock long run.
- **Risk:** memory/disk/log growth, sleep/resume, auth expiry and quota drift are untested at night.
- **Exit criterion:** one night run with phase budget caps, periodic heartbeat, disk/log checks and a morning handoff report.

### P1-02 — Windows sleep/lock/session behavior

- **Status:** open
- **Area:** overnight reliability
- **Evidence:** worktree/verify are tested on Windows in CI; active overnight sleep/power policy is not.
- **Risk:** laptop sleeps, OpenCode session dies, or network drops at 03:00.
- **Exit criterion:** documented power policy + resume test; runner pauses safely or prevents sleep explicitly.

### P1-03 — real OpenCode adapter coverage

- **Status:** open
- **Area:** M3.5
- **Evidence:** `WorkersCoderAdapter`/`WorkersReviewerAdapter` are unit-tested with fakes; no live provider run.
- **Risk:** tool/JSON/format differences between providers.
- **Exit criterion:** run one coder + four reviewer calls with free models; validate diff, verdict and budget accounting.

### P1-04 — CleanArchitect seat is defined but not wired

- **Status:** open
- **Area:** execution roster
- **Evidence:** `cleaner` is accepted by `ExecutionEngine.execute` but not executed.
- **Risk:** spec says optional cleanup; implementation silently ignores it.
- **Exit criterion:** either implement post-verify cleanup + full re-verify, or remove the parameter and document it as deferred.

### P1-05 - Spec Kit bridge is partially implemented

- **Status:** in progress (P0 bridge landed; real Spec Kit pilot pending)
- **Area:** process/integration
- **Evidence:** local `C:\Users\tuank\my-project` has Spec Kit 1.3.0 with the `opencode` integration. `v2/tasks.py` and `v2/speckit_bridge.py` now convert canonical `tasks.yaml` to a validated plan.
- **Needed:** run one real Spec Kit feature through the bridge, then execute the signed plan.
- **Exit criterion:** a bridge converts a Spec Kit `spec.md` + `plan.md` + `tasks.md` into a valid `MasterPlan.json`, then runs through v2 sign-off and execution.

### P1-06 — constitution / converge are missing

- **Status:** open
- **Area:** process
- **Evidence:** v2 has plan hash/sign-off, but no project constitution or explicit spec↔code convergence phase.
- **Risk:** drift between spec, plan and implementation is discovered late.
- **Exit criterion:** `constitution.md` is used in planning; `v2 converge` reports drift as a machine-readable artifact.

### P1-07 — runtime is OpenCode-only

- **Status:** open
- **Area:** architecture
- **Evidence:** `v2/runtime.py` now defines `AgentRuntime`/`RuntimeRegistry`; current execution still uses OpenCode workers.
- **Risk:** future Claude Code/Codex/Gemini support still needs concrete adapters and headless compatibility tests.
- **Exit criterion:** `AgentRuntime` protocol with OpenCode adapter; one stub second runtime tested.

### P1-08 — provider fallback and cross-family are not tied to a capability registry

- **Status:** open
- **Area:** routing
- **Evidence:** v2 selects free seats from config and checks family, but does not verify tool-calling or context window.
- **Risk:** a free model can pass family checks but fail tool calling mid-phase.
- **Exit criterion:** role selection requires `supports_tools` (and required context) unless explicitly overridden.

### P1-09 — v1 planner still prefers paid models

- **Status:** open
- **Area:** v1 cost
- **Evidence:** `planner._models()` begins with `ROUTES["W4"]` main/escalate (paid) before free fallbacks; `planner.py` CLI has no `--free`.
- **Risk:** “free-only” intent can be violated by the planner before v2 sees it.
- **Exit criterion:** `planner.py --free` or config-driven free-only default; paid requires explicit flag and cost estimate.

### P1-10 — 9router trust boundary and secret handling

- **Status:** open
- **Area:** security
- **Evidence:** gateway model paths are not yet exercised; prompts/code may leave the machine through 9router.
- **Risk:** secret leakage, provider logging, or untrusted gateway.
- **Exit criterion:** documented data-flow, local-only mode, secret scan before prompt, gateway auth, and deny-by-default remote endpoints.

### P1-11 — concurrent runs / stale plan state

- **Status:** open
- **Area:** concurrency
- **Evidence:** v1 plan state is a single shared `.agents/tmp/plan.json`; a second session/planner can replace it. v2 has a run lock only inside its own state model.
- **Risk:** two sessions overwrite each other’s plan/approval.
- **Exit criterion:** per-repo lock is enforced before planner/run; stale lock detection; no shared mutable plan.
- **Note:** this already happened during M3 planning: a separate planner process was writing a different plan.

### P1-12 - Spec Kit adoption plan

- **Status:** approved; P0 in progress
- **Area:** process/integration
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

---

## P2 - polish / product experience

### P2-01 — notifications not wired to M4

- **Area:** operations
- **Need:** Discord/Telegram on pause/quarantine/handoff; no secrets in message.

### P2-02 — `/v2` page is standalone, not integrated in main nav

- **Area:** UI
- **Need:** nav entry, run history, budget sparkline, phase detail, diff links.

### P2-03 — no auto-merge

- **Area:** delivery
- **Decision:** keep manual by default; if added, require separate sign-off and branch protection.
- **Need:** documented one-command merge/rollback UX.

### P2-04 — worktree cleanup/retention policy

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

### P2-07 — package excludes machine config by design

- **Area:** onboarding
- **Evidence:** zip excludes `config.json`, as intended.
- **Need:** first-run wizard/doctor that explains `scout`, provider auth, 9router, and expected free/paid state.

### P2-08 — live usage/cost dashboard

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
7. **P2**: UI/notifications/release polish.

## Decision log

- 2026-11-04: v2.1.0 packaged as opt-in; v1.0.5 remains default until P0-01 and P1-01 pass.
- 2026-11-04: Spec Kit is a process/artifact layer, not a replacement for v2 worktree/budget/verify.
- 2026-11-04: 9router is treated as a provider gateway; capability/cost metadata is a required gap, not a future nice-to-have.
- 2026-11-05: Phase 0-6 adaptive modules landed with flags default off; live shadow pilot still required before enabling `on`.
