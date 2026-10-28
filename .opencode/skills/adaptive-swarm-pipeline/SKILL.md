---
name: adaptive-swarm-pipeline
description: Run, observe and harden the Adaptive Swarm Pipeline in the project's `.agents/` folder. Use when the user asks to run a coding task through the swarm, replay a run, open the local dashboard, read cost/token/fallback statistics, get memory suggestions from past runs, or verify pipeline health (Phase 0 / 0.5 / 1 / 2 / 3 / 4 acceptance suites).
---

# Adaptive Swarm Pipeline

The full English runbook lives in `.agents/README.md`. Read it before doing
anything else; this file only exists so OpenCode can discover the skill.

Quick reference:

```bash
python3 .agents/scout.py --write --verify        # free-only; paid needs --allow-paid
python3 .agents/recipe_c.py --order order.json   # run a task
python3 .agents/replay.py --last                 # inspect a run (0 requests)
python3 .agents/dashboard.py --port 8080 --open  # dashboard
python3 .agents/tests/test_preflight.py          # health/cost gate
python3 .agents/tests/test_phase4.py             # acceptance smoke
```

Windows uses `python` instead of `python3`.

Hard rules: only Lead writes project files through `safe_writer`; only the real
machine can declare success through `verify_command`; the ledger is append-only;
never change `config.json` or the roadmap without approval.

Health preflight is mandatory: wave/meeting needs all four reviewer routes
(`security`, `qa`, `perf`, `docs`). Missing route/auth/cooldown aborts before a
model call; no heuristic fallback may auto-approve a partial panel. A routed
coder chain with no available model is blocked. `config.json` is machine-local.

## Platforms

Windows, Linux and macOS with Python 3.11+ are supported. Use `dashboard.sh` on
POSIX and `dashboard.cmd` on Windows. The OpenCode binary is resolved from
`PATH`, `OPENCODE_BIN`, then the npm shim on Windows.
