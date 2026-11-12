# Operator Guide — v3 Unified Engine

> v3.0.0 uses `swarm "task"` as the single operator entrypoint. Plan approval is
> no longer the default human gate. A quick task may run automatically. Only an
> expensive/long run asks for **Usage approval** in the single-run viewer Usage tab.
> Execution stays in a worktree; main changes only through explicit
> `swarm merge`.

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

Load the OpenCode skill. A fresh clone already has both files:

```text
.opencode/skills/adaptive-swarm-pipeline/SKILL.md
.opencode/commands/swarm.md
```

Verify them:

```powershell
python .agents\sync_skill.py --check
python .agents\tests\test_skill_consistency.py
```

Then, from OpenCode started at the project root:

```text
/swarm fix the failing add test
@adaptive-swarm-pipeline fix the failing add test
```

For a user-wide install:

```powershell
python .agents\sync_skill.py --global --write
```

and copy `.opencode/commands/swarm.md` to
`~/.config/opencode/commands/swarm.md` if you want `/swarm` everywhere.
The skill is discovery/instructions; it is not a background daemon. A coding
task should invoke the planner. See
[`OPENCODE_SKILL_SETUP.md`](OPENCODE_SKILL_SETUP.md).

## 3. Quick task

```powershell
.\swarm.cmd "fix the failing add test"   # POSIX: ./swarm.sh "fix the failing add test"
```

Add the repository root to `PATH` to use the bare `swarm` command.

The unified orchestrator runs doctor → plan → Usage decision → isolated
worktree → tier-aware execution → verify → checkpoint → handoff. A quick task
below the Usage threshold does not ask for a plan approval; verify, reviewer and
budget caps still run.

Legacy v1 direct commands remain available only as a compatibility/test oracle:

```powershell
python .agents\recipe_c.py --order order.json
```

## 4. Expensive task / tiers

```powershell
# E3 four-reviewer wave
.\swarm.cmd "review and fix the authentication flow" --tier E3

# explicit Usage approval when required
.\swarm.cmd "large refactor" --tier E3 --yes
```

`swarm` shows the plan/estimate and stops at `AWAITING_USAGE` when the policy
requires approval. The run viewer Usage card shows plan/subtask count,
reviewer/coder mode, estimated requests/cost/wall time, threshold reasons and
budget controls. Approval binds to the exact plan + estimate.

E4 uses the Blackboard meeting protocol:

```powershell
.\swarm.cmd "make an architecture decision" --tier E4 --yes
```

If a required reviewer route is unavailable, preflight aborts before any model
call.

## 5. Single-run viewer

```powershell
swarm ui --run-id <id> --open
# or
python .agents\dashboard.py --root . --run-id <id> --port 8501 --open
```

- **Run timeline**: Planning → Worktree → Executing → Verifying → Handoff.
- **Usage**: approval, estimate, plan summary, Approve/Reject.
- **Spend**: estimated vs actual tokens/cost, calls, remaining budget.
- **Chat/Replay**: per-run events and replay.
- **Verify**: commands and exit evidence.
- **Handoff**: diff preview, explicit merge and rollback.
- **Logbook**: generated Markdown/JSON for this run only.

The viewer intentionally has **no history or run selector**. To view another
run, start another viewer with that run ID:

```powershell
swarm ui --run-id <other-id> --port 8081 --open
```

The server binds `127.0.0.1`; write actions require the per-run token. Each
`swarm ui` invocation refreshes the run logbook under the run directory.

## 6. v3 unified engine

```powershell
.\swarm.cmd doctor
.\swarm.cmd "fix tests" --preset free-only
.\swarm.cmd "high-risk change" --preset strict --tier E3
.\swarm.cmd status --last
.\swarm.cmd merge --run-id v2_...
.\swarm.cmd rollback --run-id v2_...
```

- `swarm` is the single new-user entrypoint; `/swarm` is the OpenCode surface.
- `swarm merge` applies the handoff patch, reruns every plan `verify_command`,
  and commits only on exit 0. `swarm rollback` reverts that merge.
- `swarm resume` recovers a killed phase or resumes a paused run.
- `free-only` blocks paid models; `strict` keeps four reviewers and lowers the
  Usage gate to one request / $0.01.
- `swarm prune --days 7` removes old isolated worktrees/branches while retaining
  manifests, checkpoints, events, budget and handoff evidence.
- `v1.0.5` remains a frozen emergency fallback tag; do not treat `job.py` /
  `recipe_c.py` as the new-user path.

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
