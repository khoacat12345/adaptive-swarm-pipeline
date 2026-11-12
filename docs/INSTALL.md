# Install and run Adaptive Swarm Pipeline

This project is **OpenCode-first**. The pipeline lives in each project's
`.agents/` directory; the OpenCode skill only makes it discoverable.

## Requirements

- Python 3.11+ (3.12 recommended)
- Git
- OpenCode CLI available as `opencode` in `PATH`
- Access to at least one OpenCode model/provider
- Optional: Node.js for web/JS tasks, Godot for Godot tasks

Check:

```bash
python3 --version
opencode --version
```

On Windows use `python` instead of `python3` if that is how Python is installed.

## Option A - use this repository as the project

```bash
git clone https://github.com/khoacat12345/adaptive-swarm-pipeline.git
cd adaptive-swarm-pipeline
python3 .agents/scout.py          # preview model mapping
python3 .agents/scout.py --write --verify  # free-only by default
python3 .agents/tests/test_phase4.py --fast
```

Then run a task:

```bash
./swarm.sh "fix the failing add test"
# Windows: .\swarm.cmd "fix the failing add test"
```

`swarm --version` reports `3.0.0`. The v1 `recipe_c.py` order flow is frozen
compatibility/test-oracle only.

## Option B - add the pipeline to an existing project

Copy the **source** files, not runtime junk.

Linux/macOS:

```bash
cp -R /path/to/adaptive-swarm-pipeline/.agents /path/to/your-project/
cp -R /path/to/adaptive-swarm-pipeline/.opencode /path/to/your-project/
cp /path/to/adaptive-swarm-pipeline/swarm.sh /path/to/your-project/
cp /path/to/adaptive-swarm-pipeline/dashboard.sh /path/to/your-project/
cd /path/to/your-project
python3 .agents/scout.py --write
python3 .agents/tests/test_phase4.py --fast
./swarm.sh doctor
```

Windows PowerShell:

```powershell
Copy-Item -Recurse C:\path\to\adaptive-swarm-pipeline\.agents C:\path\to\your-project\
Copy-Item -Recurse C:\path\to\adaptive-swarm-pipeline\.opencode C:\path\to\your-project\
Copy-Item C:\path\to\adaptive-swarm-pipeline\swarm.cmd C:\path\to\your-project\
Copy-Item C:\path\to\adaptive-swarm-pipeline\dashboard.cmd C:\path\to\your-project\
cd C:\path\to\your-project
python .agents\scout.py --write
python .agents\tests\test_phase4.py --fast
.\swarm.cmd doctor
```

Do not copy:

```text
.agents/tmp/
.agents/ledger.jsonl
.agents/backups/
.agents/draft/
.agents/__pycache__/
```

`.agents/config.json` is machine-local and is not committed. It may be copied
from another project on the same machine, but `scout --write` is the safe option
when models or auth change.

## OpenCode skill discovery

The repository already contains the project-local skill and the `/swarm`
command:

```text
.opencode/skills/adaptive-swarm-pipeline/SKILL.md
.opencode/commands/swarm.md
```

OpenCode V2 discovers `.opencode/skills/` automatically when it is started from
the project root. No copy is required for a fresh clone. Verify:

```bash
python3 .agents/sync_skill.py --check
python3 .agents/tests/test_skill_consistency.py
```

Then invoke the swarm from OpenCode:

```text
/swarm <your task>
@adaptive-swarm-pipeline <your task>
```

The skill ID is exactly `adaptive-swarm-pipeline`; the frontmatter `name` is
only a display label. `/swarm` is provided by `.opencode/commands/swarm.md`.

### Global install (optional)

OpenCode V2 global skills live under:

```text
~/.config/opencode/skills/adaptive-swarm-pipeline/SKILL.md
```

On Windows that is normally:

```text
C:\Users\<you>\.config\opencode\skills\adaptive-swarm-pipeline\SKILL.md
```

Install/update the global skill with:

```bash
python3 .agents/sync_skill.py --global --write
python3 .agents/sync_skill.py --global --check
```

To use `/swarm` from every project, also copy the command:

```bash
mkdir -p ~/.config/opencode/commands
cp .opencode/commands/swarm.md ~/.config/opencode/commands/swarm.md
```

Keep one canonical source (`.agents/SKILL.md`), then sync; do not edit the
installed copy by hand. Full guide:
[`OPENCODE_SKILL_SETUP.md`](OPENCODE_SKILL_SETUP.md).

## Run

v3.0.0 uses one official entrypoint. From the repository root, Windows uses
`.\swarm.cmd` and Linux/macOS uses `./swarm.sh`; add the root to `PATH` if you
want the bare `swarm` command.

```powershell
.\swarm.cmd "fix the failing add test"
```

The flow is doctor → plan → Usage decision → isolated worktree → E1–E4
execution → verify → checkpoint → handoff. A quick task below the Usage
threshold starts without plan approval; verify, reviewer and budget gates still
run.

Expensive or high-risk tasks:

```powershell
.\swarm.cmd "high-risk authentication change" --tier E3 --preset strict
.\swarm.cmd "architecture decision" --tier E4 --yes
```

E3 runs four independent reviewers. E4 uses the Blackboard meeting protocol.
If a required reviewer route is unavailable, preflight aborts before any model
call. Paid models are opt-in with `scout --write --allow-paid`.

Legacy v1 commands remain frozen as a test oracle:

```bash
python3 .agents/job.py --plan plan.json
```

## Single-run viewer

The UI is now scoped to one run and is opened from the terminal:

```powershell
swarm ui --run-id <id> --open
# or:
python .agents\dashboard.py --root . --run-id <id> --port 8501 --open
```

Windows wrapper:

```powershell
.\dashboard.cmd --run-id <id> --port 8501 --open
```

Linux/macOS:

```bash
./dashboard.sh --run-id <id> --port 8501 --open
```

The default page without `--run-id` only prints instructions. There is no
history/run selector in the UI. To view another run, open another viewer with
that run ID; use a different port for concurrent viewers.

The server binds `127.0.0.1` only. Control actions require the per-run sign-off
token. Each `swarm ui` invocation also regenerates the run logbook at:

```text
.agents/tmp/v2/runs/<id>/logbook.md
.agents/tmp/v2/runs/<id>/logbook.json
```

## Verify your platform

```bash
python3 .agents/tests/test_unified_contracts.py
python3 .agents/tests/test_unified_orchestrator.py
python3 .agents/tests/test_swarm_cli.py
python3 .agents/tests/test_v1_v2_parity.py
python3 .agents/tests/test_phase4b.py
python3 .agents/tests/test_phase_process_crash.py
python3 .agents/tests/test_phase4.py --fast
```

On Windows replace `python3` with `python`.

## MCP

MCP is optional and is **not** part of the safety invariant. For Godot, use the
Godot MCP server in the interactive editor (Antigravity) for scene/runtime
inspection; do not give an MCP write tool control of the live project while the
pipeline is applying changes. Keep one writer at a time.

## Known limitations

- The pipeline is sequential; independent workstreams are not parallel yet.
- No OS/container sandbox: verify commands run on the real machine.
- No production API/MCP adapter yet; providers are OpenCode/Mock plus the
  adapter interface.
- Monorepos and very large projects need project profiles, scoped context and
  test-impact analysis (roadmap, not v1.0).
- Verification quality is only as good as your `verify_command`.
- Dashboard is local and single-user.

## License

MIT. See [LICENSE](../LICENSE).
