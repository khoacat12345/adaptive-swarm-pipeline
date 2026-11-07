# Adaptive Swarm Pipeline - Operator Runbook

This is the English operator guide for the `.agents/` pipeline.

## What it does

A verification-first coding harness for OpenCode. Multiple models can plan, code
and review, but only the Lead writes project files, and only a real machine
command can declare success.

## Invariants

1. **Single writer**: only Lead writes project files, through `safe_writer`.
2. **Machine verify**: no apply unless `verify_command` exits `0`.
3. **Byte-exact rollback**: backup first; failed verify restores exact bytes.
4. **Budget**: soft 8 / hard 16 requests, 600 seconds; hard abort at 16.
5. **Usage approval**: a quick free one-coder + one-reviewer run may start
   automatically. A wave/meeting/overnight/paid or over-threshold run needs a
   Usage approval bound to its plan + estimate. Plan approval is not a default gate.
6. **Secret safety**: secrets are blocked or redacted before leaving the machine.
7. **Append-only ledger**: replay, dashboard and memory read the same evidence.
8. **No silent config or roadmap changes**.

## Setup

```bash
python3 .agents/scout.py --write --verify   # free-only by default
python3 .agents/tests/test_phase4.py --fast
```

Windows uses `python` instead of `python3`. After editing the skill entrypoint,
sync the installed copy so OpenCode does not load a stale frontmatter file:

```bash
python3 .agents/sync_skill.py --write
```

Usage approval defaults to **8 requests / $0.50 / 2 hours**. Override in
`.agents/approval.json` or with `SWARM_USAGE_REQUEST_THRESHOLD`,
`SWARM_USAGE_COST_THRESHOLD_USD`, and `SWARM_USAGE_APPROVAL_TTL`. Approval is
bound to the exact plan and estimate; any plan or estimate change invalidates it.

`scout.py` reads the local OpenCode model catalog and writes
`.agents/config.json`. The router uses that catalog; OpenCode Go is optional.
`config.json` is machine-local and is intentionally not committed. New installs
are free-only; use `--allow-paid` only when you explicitly want paid models.
`scout` treats `opencode auth list` as authoritative, so `opencode-go/*` is not
mistaken for unconfigured. Free `*-free` models survive a provider without an
API key, and a dead local `9router` proxy is probed and excluded from role maps.
Router quota fallbacks are read from `config.json`, not hardcoded model IDs.
`scout --verify` micro-pings the five main roles plus the four meeting/wave
reviewer roles, so the preflight has fresh evidence.

Before wave/meeting runs, `preflight.py` checks all required reviewer models and
the routed coder chain. It also cross-checks the live `opencode models` catalog
for `opencode/*` and `opencode-go/*`; if the live lookup cannot run, meeting/wave
fails closed. Missing routes/auth, provider unavailability or cooldown abort the
run before any model call. Meeting/wave requires all four reviewers;
it never auto-approves through a heuristic fallback. Health results cache for
15 minutes; config/model changes invalidate the cache; a >24h cache is rechecked
with a warning. Meeting runs have a 600s wall-clock cap; role reviewers get
240s each (`SWARM_REVIEWER_TIMEOUT`).

## Run a task

### Quick path

```bash
python3 .agents/recipe_c.py --order order.json --route --json
```

A free one-coder + one-reviewer task below the Usage threshold runs without a
plan-approval click. Machine verify, reviewer and budget caps still apply.

### Expensive path on the dashboard

```bash
python3 .agents/job.py --plan plan.json --reviewer wave --wait --open
python3 .agents/recipe_c.py --order order.json --reviewer wave --wait-usage
```

The dashboard opens the **Usage** tab and waits for approval. Direct recipe
requests are kept under `.agents/tmp/usage_request.json`, separate from the job
plan/token.

### Meeting

```bash
python3 .agents/job.py --plan plan.json --mode meeting --reviewer wave --wait --open
```

`--mode meeting` requires `--reviewer wave` and all four reviewer roles
(`security`, `qa`, `perf`, `docs`). If any reviewer is unavailable, the meeting
aborts with `meeting.aborted`; nothing is applied.

### v2

```bash
python3 .agents/v2/run.py prepare --goal "..." --phase P1
python3 .agents/v2/run.py plan --run-id v2_... --plan plan.json
python3 .agents/v2/run.py usage-approve --run-id v2_...
python3 .agents/v2/run.py execute --run-id v2_... --free
python3 .agents/v2/run.py recover --run-id v2_...   # after a killed phase
```

