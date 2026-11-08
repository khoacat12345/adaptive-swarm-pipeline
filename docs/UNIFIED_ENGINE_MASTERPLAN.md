# Unified Engine Master Plan — Zero-User / V2-Only / V3 Release

> Revision: **2.2 — tier/planner/CLI hardening before the War Room**
> Status: **in progress**; Phase 1 and the `swarm` CLI are implemented and
> hardened, the unified War Room and release gates are still open.
> Last updated: 2026-11-08
>
> Companion audit: [`V1_FEATURE_PARITY.md`](V1_FEATURE_PARITY.md)
>
> This revision supersedes the compatibility-heavy plan. There are currently no
> users, no production data and no external CLI contract, so the goal is a
> clean, stable v2-only product rather than a long dual-engine migration.

---

## 1. Executive decision

### Target

```text
v1.0.5 = frozen fallback / test oracle / emergency tag
v2     = the only product engine
swarm  = the only new-user entrypoint
War Room = the only dashboard
v3.0.0 = first unified production release
```

### What changes from the previous plan

- No v1 CLI compatibility wrappers.
- No `job.py`/`recipe_c.py` routing layer.
- No dual dashboard navigation.
- No migration of v1 event/state formats.
- No requirement to preserve old JSON output.
- No public `v2.1.0` tag rewrite.

### What does not change

- v1 features are audited and either ported or explicitly dropped.
- v2 remains worktree-first.
- `safe_writer` remains the only writer.
- Main changes only through explicit `v2 merge`.
- Usage, budget, verify and secret-scan gates remain mandatory.
- v1.0.5 remains available as rollback.

The feature audit is the release gate:
[`V1_FEATURE_PARITY.md`](V1_FEATURE_PARITY.md).

---

## 2. Why zero-user changes the architecture

The hardest and riskiest part of the previous plan was compatibility:

```text
old CLI flags
old JSON output
old plan/job state
old dashboard API
old event schema
old direct-write behavior
```

With zero users:

- We can drop old command shapes.
- We can change output/state schemas.
- We can use one dashboard.
- We can use one engine.
- We can make `swarm` the official entrypoint.
- We can remove duplicate concepts instead of preserving them.

This is not permission to lose capabilities. It is permission to lose **old
implementation shapes** while keeping or improving the underlying capability.

### Rule

> Remove compatibility code freely.
> Remove a user-facing capability only with an explicit owner decision.

---

## 3. Current baseline and known gaps

### 3.1 Existing strengths

- v2 Git worktree isolation.
- v2 budget reservation/settlement and usage reconciliation.
- Usage approval bound to plan/estimate hashes.
- Four-reviewer E3 and E4 blackboard meeting.
- Adaptive capability bundle.
- Explicit merge and rollback.
- Presets, alerts, doctor, prune.
- `/v2` spend, handoff and diff page.
- 48 test files pass locally.

### 3.2 Gaps that block v3

| Gap | Evidence | Required outcome |
|---|---|---|
| ~~E1/E2 policy not wired into v2~~ | **wired 2026-11-08**; `test_v2_tiers.py` 40/40 | tier-aware execution |
| ~~No planner adapter~~ | **added** `v2/planner_adapter.py`; 38/38 adapter checks | goal → v2 plan |
| ~~No terminal `swarm`~~ | **added** `v2/swarm_cli.py` + wrappers; 44/44 CLI checks | one-command CLI |
| ~~Runtime tier override bypassed Usage~~ | **closed** `assert_tier_matches_signoff` | tier must match the signed plan |
| P2/P3 planner council not executed | adapter proposes the level; only one deterministic proposal | execute 2/3 proposals |
| No unified War Room | v1 `/` and v2 `/v2` are separate; default 8080 | one dashboard at 8501 |
| No full v1-vs-v2 parity | existing parity is E3/E4-centric | fixture-level parity suite |
| Status/history split | v1 ledger vs v2 per-run events | one read-only history |
| Release tag old | `v2.1.0` points to old commit | new v3 tag, no retag |

### 3.3 Progress log

