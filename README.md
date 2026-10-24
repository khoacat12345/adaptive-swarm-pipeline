# Adaptive Swarm Pipeline

![Adaptive Swarm dashboard](docs/dashboard.png)

A **verification-first** coding harness for [OpenCode](https://opencode.ai):
several AI models work as one team, but nothing is written to your project unless a
real machine check passes.

> Status: **v1.0.5** (stable core). Original acceptance on Windows; Linux/macOS
> paths are supported and covered by the cross-platform CI workflow.
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
- **Hard budget** — soft 8 / hard 16 requests / 300 seconds per run; fallback switches models inside
  the same call on 429/5xx/timeout.
- **Quota-aware routing** — reads real spend from `opencode stats`, tracks the shared
  plan budget *and* per-model caps, then picks main -> abundant escalate -> cheap/free.
- **Append-only ledger** — every event is evidence; replay, dashboard and memory read
  the same source.
- **Single writer** — only the Lead writes project files, always through `safe_writer`.

## Install (Windows / Linux / macOS)

Requirements: Python 3.11+ (3.12 recommended), Git, and the OpenCode CLI in `PATH`.

```bash
git clone https://github.com/khoacat12345/adaptive-swarm-pipeline.git
cd adaptive-swarm-pipeline
```

### Per machine

```bash
# Linux/macOS
python3 .agents/scout.py          # preview
python3 .agents/scout.py --write  # write .agents/config.json

# Windows PowerShell
python .agents\scout.py
python .agents\scout.py --write
```

### Per project

Copy the source `.agents/` and `.opencode/` folders into the project, then run
`scout --write` and the acceptance smoke test.

```bash
python3 .agents/tests/test_phase4.py --fast
```

Full cross-platform setup, optional skill discovery, dashboard and MCP notes:
**[docs/INSTALL.md](docs/INSTALL.md)**.

### First task

```bash
python3 .agents/recipe_c.py --order scratch/swarm_demo/order.json --route --json
```

Panel mode is the default. Meeting mode is opt-in:

```bash
python3 .agents/job.py --plan plan.json --mode meeting --reviewer wave --decision approve
```

`--mode meeting` requires `--reviewer wave`; it adds independent Round 1 plus at
most one targeted rebuttal and a machine re-verify before Lead applies.

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

## Dashboard (mission control)

Open it with `dashboard.cmd` on Windows or `./dashboard.sh` on Linux/macOS, or
`job.py --wait --open` for full control with a token.

Left sidebar: **Overview, Plan, Chat, Workspaces, Usage, Drafts, Settings**.

- **Chat** is a chronological timeline (oldest first) with `HH:MM:SS` on every event;
  logs are not shown on the overview.
- **Workspaces** lists real OpenCode projects (old working rooms) with session count,
  cost and tokens.
- **Usage** shows exact per-request spend from `opencode.db` (today / 5h rolling /
  7d / 30d), per-model costs and a request log.
- Deep links: `/#overview`, `/#plan`, `/#chat`, `/#workspaces`, `/#quota`, `/#drafts`, `/#settings`.

![Usage view](docs/usage.png)

![Workspaces](docs/workspaces.png)

![Swarm chat](docs/chat.png)

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
it uses the wave reservation/burst rules (soft 8 / hard 16) and records any
unused roles as `skipped`. Default remains the 0-request heuristic reviewer.

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

Install guide: [`docs/INSTALL.md`](docs/INSTALL.md).

Full runbook: [`.agents/README.md`](.agents/README.md) (Vietnamese, no diacritics)
and [`.agents/SKILL.md`](.agents/SKILL.md).

## Limitations (honest)

- Sequential execution; parallel independent workstreams/merge gate are roadmap (v1.5).
- Linux/macOS paths are supported; the original acceptance run was on Windows.
- No OS/container sandbox: verify commands run on the real machine.
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
