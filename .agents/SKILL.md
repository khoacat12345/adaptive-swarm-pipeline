# Adaptive Swarm Pipeline

The full English runbook lives in `.agents/README.md`. Read it before doing
anything else; this file only exists so OpenCode can discover the skill.

Quick reference:

```bash
python3 .agents/scout.py --write                 # once per machine / model change
python3 .agents/recipe_c.py --order order.json   # run a task
python3 .agents/replay.py --last                 # inspect a run (0 requests)
python3 .agents/dashboard.py --port 8080 --open  # dashboard
python3 .agents/tests/test_phase4.py             # acceptance smoke
```

Windows uses `python` instead of `python3`.

Hard rules: only Lead writes project files through `safe_writer`; only the real
machine can declare success through `verify_command`; the ledger is append-only;
never change `config.json` or the roadmap without approval.

## Platforms

Windows, Linux and macOS with Python 3.11+ are supported. Use `dashboard.sh` on
POSIX and `dashboard.cmd` on Windows. The OpenCode binary is resolved from
`PATH`, `OPENCODE_BIN`, then the npm shim on Windows.