**2026-11-08 — Phase 1 and the CLI half of Phase 2 are implemented.**

- E1–E4 runtime: `v2/execution.py` resolves a tier profile (E1 no reviewer,
  E2 security only, E3 four-role wave, E4 meeting). Old plans default to E3.
  E1 is explicit low-risk opt-in, blocks sensitive targets and keeps secret
  scanning mandatory. Evidence: `python .agents/tests/test_v2_tiers.py`
  (40/40), including `run.py` CLI guards for E1 opt-in and E2's security seat.
- Usage estimate follows the tier, so E1/E2 do not inherit the E3 wave reason.
- Planner adapter: `v2/planner_adapter.py` turns a goal, v1 plan or existing
  v2 plan into a validated MasterPlan with targets, verify commands and a
  proposed tier/planner. Evidence: `python .agents/tests/test_planner_adapter.py`
  (38/38).
- One-command CLI: `.agents/v2/swarm_cli.py` plus `swarm.cmd` / `swarm.sh`
  provides `doctor`, `planner`, `run`, `status`, `handoff`, `merge`. `run`
  does doctor → plan → prepare → submit → Usage gate → signoff → execute →
  verify → handoff. Evidence: `python .agents/tests/test_swarm_cli.py`
  (44/44).
- Still open: P2/P3 councils, the separate unified orchestrator/contracts,
  the 8501 War Room, fixture-level parity, live free-model smoke and soak.

**2026-11-08 — Hardening pass on the same slice (no new test files).**

- Tier integrity: `execution.assert_tier_matches_signoff` rejects a runtime
  `--tier` that differs from the signed plan, because the Usage estimate is
  hash-bound to the plan tier; `tier.selected` now records both `tier` and
  `signed_tier`, and `ExecutionResult` exposes `low_risk_opt_in`.
- E1 policy: the sensitive-path class now also covers token/password files,
  `.npmrc`, `.pypirc`, `.netrc`, `.ssh`, `.gnupg`, keystores and certificate
  files; secret scanning and the verify requirement are unchanged and
  mandatory.
- Planner adapter: v1 `allowed_paths` scopes are preserved (test paths are
  still excluded), job-level `verify_command` is honoured, and
  `--allow-new-files` lets a goal create a file that does not exist yet.
- `swarm`: `--version` (2.2.0) and `--max-cost` / `--max-requests` /
  `--max-wall-seconds`. Caps only lower the plan envelope before the Usage
  decision and down-scale phase allocations so `validate_plan` still passes.
- Evidence unchanged and re-run green: `test_v2_tiers.py` 40/40,
  `test_planner_adapter.py` 38/38, `test_swarm_cli.py` 44/44, `test_v2_m3.py`
  25/25. Remaining gaps are the same as listed above.

---

## 4. Product architecture

```text
                        Operators
          swarm CLI / OpenCode / War Room
                          |
                          v
                  UnifiedOrchestrator
      preflight -> tier -> planner -> usage decision
                          |
                          v
                       V2 Runner
          worktree + budget + capability bundle
                          |
                          v
           verify -> checkpoint -> handoff
                          |
              +-----------+-----------+
              v                       v
        explicit v2 merge       Telegram/War Room
              |
              v
          main branch
```

### 4.1 One engine

There is no second execution engine in the product. v1 execution remains only
in the frozen `v1.0.5` tag/reference for parity testing.

### 4.2 One entrypoint

```bash
swarm "fix the failing add() test"
```

Optional modes:

```bash
swarm "task" --preset free-only
swarm "task" --preset strict
swarm "task" --tier E2
swarm "task" --merge        # explicit, never default
swarm "task" --dry-run
swarm status <run_id>
swarm doctor
```

### 4.3 One dashboard

```text
http://127.0.0.1:8501
```

One page for:

- run progress;
- tier/planner;
- spend/tokens;
- Usage approval;
- E4 blackboard;
- handoff diff;
- merge/rollback;
- history.

---

## 5. Non-goals

