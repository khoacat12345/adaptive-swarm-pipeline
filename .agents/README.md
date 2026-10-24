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
4. **Budget**: soft 8 / hard 16 requests, 300 seconds; hard abort at 16.
5. **Secret safety**: secrets are blocked or redacted before leaving the machine.
6. **Append-only ledger**: replay, dashboard and memory read the same evidence.
7. **No silent config or roadmap changes**.

## Setup

```bash
python3 .agents/scout.py --write --verify   # free-only by default
python3 .agents/tests/test_phase4.py --fast
```

Windows uses `python` instead of `python3`.

`scout.py` reads the local OpenCode model catalog and writes
`.agents/config.json`. The router uses that catalog; OpenCode Go is optional.
`config.json` is machine-local and is intentionally not committed. New installs
are free-only; use `--allow-paid` only when you explicitly want paid models.
`scout --verify` micro-pings the five main roles plus the four meeting/wave
reviewer roles, so the preflight has fresh evidence.

Before wave/meeting runs, `preflight.py` checks all required reviewer models and
the routed coder chain. Missing routes/auth, provider unavailability or cooldown
abort the run before any model call. Meeting/wave requires all four reviewers;
it never auto-approves through a heuristic fallback. Health results cache for
15 minutes; config/model changes invalidate the cache; a >24h cache is rechecked
with a warning.

## Run a task

```bash
python3 .agents/recipe_c.py --order order.json --route --json
```

Panel mode is the default. To use the governed meeting mode:

```bash
python3 .agents/job.py --plan plan.json --mode meeting --reviewer wave --decision approve
```

`--mode meeting` requires `--reviewer wave` and all four reviewer roles
(`security`, `qa`, `perf`, `docs`). If any reviewer is unavailable, the meeting
aborts with `meeting.aborted`; nothing is applied.

## Observe

```bash
python3 .agents/replay.py --last
python3 .agents/replay.py --list
python3 .agents/dashboard.py --port 8080 --open
python3 .agents/cost_report.py
python3 .agents/memory.py
```

Dashboard: `http://127.0.0.1:8080/`. It binds loopback only.

## Tests

```bash
python3 .agents/tests/test_protocol.py
python3 .agents/tests/test_preflight.py
python3 .agents/tests/test_round1.py
python3 .agents/tests/test_wave.py --fast
python3 .agents/tests/test_rebuttal_policy.py
python3 .agents/tests/test_meeting_ui.py
python3 .agents/tests/test_sse.py
python3 .agents/tests/test_adapter.py
python3 .agents/tests/test_model_selector.py
python3 .agents/tests/test_phase4.py --fast
```

Run the full suite listed in the root README before a release.

## Files

| Path | Purpose |
|---|---|
| `recipe_c.py` | verification pipeline, budget, apply/rollback |
| `safe_writer.py` | backup, atomic write, transaction, rollback |
| `router.py` | work-type router and local catalog selector |
| `quota.py` | OpenCode quota and cost accounting |
| `job.py` | plan approval and sequential executor |
| `planner.py` | goal -> plan |
| `preflight.py` | health/cost gate: model route, auth, cooldown, chain |
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
