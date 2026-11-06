# Plan — P0-06: Usage-only approval

> Status: planning only; no runtime code changed yet.
> Source of truth: [`OPEN_PROBLEMS.md`](OPEN_PROBLEMS.md), section **P0-06**.
> Goal: remove plan approval as a default human gate; keep a conditional approval only for quota/spend.

---

## 1. Current defects in P0-06

These are the concrete errors the implementation must remove:

1. **Plan approval is universal in `job.py`.** Every plan, including a small one, needs a plan decision before `run_job`.
2. **Direct `recipe_c` has no approval at all.** It can run wave/meeting and apply changes without any human check.
3. **The OpenCode skill recommends the unguarded path.** `.agents/SKILL.md` tells the agent to run `recipe_c.py --order ...` for an existing Work Order.
4. **Approval lives on the Plan page, not Usage.** The user wants cost consent, not plan consent.
5. **There is no usage estimate in the approval flow.** The user cannot see estimated requests, cost, wall time or remaining budget before approving.
6. **There is no shared policy.** `job.py`, `recipe_c.py`, the dashboard and v2 each make different decisions.
7. **`--decision approve` means plan approval.** It must become a Usage decision or be deprecated.
8. **v2 `signoff` is a plan/hash approval.** It must become Usage approval (or be replaced by it).
9. **Approval is not bound to an estimate.** A plan/estimate change does not reliably invalidate the old approval.
10. **No audit events distinguish required/approved/skipped.** Operators cannot see why a run did or did not ask for approval.
11. **README/INSTALL/SKILL contradict each other.** One path uses `--decision approve`; another uses `--wait --open`; another uses direct recipe.
12. **No tests for the intended policy.** There is no test proving a quick path auto-runs while a wave/meeting path blocks without Usage approval.

---

## 2. Target behavior

```text
Planner always runs.
Plan is validated and previewed.
Policy estimates usage.
If usage approval is NOT required:
    run immediately; no plan click; emit approval.skipped.
If usage approval IS required:
    block before the first model call.
    show Usage approval with plan + estimate.
    after approval: start the run.
    if plan/estimate changes: invalidate the approval.
```

Approval trigger:

```text
usage_approval_required =
    mode == "meeting"
    OR reviewer == "wave"
    OR multi_phase / overnight
    OR paid model
    OR estimated_requests >= threshold
    OR estimated_cost_usd >= threshold
    OR estimate confidence == "unknown"
```

Defaults (proposal; values to confirm before release):

```text
request_threshold: 8
cost_threshold_usd: 0.50
approval_ttl_minutes: 120
```

Quick path:

```text
no approval
still requires verify_command + budget cap + reviewer + rollback
```

---

## 3. Architecture

### 3.1 New shared module: `.agents/approval_policy.py`

One decision source for v1, v2 and tests.

```python
@dataclass
class UsageEstimate:
    requests: int
    cost_usd: float | None      # None = unknown -> fail closed
    wall_seconds: int
    confidence: str             # high | medium | unknown
    agents: list[str]
    models: list[str]

@dataclass
class UsageDecision:
    required: bool
    reasons: list[str]
    estimate: UsageEstimate
    thresholds: dict
    policy_hash: str
    estimate_hash: str

def evaluate(plan, *, mode, reviewer, multi_phase=False,
             overnight=False, paid=False) -> UsageDecision: ...

def assert_approved(root_or_run_dir, plan_hash, estimate_hash) -> dict: ...

def record_approval(path, *, plan_hash, estimate_hash,
                    approved_by, ttl_seconds) -> dict: ...
```

Rules:

- Unknown model pricing => `cost_usd=None` => approval required (fail closed).
- Free model => cost `0.0`, but request threshold still applies.
- `estimate_hash` covers plan hash, model roster, mode, reviewer, thresholds.
- Any plan/estimate change invalidates the record.
- Policy hash is stored in the ledger for audit.

### 3.2 New estimate helper: `.agents/usage_estimate.py` (or inside `approval_policy.py`)

Estimate before the first model call:

- coder requests: `max_rounds` (or one per subtask)
- reviewer requests: wave = 4; meeting = 4 + possible round 2 (max 2)
- planner requests: P1/P2/P3 (until P0-07 exists, use current planner path)
- verify commands: no model request, but add wall time estimate
- model price: from `.agents/config.json`; if missing, unknown/block
- wall seconds: conservative cap per call

The estimate must be visible in the approval screen; it is not billed actuals.

### 3.3 Approval record