- Do not remove the `v1.0.5` tag.
- Do not move/retag `v2.1.0`.
- Do not add DSPy/Mem0/Letta/LiteLLM/AutoGen dependencies.
- Do not claim OS/container sandbox isolation.
- Do not auto-merge without explicit operator action.
- Do not support concurrent worktree runs in one repo beyond the lock.
- Do not keep v1 CLI wrappers merely for nostalgia.

---

## 6. Feature parity gate

Before code deletion or v3 release, `V1_FEATURE_PARITY.md` must show:

```text
PORTED
REPLACED with equivalent
DEFERRED approved by owner
DROP approved by owner
```

No unclassified `PORT_REQUIRED` or `DROP_CANDIDATE` may remain.

### Feature priorities

**Must port before v3:**

- quick task path / E1–E2
- planner + clarify
- tier/planner policy
- model routing/fallback
- live provider preflight
- cost/spend reporting
- replay/history
- dashboard run progress
- usage approval
- current Telegram alert behavior
- replay/control operations
- plan inspection

**May be dropped with approval:**

- v1 direct-write behavior
- old plan approval click
- old CLI JSON shapes
- old dashboard shell
- two-way Telegram bot if v2 alerts are enough
- benchmark/eval/profile tooling

**Keep as release evidence:**

- v1 acceptance suites
- v1.0.5 tag
- parity fixtures

---

## 7. Workstreams and phases

### Phase 0 — Baseline and parity audit

**Goal:** freeze current evidence and make the feature matrix authoritative.

#### Tasks

1. Record current `main` SHA and test results.
2. Run all 48 test files sequentially.
3. Review `docs/V1_FEATURE_PARITY.md` with the owner.
4. Mark every `DEFERRED`, `DROP_CANDIDATE`, and `PORT_REQUIRED`.
5. Add a baseline document with reproducible commands.

#### Files

```text
docs/UNIFIED_ENGINE_BASELINE.md
docs/V1_FEATURE_PARITY.md
```

#### Exit gate

```text
[ ] all existing tests pass
[ ] every v1 module classified
[ ] no unapproved capability loss
[ ] owner signs the drop/defer list
```

---

### Phase 1 — Tier-aware v2 engine

**Goal:** make E1–E4 real in v2.

#### 1.1 Define tier runtime profile

Use `tier_policy.TIERS` as source of truth.

Add a profile consumed by execution:

```python
TierRuntimeProfile(
    tier="E2",
    reviewer_roles=("security",),
    planner="P1",
    meeting=False,
)
```

Mapping:

| Tier | Reviewer roles | Planner | Meeting |
|---|---|---|---:|
| E1 | `()` | P1 | no |
| E2 | `("security",)` | P1 | no |
| E3 | all four | P2 | no |
| E4 | all four | P3 | yes |

#### 1.2 Modify `v2/execution.py`

- Resolve `execution_tier` from plan/request.
- Validate only the required reviewer roles.
- Preserve cross-family checks for seats that exist.
- Keep E4 blackboard meeting unchanged.
- Default old plans without a tier to E3, preserving current behavior.
- Reject E1 when the plan touches sensitive paths unless explicit permission.
- Keep `safe_writer` and verify integrity unchanged.

#### 1.3 Modify `v2/run.py execute`

- Accept `--tier` when needed.
- Do not require four reviewer models for E1/E2.
- Keep four reviewers mandatory for E3/E4.
- Reject unknown/unavailable roles fail-closed.

#### 1.4 Tests

```text
test_v2_tiers.py
```

Cases:

- E1: zero reviewer calls; verify still required.
- E2: one reviewer; cross-family check.
- E3: four reviewers.
- E4: meeting round 1/2 + votes.
- E1 sensitive path blocked.
- missing reviewer for E3/E4 blocked.
- old plan default E3.
- capability context still present in all tiers.

#### Exit gate

```text
[x] E1–E4 execute with expected seat counts
[x] E4 existing parity still passes
[x] runtime tier override is rejected when it differs from the signed plan
[x] no v1 files changed
[x] no dashboard changes
```

---

### Phase 2 — Planner, orchestrator, and one-command `swarm`

**Goal:** turn a goal into a complete v2 execution.

#### 2.1 Planner adapter

