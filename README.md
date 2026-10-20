# Adaptive Swarm Pipeline

A **verification-first** coding harness for [OpenCode](https://opencode.ai):
several AI models work as one team, but nothing is written to your project unless a
real machine check passes.

> Status: **experimental**. Built and tested on Windows (Python 3.12 + OpenCode Go).

## Why

Most coding agents trust themselves. This one does not.

- **Verified apply** — a change is written only if `verify_command` exits `0`.
- **Atomic multi-file transaction** — Level 3 backs up every target first, writes all,
  verifies the whole project, then commits or rolls back *all* files to their exact bytes.
- **Crash recovery** — a journal in `.agents/tmp/tx/` lets the next run roll back an
  interrupted transaction.
- **Cross-family review** — the reviewer must come from a different model family than
  the coder, so it cannot rubber-stamp its own work.
- **Hard budget** — 8 requests / 300 seconds per run; fallback switches models inside
  the same call on 429/5xx/timeout.
- **Quota-aware routing** — reads real spend from `opencode stats`, tracks the shared
  plan budget *and* per-model caps, then picks main -> abundant escalate -> cheap/free.
- **Append-only ledger** — every event is evidence; replay, dashboard and memory read
  the same source.
- **Single writer** — only the Lead writes project files, always through `safe_writer`.

## Quick start (Windows)

Requirements: Python 3.12+, OpenCode CLI (and optionally Node for JS tasks).

```bash
git clone https://github.com/khoacat12345/adaptive-swarm-pipeline.git
cd adaptive-swarm-pipeline

# 1) discover models / auth and preview the config
python .agents/scout.py

# 2) write .agents/config.json
python .agents/scout.py --write

# 3) run a task
python .agents/recipe_c.py --order scratch/swarm_demo/order.json --route --json
```

`--route` uses the W0-W8 work-type router and the real quota tracker.
Without `--route`, the coder chain comes from `.agents/config.json`.

## What a Work Order looks like

```json
{
  "task_id": "R1",
  "title": "fix add()",
  "objective": "Make add(a, b) return a + b",
  "boundaries": "Only edit calc.py",
  "input_context": "calc.py has add() returning a - b",
  "acceptance_criteria": "check.py exits 0 and add(2,3) == 5",
  "verify_command": "python check.py",
  "target_files": ["calc.py"],
  "sandbox_files": ["calc.py", "check.py"],
  "level": 1,
  "max_rounds": 1
}
```

Missing `verify_command` is rejected immediately. Level 1/2 take one target file;
Level 3 takes two or more and runs as one atomic transaction.

## Tests

```bash
python .agents/tests/test_phase4.py      # observation layer
python .agents/tests/test_level3.py      # multi-file transaction + regressions
python .agents/tests/test_phase5.py      # router + fallback reviewer + regressions
python .agents/tests/test_quota.py       # quota math + quota-aware routing
python .agents/tests/smoke_suite.py      # engine + 10 real tasks
```

Hard model-selection tasks live in `.agents/tests/hard_tasks.py`; see
`.agents/tests/HARD_TESTS.md` for how to run them and their real cost.

## Layout

| Path | Purpose |
|---|---|
| `.agents/recipe_c.py` | pipeline engine (classify, verify twice, apply, rollback) |
| `.agents/safe_writer.py` | backup, atomic write, transaction, journal, rollback |
| `.agents/router.py` | work-type router (W0-W8) with cross-family chains |
| `.agents/quota.py` | real spend from `opencode stats` (shared + per-model caps) |
| `.agents/workers.py` | coders/reviewers (agentic CLI, scripted, fallback) |
| `.agents/ledger.py`, `schema.py`, `replay.py`, `dashboard.py` | evidence + observability |
| `.agents/tests/` | acceptance suites and hard tasks |

Full runbook: [`.agents/README.md`](.agents/README.md) (Vietnamese, no diacritics)
and [`.agents/SKILL.md`](.agents/SKILL.md).

## Limitations (honest)

- Sequential execution; no parallel worktrees or merge gate yet.
- Windows-first paths (a Linux/macOS port is straightforward but not done).
- Verification is only as strong as your `verify_command`; a weak test gives false confidence.
- No repo-scale indexing/RAG; context is limited to the files you list in the order.
- Quota figures are read locally from `opencode stats`; the console remains the source of truth.

## Repository layout note

This repository was originally developed inside a home directory, so the root
`.gitignore` is a **whitelist**: it ignores everything and re-includes only
`.agents/`, `.opencode/`, `scratch/swarm_demo/` and `AGENTS.md`. The nested
`.agents/.gitignore` excludes runtime artifacts (ledger, backups, drafts, caches, secrets).

## License

MIT — see [LICENSE](LICENSE).