v1 path:

```text
.agents/tmp/usage_approval.json
```

v2 path:

```text
.agents/tmp/v2/runs/<run_id>/usage_approval.json
```

Shape:

```json
{
  "schema_version": 1,
  "job_id": "J1",
  "run_id": null,
  "plan_hash": "sha256...",
  "estimate_hash": "sha256...",
  "approved_by": "dashboard-user",
  "approved_at": "2026-11-06T...",
  "expires_at": "2026-11-06T...",
  "reasons": ["meeting", "estimated_requests=12"],
  "estimate": {"requests": 12, "cost_usd": 0.0, "wall_seconds": 600}
}
```

The old `plan_decision.json` may remain for display/migration, but it must **not** authorize a run after P0-06.

---

## 4. Entry-point changes

### 4.1 `job.py`

Current:

```text
validate plan -> submit plan -> pending -> --wait/--decision -> run
```

Target:

```text
validate plan -> evaluate usage
  if not required: run immediately
  if required:     submit plan -> start/ensure dashboard Usage page
                   wait for usage decision -> run
```

Changes:

- Add `--usage-wait` / `--open-usage` (keep `--wait` as alias for one release).
- `--decision approve` becomes deprecated; it may only create a Usage approval when required.
- `start_run` checks `usage_approval` when policy requires it; it no longer checks only `plan_decision`.
- Auto-run quick path prints/logs `approval.skipped` with reason `below_threshold`.
- Remove plan approval from the normal `run_job` gate.
- Keep `--dry-run`; if dry-run still calls models, apply the same Usage policy.

### 4.2 `recipe_c.py`

Current: direct apply with no approval.

Target:

- After order validation and before coder:
  - compute usage decision from order + mode + reviewer.
  - if not required: run; emit `approval.skipped`.
  - if required and no valid approval record: exit non-zero before any model call, with a message pointing to the dashboard/Usage flow.
- Add `--usage-approval PATH` for headless/CI runs that already obtained a record.
- Direct `--mode meeting` / `--reviewer wave` must never bypass the gate.
- Keep `--dry-run` support, but apply policy if model calls are still estimated.

### 4.3 Dashboard

Plan page:

- remove Approve/Reject buttons;
- show `Plan preview — auto-runs unless Usage approval is required`;
- show estimate and gate status read-only.

Usage page:

- add `usageApproval` card:
  - status: `not_required | awaiting | approved | rejected | expired`
  - plan summary, agents, models
  - estimated requests/cost/wall
  - thresholds and reason
  - remaining budget
  - Approve / Reject with token
- new APIs:
  - `GET /api/usage/status`
  - `POST /api/usage/decision` (approve/reject)
- Reuse `job.py` token auth; no new daemon.
- On plan/estimate hash mismatch return `409`.
- `job.py --open-usage` deep-links to Usage approval.

### 4.4 v2

- Keep plan hash validation for audit.
- Repurpose `signoff` semantics:
  - add `usage-approve`; keep `signoff` as deprecated alias for one release.
  - `AWAITING_SIGNOFF` is displayed as `AWAITING_USAGE_APPROVAL`; state rename can be staged.
- `execute` asserts a valid Usage approval, not only `signoff`.
- `/v2` page button text changes to `Approve Usage`; show estimate.
- `accept_handoff` remains a separate end-of-run acknowledgement.

### 4.5 Skill and agent path

- `.agents/SKILL.md`:
  - planner always runs for coding tasks;
  - no direct `recipe_c --order` shortcut for skill-driven tasks;
  - expensive path must use Usage approval;
  - quick path auto-run is explicitly allowed.
- If the agent has shell access, it can self-approve; document that this is a workflow/cost rule, not an adversarial sandbox.

---

## 5. Implementation sequence

### Phase 1 — Policy and estimate (no behavior change)

- Add `approval_policy.py`.
- Add estimate helper and tests.
- Add config/default thresholds.
- Add policy hash/estimate hash helpers.
- Gate: unit tests for quick/wave/meeting/paid/unknown/threshold.

### Phase 2 — Approval record and CLI

- Add approval record read/write/assert.
- Add audit events:
  - `approval.required`
  - `approval.approved`
  - `approval.rejected`
  - `approval.skipped`
  - `approval.expired`
- Test hash invalidation.

### Phase 3 — `job.py`

- Replace plan gate with usage gate.
- Quick path auto-runs.
- Expensive path waits/opens Usage.
- Deprecate `--decision approve` as a Usage approval alias.
- Update `test_phase6.py`.

