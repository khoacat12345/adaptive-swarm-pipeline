# Install the OpenCode Skill (New-User Guide)

This guide is for someone who has just cloned or downloaded Adaptive Swarm
Pipeline and expects to call the swarm from OpenCode.

The important distinction:

- `.agents/` contains the pipeline and scripts.
- `.opencode/skills/adaptive-swarm-pipeline/SKILL.md` tells OpenCode **when and
  how** to invoke that pipeline.
- `.opencode/commands/swarm.md` provides the `/swarm` command.

The skill is instructions, not a background daemon. The actual work still runs
through `.agents/planner.py`, `job.py`, `recipe_c.py` or `.agents/v2/run.py`.

## TL;DR

From the repository root:

```powershell
# Windows
python .agents\sync_skill.py --check
python .agents\tests\test_skill_consistency.py
opencode
```

```bash
# Linux/macOS
python3 .agents/sync_skill.py --check
python3 .agents/tests/test_skill_consistency.py
opencode
```

Then inside OpenCode use either:

```text
/swarm fix the failing add() test
```

or:

```text
@adaptive-swarm-pipeline fix the failing add() test
```

Natural language such as “run this through the swarm” also works when the skill
is discoverable.

## Option A — use the cloned repository as the project (recommended)

The repository already contains the skill. OpenCode V2 discovers project skills
under `.opencode/skills/` automatically.

```text
adaptive-swarm-pipeline/
├── .agents/                       # pipeline scripts and tests
└── .opencode/
    ├── commands/
    │   └── swarm.md               # /swarm command
    └── skills/
        └── adaptive-swarm-pipeline/
            └── SKILL.md           # skill definition / ID
```

Steps:

```powershell
git clone https://github.com/khoacat12345/adaptive-swarm-pipeline.git
cd adaptive-swarm-pipeline
python .agents\scout.py --write --verify
python .agents\sync_skill.py --check
opencode
```

- Start OpenCode **from the project root** (or a child directory).
- The skill ID is exactly `adaptive-swarm-pipeline`.
- `/swarm` comes from `.opencode/commands/swarm.md`.
- Do not rename `SKILL.md`; a nested file must be named exactly `SKILL.md`.

If you downloaded a ZIP instead of cloning, make sure the extracted folder
contains both `.agents/` and `.opencode/`. Some ZIP tools hide dot-directories
by default.

## Option B — add the skill to an existing project

Copy both source folders, not runtime state:

Windows PowerShell:

```powershell
Copy-Item -Recurse C:\path\to\adaptive-swarm-pipeline\.agents C:\path\to\your-project\
Copy-Item -Recurse C:\path\to\adaptive-swarm-pipeline\.opencode C:\path\to\your-project\
cd C:\path\to\your-project
python .agents\scout.py --write
python .agents\sync_skill.py --check
```

Linux/macOS:

```bash
cp -R /path/to/adaptive-swarm-pipeline/.agents /path/to/your-project/
cp -R /path/to/adaptive-swarm-pipeline/.opencode /path/to/your-project/
cd /path/to/your-project
python3 .agents/scout.py --write
python3 .agents/sync_skill.py --check
```

Do **not** copy runtime state:

```text
.agents/tmp/
.agents/backups/
.agents/draft/
.agents/ledger.jsonl
.agents/config.json       # machine-local; recreate with scout
```

## Option C — user-wide install

Use this when you want the skill available from any project, not only the
repository clone.

OpenCode V2 global skill root:

```text
~/.config/opencode/skills/adaptive-swarm-pipeline/SKILL.md
```

Windows equivalent:

```text
C:\Users\<you>\.config\opencode\skills\adaptive-swarm-pipeline\SKILL.md
```

Install with the helper:

```powershell
python .agents\sync_skill.py --global --write
python .agents\sync_skill.py --global --check
```

Then copy the command if you also want `/swarm` globally:

```powershell
# PowerShell
New-Item -ItemType Directory -Force "$HOME\.config\opencode\commands" | Out-Null
Copy-Item .opencode\commands\swarm.md "$HOME\.config\opencode\commands\swarm.md"
```

```bash
# Linux/macOS
mkdir -p ~/.config/opencode/commands
cp .opencode/commands/swarm.md ~/.config/opencode/commands/swarm.md
```

Without the global command, use `@adaptive-swarm-pipeline` from any project.

## Update after pulling a new release

```powershell
git pull
python .agents\sync_skill.py --check
python .agents\sync_skill.py --write
python .agents\tests\test_skill_consistency.py
```

If you installed globally:

```powershell
python .agents\sync_skill.py --global --write
```

OpenCode reloads skills and command files automatically, but restart OpenCode if
the TUI still shows stale content.

## How to verify discovery

### 1. Check the files

```powershell
Test-Path .opencode\skills\adaptive-swarm-pipeline\SKILL.md
Test-Path .opencode\commands\swarm.md
python .agents\sync_skill.py --check
```

Expected:

```text
True
True
skill copies match (1 installed)
```

### 2. Check inside OpenCode

Start OpenCode at the project root and use:

```text
/swarm add a regression test for the parser
```

or explicitly:

```text
@adaptive-swarm-pipeline add a regression test for the parser
```

If the model receives the task and starts with the planner/recipe/v2 workflow,
the skill is installed correctly.

## Troubleshooting

| Symptom | Check |
|---|---|
| `/swarm` is not listed | `.opencode/commands/swarm.md` exists and OpenCode was started in this project |
| Skill is not offered | `.opencode/skills/adaptive-swarm-pipeline/SKILL.md` exists, has a `description`, and the directory name is exactly the skill ID |
| Wrong skill is loaded | Search for another source defining the same ID; project `.opencode/skills` overrides global sources |
| Skill loads but does not run | The skill is instructions only; the agent must execute `.agents/planner.py` / `job.py` or the v2 commands |
| Global install is not visible | Confirm `~/.config/opencode/skills/adaptive-swarm-pipeline/SKILL.md`; Windows uses `C:\Users\<you>\.config\opencode\skills\...` |
| Command works but no task | `/swarm` needs a task after it, or use `@adaptive-swarm-pipeline <task>` |

Do not edit both the repository copy and the global copy by hand. Edit
`.agents/SKILL.md`, then run `sync_skill.py --write` (and optionally
`--global --write`) so all installed copies stay deterministic.

## Safety note

The skill only tells the agent to use the pipeline. It does not grant permission
to write files directly. v1 remains the default path; v2 remains opt-in and
requires explicit worktree execution and merge.
