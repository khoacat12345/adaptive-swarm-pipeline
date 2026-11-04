---
name: adaptive-swarm-pipeline
description: Run, observe and harden the Adaptive Swarm Pipeline in the project's `.agents/` folder. Use when the user asks to run a coding task through the swarm, replay a run, open the local dashboard, read cost/token/fallback statistics, get memory suggestions from past runs, or verify pipeline health (Phase 0 / 0.5 / 1 / 2 / 3 / 4 acceptance suites).
---

# Adaptive Swarm Pipeline

The full English runbook lives in `.agents/README.md`. Read it before doing
anything else; this file only exists so OpenCode can discover the skill.

## ⚠️ MANDATORY EXECUTION PROTOCOL (THIẾT QUÂN LUẬT)

1. **NEVER CODE MONOLITHICALLY IN CHAT**:
   When the user invokes `/swarm` or asks to implement a feature, bugfix, or code change:
   - **DO NOT** write code or generate solution files directly in chat.
   - **DO NOT** answer with conversational promises without invoking tools.

2. **MANDATORY TERMINAL INVOCATION**:
   You MUST immediately run the pipeline via bash/powershell tool:
   - For a natural language task / new feature:
     ```bash
     python .agents/planner.py --goal "<User's exact task>" --submit
     ```
   - For an existing Work Order file:
     ```bash
     python .agents/recipe_c.py --order order.json
     ```
   - For checking last run / replay / debug:
     ```bash
     python .agents/replay.py --last
     ```

3. **WORKFLOW EXECUTION**:
   - Execute `python .agents/planner.py --goal "..." --submit` via your shell tool.
   - Capture the terminal output, return the approval dashboard URL to the user, and wait for human sign-off.
   - All code writes MUST go through the swarm pipeline (`safe_writer`) and pass `verify_command`.

Quick reference:

```bash
python3 .agents/scout.py --write --verify        # free-only; paid needs --allow-paid
python3 .agents/recipe_c.py --order order.json   # run a task
python3 .agents/replay.py --last                 # inspect a run (0 requests)
python3 .agents/dashboard.py --port 8080 --open  # dashboard
python3 .agents/preflight.py --live              # live route/catalog gate
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

## Adaptive add-ons (Phase 0-6)

Adaptive features are optional and default to `off`:

```text
SWARM_ADAPTIVE_REPO_MAP=off|shadow|on
SWARM_ADAPTIVE_ACI=off|shadow|on
SWARM_ADAPTIVE_LESSONS=off|shadow|on
SWARM_ADAPTIVE_MEMORY_BUDGET=off|shadow|on
SWARM_ADAPTIVE_EVENTS=off|shadow|on
```

Read-only inspection commands:

```bash
python3 .agents/v2/run.py --root . adaptive-map --task "..." --mode shadow
python3 .agents/v2/run.py --root . adaptive-lessons --query "..."
```

Rules:

- `shadow` computes/logs but must not change planner or execution prompts.
- `on` injects repo-map/lesson context only as untrusted advice.
- ACI is read-only (`open`, `scroll`, `search_symbol`, `verify`); it cannot write.
- Memory constraints protect the constitution and cap core memory.
- Adaptive flags never bypass `safe_writer`, worktree, budget or verify.

## Platforms

Windows, Linux and macOS with Python 3.11+ are supported. Use `dashboard.sh` on
POSIX and `dashboard.cmd` on Windows. The OpenCode binary is resolved from
`PATH`, `OPENCODE_BIN`, then the npm shim on Windows.
