# Operator Guide — Usage Approval

> P0-06: plan approval is no longer the default human gate.
> A quick task may run automatically. Only an expensive/long run asks for
> **Usage approval** in the dashboard's Usage tab.

## 1. What approve means now

```text
plan preview != approval
Usage approval = consent to spend quota/cost for this exact plan + estimate
```

Approval is required when the usage policy says so:

```text
meeting / wave / multi-phase / overnight / paid model /
estimated_requests >= 8 / estimated_cost_usd >= 0.50
```

Quick path:

```text
1 coder + 1 reviewer, free, below threshold
no Usage approval
still requires verify_command + reviewer + budget cap + rollback
```

Defaults are configurable in `.agents/approval.json` or with
`SWARM_USAGE_REQUEST_THRESHOLD`, `SWARM_USAGE_COST_THRESHOLD_USD` and
`SWARM_USAGE_APPROVAL_TTL`. Approval binds to `plan_hash + estimate_hash`.

## 2. First install

Requirements: Python 3.11+, Git, OpenCode CLI in `PATH`.

```powershell
git clone https://github.com/khoacat12345/adaptive-swarm-pipeline.git
cd adaptive-swarm-pipeline
python --version
opencode --version
```

Connect models:

```powershell
python .agents\scout.py --write --verify
python .agents\preflight.py --live
```

Free-only is the default. Paid models need `scout --write --allow-paid`.

Load the OpenCode skill:

```powershell
python .agents\sync_skill.py --write
python .agents\sync_skill.py --check
```

The skill is discovery/instructions; it is not a background daemon. A coding
task should invoke the planner.

## 3. Quick task

```powershell
python .agents\planner.py --goal "fix the failing add test" --out .agents\tmp\auto_plan.json
python .agents\job.py --plan .agents\tmp\auto_plan.json
```

If the plan is below threshold, it starts without a plan-approval click. Verify,
reviewer and budget caps still run.

Direct Work Order:

```powershell
python .agents\recipe_c.py --order order.json
```

## 4. Expensive task

### Job plan

```powershell
python .agents\job.py --plan plan.json --reviewer wave --wait --open
```

This opens the dashboard and waits for Usage approval. The Usage card shows:

- plan and subtask count
- reviewer/coder mode
- estimated requests/cost/wall time
- threshold reasons
- budget and approval controls

Approve once; if the plan or estimate changes, approval is invalid.

### Direct recipe

```powershell
python .agents\recipe_c.py --order order.json --reviewer wave --wait-usage
```

The direct request is stored under `.agents/tmp/usage_request.json`, separate
from the job plan/token. The dashboard URL includes `usage_source=recipe`.

Headless alternative:

```powershell
python .agents\recipe_c.py --order order.json --reviewer wave --usage-approve
```

This is explicit and audited; it is not a silent bypass.

### Meeting

```powershell
python .agents\job.py --plan plan.json --mode meeting --reviewer wave --wait --open
```

Meeting requires `--reviewer wave` and all four roles: security, qa, perf, docs.
If a role is unavailable, preflight aborts before any model call.

## 5. Dashboard

```powershell
.\dashboard.cmd
# or
python .agents\dashboard.py --root . --port 8080 --open
```

- **Plan**: preview only. No plan-approval button.
- **Usage**: Usage gate, estimate, plan summary, Approve/Reject.
- **War Room**: live state; can open Usage gate.
- **Chat / Report**: timeline and final evidence.

Port conflict:

```powershell
python .agents\dashboard.py --root . --port 8081 --open
```

The server binds `127.0.0.1`; control actions need the token printed by
`job.py`/`recipe_c --wait-usage` (or `.agents/tmp/plan_token.txt`).

## 6. v2

```powershell
python .agents\v2\run.py prepare --goal "fix tests" --phase P1
python .agents\v2\run.py plan --run-id v2_... --plan plan.json
python .agents\v2\run.py usage-approve --run-id v2_...
python .agents\v2\run.py execute --run-id v2_... --free
```

- `signoff` remains a one-release alias for `usage-approve`.
- `/v2` page button is **Approve Usage**.
- `Accept handoff` is a separate end-of-run acknowledgement; it does not start
  the run or auto-merge.
- Preflight a new machine with `python .agents\v2\run.py doctor` (Git, worktree,
  OpenCode free models, Spec Kit). Add `--json` for automation.
- Retain evidence but reclaim disk with `python .agents\v2\run.py prune --days 7`
  (add `--dry-run` first, `--force` for completed runs younger than N days).
  Prune removes only isolated worktrees and `swarm/*` branches; manifests,
  checkpoints, events, budget and handoff patch/report stay in the run ledger.

## 7. Approval matrix

| Entry path | Quick path | Expensive path |
|---|---|---|
| `job.py --plan` | auto-run | Usage gate |
| `recipe_c --order` | auto-run | blocked unless `--wait-usage` / `--usage-approve` |
| `job.py --decision approve` | no-op for quick | explicit Usage approval |
| `v2 usage-approve` | n/a (v2 is four reviewers) | required |
| `v2 signoff` | legacy alias | same as Usage approval |
| Plan dashboard button | removed | removed |

## 8. Failure recovery

- **Usage awaiting / timeout**: start dashboard, open Usage, approve; or use
  `--usage-approve` for explicit headless approval.
- **Expired / invalid approval**: re-open Usage; plan/estimate changed.
- **Reviewer unavailable / quota**: wait for reset or change free models; do not
  switch to paid without usage approval.
- **Verify fail**: no apply; inspect the report and draft, then re-run.
- **Budget stop**: reduce scope or thresholds; do not bypass budget caps.
- **Plan change**: old approval is invalid; approve the new estimate.

## 9. Safety boundary

Approval is a workflow/cost gate, not an adversarial sandbox. If the agent has
unrestricted shell access, it can call other commands. A real security boundary
needs OS/user/container isolation or an external approval service.

Do not change `safe_writer`, worktree isolation, budget caps or `verify_command`
to make approval easier.

## 10. Verification

```powershell
python .agents\tests\test_approval_policy.py
python .agents\tests\test_usage_gate.py
python .agents\tests\test_phase6.py
python .agents\tests\test_v2_m2.py
python .agents\tests\test_v2_m3.py
python .agents\tests\test_skill_consistency.py
```

Current defaults: **8 requests / $0.50 / 2 hours**.