Create:

```text
.agents/v2/planner_adapter.py   # implemented 2026-11-08
```

The original plan proposed `.agents/unified/planner_adapter.py`; it lives in
the v2 package so it can import the plan schema and tier policy directly. The
API remains `from_goal`, `from_v1_plan`, `adapt_source` and `adapt_file`.

Inputs:

- natural-language goal;
- v1 planner plan (for parity);
- Spec Kit `tasks.yaml`;
- existing v2 `MasterPlan.json`.

Outputs:

- validated v2 plan;
- tier/planner selection;
- source/artifact hashes.

Rules:

- Planner is mandatory for goal-based tasks.
- Use P1/P2/P3 according to tier.
- On planning failure, do not create a worktree/model call.
- Preserve the plan as a reviewable artifact.

#### 2.2 Unified orchestrator

Create:

```text
.agents/unified/orchestrator.py
.agents/unified/contracts.py
```

Flow:

```text
doctor/preflight
  -> classify/normalize request
  -> select tier + planner
  -> generate/validate plan
  -> v2 prepare
  -> submit plan
  -> usage decision
  -> execute
  -> verify/checkpoint/handoff
  -> notification
  -> return UnifiedResult
```

It must not:

- write main directly;
- auto-merge;
- skip Usage gate;
- skip verify;
- call a model before plan/preflight.

#### 2.3 `swarm` CLI

Files:

```text
.agents/v2/swarm_cli.py   # implemented 2026-11-08
swarm.cmd                 # implemented 2026-11-08
swarm.sh                  # implemented 2026-11-08
```

Commands (implemented):

```text
swarm "task"
swarm "task" --tier E2
swarm "task" --preset free-only
swarm "task" --preset strict
swarm "task" --e1-opt-in --tier E1
swarm "task" --dry-run
swarm "task" --merge
swarm "task" --max-cost 0.10 --max-requests 16
swarm --version
swarm planner "task" --out plan.json
swarm status <run_id> | --last
swarm doctor
swarm handoff --run-id <id>
swarm merge --run-id <id>
```

`--merge` is explicit and requires the same path allowlist/verify gates as
`v2 merge`. Budget caps lower the plan envelope before the Usage decision; they
can never raise the estimate, and a runtime `--tier` must match the signed plan
tier (re-plan to change it).

#### 2.4 OpenCode

Keep:

```text
.opencode/commands/swarm.md
.opencode/skills/adaptive-swarm-pipeline/SKILL.md
```

The slash command and terminal command must print the same run id, status,
worktree, handoff paths and next action.

#### 2.5 Tests

```text
test_unified_contracts.py
test_unified_planner.py
test_unified_orchestrator.py
test_swarm_cli.py
```

Cases:

- goal -> plan -> execute on fake models;
- invalid plan;
- usage required/not required;
- free-only/strict;
- non-TTY usage message;
- explicit merge only;
- main unchanged before merge;
- exit codes.

#### Exit gate

```text
[x] one command reaches HANDOFF_READY
[x] no automatic merge
[x] plan and worktree artifacts inspectable
[x] E1/E2 work through the CLI
[ ] separate unified orchestrator/contracts modules
[ ] P2/P3 council execution
```

---

### Phase 3 — Unified War Room

**Goal:** one dashboard at `127.0.0.1:8501`.

#### 3.1 Unified state reader

```text
.agents/unified/state.py
```

- Primary source: v2 run state.
- Optional read-only importer: v1 ledger.
- Normalize status, tier, spend, verify, handoff.
- Never write old ledger.

#### 3.2 API

Read:

```text
GET /api/unified/runs
GET /api/unified/runs/<id>
GET /api/unified/runs/<id>/events
GET /api/unified/runs/<id>/diff
```

Actions:

```text
POST /api/unified/usage/approve
POST /api/unified/merge
POST /api/unified/rollback
POST /api/unified/control
```

Security:

- bind loopback only;
- token/control rules;
- redact secrets;
- size-cap diffs;
- no remote exposure.

#### 3.3 UI

Panels:

