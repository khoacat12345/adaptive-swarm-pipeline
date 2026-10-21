# Adaptive Swarm Pipeline

![Adaptive Swarm dashboard](docs/dashboard.png)

A **verification-first** coding harness for [OpenCode](https://opencode.ai):
several AI models work as one team, but nothing is written to your project unless a
real machine check passes.

> Status: **experimental**. Built and tested on Windows (Python 3.12 + OpenCode Go).
>
> Built with AI assistance; every change is machine-verified.

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

## Goal -> plan (W4 planner)

```bash
python .agents/planner.py --goal "Add JWT auth and tests" --root . --submit
```

Or type the goal in the dashboard's input and click **Plan**: the planner reads the
project tree, proposes 1-5 subtasks with verification, validates that every target
exists and that dependencies are acyclic, then submits the plan for approval.

Planner chain: Qwen3.8 Max -> GLM-5.3 -> free fallbacks. If the first plan is
invalid it retries once with the validation errors attached.

## Wave reviewers (parallel audit)

```bash
python .agents/job.py --plan plan.json --root . --reviewer wave --wait --open
```

Four read-only reviewers run **in parallel** (security, QA, perf, docs) and their
verdicts are aggregated: any reject blocks the apply. The wave is **budget-aware**:
it runs only as many reviewers as the 8-request cap allows and records the rest as
`skipped`. Default remains the 0-request heuristic reviewer.

Free dry-run (same free model for all four roles, $0):

```cmd
set SWARM_WAVE_ROLES=security=opencode/deepseek-v4.1-flash-free,qa=opencode/deepseek-v4.1-flash-free,perf=opencode/deepseek-v4.1-flash-free,docs=opencode/deepseek-v4.1-flash-free
python .agents/job.py --plan scratch/swarm_demo/plan_wave.json --root . --reviewer wave --model opencode/deepseek-v4.1-flash-free --decision approve
```

## Notifications (one-way)

Create `.agents/notify.json`:

```json
{"enabled": true, "kind": "discord", "url": "https://discord.com/api/webhooks/..."}
```

Kinds: `discord` (`content`), `telegram` (`chat_id` + `text`; `url` is the bot
`sendMessage` endpoint), `generic` (`text`). Events: `plan.submitted`,
`plan.approved`, `plan.rejected`, `job.done`. Notification failures never break a job.

### Telegram bot (two-way)

```json
{"telegram": {"bot_token": "123:ABC", "chat_id": 123456}}
```

```bash
python .agents/telegram_bot.py --root .
```

Only the configured `chat_id` is accepted. Commands: `/status`, `/approve`,
`/reject`, `/pause`, `/resume`, `/stop`, `/say <direction>`, `/help`.

## Tests

```bash
python .agents/tests/test_phase4.py      # observation layer
python .agents/tests/test_level3.py      # multi-file transaction + regressions
python .agents/tests/test_phase5.py      # router + fallback reviewer + regressions
python .agents/tests/test_quota.py       # quota math + quota-aware routing
python .agents/tests/test_phase6.py      # plan approval + job executor
python .agents/tests/test_planner.py     # goal -> plan + webhook notifications
python .agents/tests/test_wave.py        # 4 parallel reviewers + budget
python .agents/tests/test_telegram.py    # telegram bot commands/auth
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
| `.agents/job.py` | plan approval, state/control, sequential executor |
| `.agents/planner.py` | goal -> plan.json (W4 planner) |
| `.agents/notify.py` | one-way Discord/Telegram/generic webhooks |
| `.agents/telegram_bot.py` | two-way Telegram control (/approve, /pause, /say) |
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