`signoff` remains an alias for one release; `usage-approve` is the new explicit
name. The `/v2` page shows **Approve Usage**. `Accept handoff` remains a separate
end-of-run acknowledgement.

## Soak / overnight

```bash
python3 .agents/soak_runner.py --mode fake --hours 8 --interval 60 --keep-awake
python3 .agents/soak_runner.py --mode live --hours 8 --interval 60 --keep-awake
```

Heartbeats append to `docs/SOAK_LOG.md`. Fake mode exercises the state machine
without model quota; live mode uses the same free seats and Usage gate.

## Observe

```bash
python3 .agents/replay.py --last
python3 .agents/replay.py --list
python3 .agents/dashboard.py --port 8080 --open
python3 .agents/cost_report.py
python3 .agents/memory.py
```

Dashboard: `http://127.0.0.1:8080/`. It binds loopback only. The **Usage** tab
shows the Usage gate, estimate, plan summary and Approve/Reject controls. The
Plan tab is preview-only under P0-06.

## Tests

```bash
python3 .agents/tests/test_tiers.py
python3 .agents/tests/test_capability_registry.py
python3 .agents/tests/test_context_guard.py
python3 .agents/tests/test_capabilities_uniform.py
python3 .agents/tests/test_cost_reconciliation.py
python3 .agents/tests/test_resilience.py
python3 .agents/tests/test_speckit_converge.py
python3 .agents/tests/test_v2_parity.py
python3 .agents/tests/test_phase_process_crash.py
python3 .agents/tests/test_approval_policy.py
python3 .agents/tests/test_usage_gate.py
python3 .agents/tests/test_protocol.py
python3 .agents/tests/test_preflight.py
python3 .agents/tests/test_skill_consistency.py
python3 .agents/tests/test_round1.py
python3 .agents/tests/test_wave.py --fast
python3 .agents/tests/test_rebuttal_policy.py
python3 .agents/tests/test_meeting_ui.py
python3 .agents/tests/test_sse.py
python3 .agents/tests/test_adapter.py
python3 .agents/tests/test_model_selector.py
python3 .agents/tests/test_v2_m1.py
python3 .agents/tests/test_v2_m2.py
python3 .agents/tests/test_v2_m3.py
python3 .agents/tests/test_v2_m5.py
python3 .agents/tests/test_v2_p0.py
python3 .agents/tests/test_v2_adaptive_flags.py
python3 .agents/tests/test_v2_planner_repo_map.py
python3 .agents/tests/test_v2_aci_integration.py
python3 .agents/tests/test_v2_lessons_integration.py
python3 .agents/tests/test_v2_context_constraints.py
python3 .agents/tests/test_v2_event_state.py
python3 .agents/tests/test_v2_adaptive.py
python3 .agents/tests/test_phase4.py --fast
```

Run the full suite listed in the root README before a release.

## Files

| Path | Purpose |
|---|---|
| `recipe_c.py` | verification pipeline, budget, apply/rollback |
| `safe_writer.py` | backup, atomic write, transaction, rollback |
| `router.py` | work-type router and local catalog selector |
| `quota.py` | OpenCode quota and cost accounting (`SWARM_PLAN_MONTHLY_USD` override) |
| `job.py` | Usage approval, state/control and sequential executor |
| `approval_policy.py` | shared Usage estimate/policy/approval record |
| `usage_request.py` | direct recipe request for the dashboard Usage gate |
| `planner.py` | goal -> plan |
| `preflight.py` | health/cost gate: model route, auth, cooldown, chain |
| `sync_skill.py` | sync `.agents/SKILL.md` into installed OpenCode skill |
| `workers.py` | coders/reviewers and provider injection |
| `core/` | meeting protocol, blackboard, policy, budget |
| `providers/` | OpenCode, Mock and adapter contract |
| `ledger.py`, `schema.py`, `replay.py`, `dashboard.py` | evidence and observability |

## Limitations

- Sequential execution; parallel workstreams are roadmap.
- No OS/container sandbox; verify runs on the real machine.
- No production MCP/API adapter yet.
- Dashboard is local and single-user.
- Context is limited to the files listed in the work order.

Vietnamese historical runbook: `README.vi.md`.
Vietnamese historical skill notes: `SKILL.vi.md`.