1. Run timeline: Planning → Worktree → Executing → Verifying → Handoff.
2. Tier/planner badge.
3. Spend: estimated vs actual, tokens, calls, remaining.
4. Usage approval card.
5. E4 blackboard Round 1/2.
6. Handoff diff + Merge + Rollback.
7. History/replay.

#### 3.4 Compatibility

- Default port 8501.
- `--port 8080` supported for muscle memory.
- `/v2` remains an alias/redirect, not a second product.

#### 3.5 Tests

- mixed history;
- spend projection;
- usage approve;
- meeting transcript;
- handoff diff;
- merge/rollback with temp repo;
- token auth;
- local-only bind;
- JS syntax check.

#### Exit gate

```text
[ ] one URL shows all run data
[ ] E4 meeting visible
[ ] diff/merge/rollback usable
[ ] no second dashboard
```

---

### Phase 4 — Parity and hardening

**Goal:** prove v2 can replace v1 for the features we chose to keep.

#### 4.1 Fixtures

At least three:

1. single-file logic fix;
2. multi-file change;
3. security-sensitive change requiring review.

Run each through:

```text
v1.0.5 reference (test oracle)
v2 via swarm
v2 via swarm --merge
```

Compare:

- final file bytes;
- verify exit;
- main unchanged before merge;
- patch content;
- no duplicate writes;
- tier/planner;
- cost/tokens;
- exit codes.

#### 4.2 Mandatory suites

New:

```text
test_v2_tiers.py
test_unified_contracts.py
test_unified_planner.py
test_unified_orchestrator.py
test_swarm_cli.py
test_unified_dashboard.py
test_v1_v2_parity.py
```

Existing:

```text
V2 DELIVERY / PRESETS / M1 / M2 / M3 / M5 / P0 / PARITY / WEB / ALERTS / DOCTOR / PRUNE
CAPABILITIES UNIFORM
SPECKIT CONVERGE
RESILIENCE
PHASE 4 / 5 / 6
SKILL CONSISTENCY
```

#### 4.3 Live gates

- Free-model live run.
- Crash/resume test.
- Merge/rollback test.
- 8-hour soak or agreed equivalent.
- Windows + Linux/macOS CI.
- Secret/path redaction review.

#### Exit gate

```text
[ ] all deterministic tests pass
[ ] parity fixtures match
[ ] live smoke passes
[ ] soak and crash tests pass
[ ] owner accepts
```

---

### Phase 5 — v3 release and v1 freeze

**Goal:** publish the new default without ambiguity.

#### 5.1 Release version

Use a new version:

```text
v3.0.0
```

Reasons:

- default engine changes;
- CLI surface changes;
- v1 is no longer the product default;
- old `v2.1.0` must not be retagged.

If for some reason v3 remains opt-in, use `v2.2.0` instead. Do not reuse
`v2.1.0`.

#### 5.2 v1 freeze

- Keep tag `v1.0.5`.
- Keep a `legacy/v1.0.5` reference if useful.
- Do not advertise v1 commands to new users.
- Keep v1 tests as historical oracle.
- Remove compatibility wrappers only after parity gate.

#### 5.3 Documentation

Update:

```text
README.md
docs/RELEASE_NOTES.md
docs/OPERATOR_GUIDE.md
docs/INSTALL.md
docs/OPENCODE_SKILL_SETUP.md
docs/V1_FEATURE_PARITY.md
```

Remove “v2 is opt-in” wording only at this phase.

#### 5.4 Release artifacts

- source zip;
- SHA256;
- release notes;
- skill setup;
- rollback instructions;
- feature parity matrix.

#### Exit gate

```text
[ ] v3.0.0 tag points to tested commit
[ ] v1.0.5 remains available
[ ] v2.1.0 untouched
[ ] new-user install uses swarm only
[ ] rollback documented and tested
```

---

## 8. Detailed implementation rules

### 8.1 Main branch protection

- `v2 execute` never writes main.
- `v2 merge` is the only delivery write.
- `swarm --merge` calls the same delivery function.
- Merge requires clean main, base match, path allowlist, verify 0.
- Rollback is `git revert` with a recorded merge commit.

