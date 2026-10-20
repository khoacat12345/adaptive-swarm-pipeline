---
name: adaptive-swarm-pipeline
description: Run, observe and harden the Adaptive Swarm Pipeline in the project's `.agents/` folder. Use when the user asks to run a coding task through the swarm, replay a run, open the local dashboard, read cost/token/fallback statistics, get memory suggestions from past runs, or verify pipeline health (Phase 0 / 0.5 / 1 / 2 / 3 / 4 acceptance suites).
---

# Adaptive Swarm Pipeline

The full runbook lives in `.agents/SKILL.md`. Read it before doing
anything else; this file only exists so OpenCode can discover the skill.

Quick reference:

```powershell
python .agents/scout.py --write                 # 1 lan / khi doi may
python .agents/recipe_c.py --order order.json   # giao task
python .agents/replay.py --last                 # xem lai (0 token)
python .agents/dashboard.py --port 8080 --open  # web dashboard
python .agents/memory.py                        # rut kinh nghiem
python .agents/tests/test_phase4.py             # nghiem thu tong
```

Hard rules: only Lead writes project files (via `safe_writer`), only the real machine
can declare success (`verify_command`), ledger is append-only, never change
`config.json` or the roadmap without the user's approval.