### Phase 4 — `recipe_c.py`

- Add pre-coder policy check.
- Add `--usage-approval`.
- Block direct wave/meeting without a record.
- Update wave/meeting tests.

### Phase 5 — Dashboard Usage UI

- Move approval controls from Plan to Usage.
- Add status/estimate card, APIs, SSE updates.
- Test dashboard API and HTML contract.
- Add deep link.

### Phase 6 — v2

- Add `usage-approve`, keep alias.
- `execute` requires Usage approval.
- Update `/v2` page and state labels.
- Update v2 tests.

### Phase 7 — Docs and rollout

- Update `README.md`, `docs/INSTALL.md`, `.agents/SKILL.md`, `.agents/README.md`, `README.vi.md`.
- Update `P1-13` operator guide.
- Add a single approval matrix.
- Feature-flag rollout:
  - `SWARM_APPROVAL_MODE=usage` default after tests pass;
  - `plan` mode only as temporary rollback for one release;
  - remove plan mode in the next release.
- Migrate old pending plan approvals to `expired`; never auto-convert them to Usage approvals.

---

## 6. Tests

### New suite: `.agents/tests/test_approval_policy.py`

- quick path -> `required=False`, `approval.skipped`.
- wave -> required, reason `reviewer=wave`.
- meeting -> required, reason `mode=meeting`.
- paid model -> required.
- unknown price -> required, fail closed.
- request threshold boundary.
- cost threshold boundary.
- plan change invalidates.
- estimate change invalidates.
- expired approval rejected.
- approved record passes.

### Existing suites to update

```text
.agents/tests/test_phase6.py       plan -> usage approval + dashboard
.agents/tests/test_wave.py         direct wave gate
.agents/tests/test_protocol.py     meeting direct gate
.agents/tests/test_round1.py       meeting direct gate
.agents/tests/test_preflight.py    no regression
.agents/tests/test_v2_m2.py        signoff/usage semantics
.agents/tests/test_v2_m3.py        execute requires usage approval
.agents/tests/test_skill_consistency.py
```

### Regression commands

```powershell
python .agents\tests\test_approval_policy.py
python .agents\tests\test_phase6.py
python .agents\tests\test_wave.py
python .agents\tests\test_protocol.py
python .agents\tests\test_round1.py
python .agents\tests\test_preflight.py
python .agents\tests\test_v2_m2.py
python .agents\tests\test_v2_m3.py
python .agents\tests\test_skill_consistency.py
```

Acceptance is not complete until a manual test proves:

1. Quick task runs without approve.
2. Expensive task blocks before any model call.
3. Usage approval starts the task.
4. Direct expensive `recipe_c` cannot bypass.
5. Plan/estimate change invalidates approval.

---

## 7. Acceptance criteria

- [ ] Quick one-coder path runs without plan approval.
- [ ] Expensive path cannot call a model before Usage approval.
- [ ] Direct `recipe_c` cannot bypass wave/meeting approval.
- [ ] Plan page has no default Approve button.
- [ ] Usage page shows plan, agents, models, estimate, budget.
- [ ] Approval record binds to `plan_hash + estimate_hash`.
- [ ] Plan/estimate change invalidates approval.
- [ ] Audit events are present in the ledger.
- [ ] `--decision approve` is deprecated or Usage-only.
- [ ] v2 execute requires Usage approval.
- [ ] README/INSTALL/SKILL have one consistent matrix.
- [ ] Full regression suites pass.
- [ ] No changes to `safe_writer`, worktree isolation, budget caps or `verify_command`.

---

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Estimated cost is wrong | Conservative estimate; unknown price => approval required |
| Free quota still burns after approval | Keep budget/request caps; estimate is a gate, not a cap |
| Agent self-approves | Document threat model; OS/container/external approval only if needed |
| Old approvals authorize new plans | Reject legacy `plan_decision` for execution; bind hashes |
| Users still click Approve blindly | Show estimate/plan and threshold reasons; default quick path is auto |
| v2 state migration breaks tests | Keep `signoff` alias for one release; add state label mapping |
| Dashboard API compatibility | Add new endpoints; keep old read-only endpoints |

---

## 9. Open decisions before coding

1. Exact request and cost thresholds.
2. Whether E1/E2 quick path is completely approval-free even when free quota is low.
3. Whether `dry-run` should require approval if it still spends model requests.
4. Whether v2 `accept_handoff` should remain manual.
5. Whether old `plan_decision.json` should be deleted or only ignored.