### 8.2 Locking

- `swarm`, `v2 run`, and any future dashboard action acquire `RepoLock`.
- A second command fails with a clear `repo lock busy`, not a partial write.

### 8.3 Usage

- `free-only` never requests paid approval.
- `strict` lowers thresholds and keeps 4 reviewers.
- Usage approval binds exact plan + estimate.
- A plan change invalidates approval.

### 8.4 Verification

- Every work order has `verify_command`.
- Verify runs in the worktree before handoff.
- Merge reruns verify in main before committing.
- A failed merge restores the tree.

### 8.5 Secret handling

- Secret scan before prompts and target contents.
- Redact keys and paths in notifications.
- Never send `.env`, private keys or credential files.

---

## 9. Test matrix

| Area | Unit | Integration | E2E |
|---|---|---|---|
| Tier profile | `test_v2_tiers` (done) | fake execution (done) | real free-model smoke |
| Planner | `test_planner_adapter` (done) | fake planner (done) | goal → HANDOFF_READY (CLI done) |
| Usage | policy tests | runner | dashboard approval |
| Worktree | isolation | execute | kill/recover |
| Merge | path validation | temp repo | v1-vs-v2 parity |
| Dashboard | API | temp server | browser/UI contract |
| Alerts | redaction | capture webhook | optional live hook |
| Presets | config | execute | live free-only |
| Crash | state | process kill | resume |

---

## 10. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Losing a v1 capability silently | high | feature parity gate |
| E1 allows unsafe changes | high | low-risk opt-in + secret scan |
| Tier refactor breaks E3/E4 | high | old-plan default E3 + parity |
| Dashboard hides failures | medium | status/verify fields + tests |
| Merge applies unexpected paths | critical | allowlist + base + verify + rollback |
| Release confusion | medium | v3.0.0, no retag v2.1.0 |
| v1 oracle unavailable | medium | tag/branch + archived code |
| No users means no real feedback | medium | deterministic fixtures + live pilot |

---

## 11. Owner decisions required

Before Phase 1:

1. **E1 policy**
   - Recommended: explicit low-risk opt-in, secret scan mandatory, no
     sensitive paths.

2. **E2 reviewer**
   - Recommended: security reviewer default.

3. **Old commands**
   - Recommended: freeze v1, no compatibility wrappers; keep tag.

4. **Dashboard port**
   - Recommended: 8501 default, 8080 alias.

5. **Default flip**
   - Recommended: only after Phase 4, release v3.0.0.

6. **Drop list**
   - Review `V1_FEATURE_PARITY.md` and approve/defer/drop explicitly.

---

## 12. Commit sequence

```text
01 docs: revise master plan for zero-user v3 strategy
02 docs: add v1 feature parity matrix
03 test(unified): freeze baseline and feature contracts
04 feat(v2): add tier-aware E1-E4 execution
05 test(v2): cover E1-E4 tier runtime
06 feat(unified): add planner adapter
07 feat(unified): add orchestrator
08 feat(cli): add swarm command and wrappers
09 test(unified): cover one-command flow
10 feat(ui): add unified state API
11 feat(ui): merge War Room and spending panels
12 test(unified): add v1-vs-v2 parity
13 docs: publish v3 release notes
14 chore(release): tag v3.0.0 after owner approval
```

Each commit:

- has focused scope;
- includes or updates tests;
- runs the relevant suite;
- does not flip default until commit 14.

---

## 13. Definition of done

- [ ] `swarm "task"` reaches `HANDOFF_READY`.
- [ ] E1–E4 are real and tested.
- [ ] Planner P1–P3 works from a goal.
- [ ] Main stays unchanged until explicit merge.
- [ ] Merge/rollback work from CLI and War Room.
- [ ] One dashboard at 8501.
- [ ] v1 feature parity matrix has no unapproved loss.
- [ ] All 48+ suites pass.
- [ ] Free-model live smoke passes.
- [ ] 8-hour soak or agreed equivalent passes.
- [ ] v3.0.0 released; v1.0.5 remains.
- [ ] No public tag is rewritten.
