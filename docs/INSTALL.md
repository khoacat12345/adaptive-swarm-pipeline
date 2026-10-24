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
python3 .agents/scout.py --write  # write .agents/config.json
python3 .agents/tests/test_phase4.py --fast
```

Then run a task:

```bash
python3 .agents/recipe_c.py --order scratch/swarm_demo/order.json --json
```

## Option B - add the pipeline to an existing project

Copy the **source** files, not runtime junk.

Linux/macOS:

```bash
cp -R /path/to/adaptive-swarm-pipeline/.agents /path/to/your-project/
cp -R /path/to/adaptive-swarm-pipeline/.opencode /path/to/your-project/
cd /path/to/your-project
python3 .agents/scout.py --write
python3 .agents/tests/test_phase4.py --fast
```

Windows PowerShell:

```powershell
Copy-Item -Recurse C:\path\to\adaptive-swarm-pipeline\.agents C:\path\to\your-project\
Copy-Item -Recurse C:\path\to\adaptive-swarm-pipeline\.opencode C:\path\to\your-project\
cd C:\path\to\your-project
python .agents\scout.py --write
python .agents\tests\test_phase4.py --fast
```

Do not copy:

```text
.agents/tmp/
.agents/ledger.jsonl
.agents/backups/
.agents/draft/
.agents/__pycache__/
```

`.agents/config.json` may be copied from another project on the same machine,
but `scout --write` is the safe option when models or auth change.

## OpenCode skill discovery

The repository already contains:

```text
.opencode/skills/adaptive-swarm-pipeline/SKILL.md
```

If you use a global skills directory, copy that folder there. OpenCode V2 on
Linux/macOS normally discovers skills under the XDG config directory
(`~/.config/opencode/skills/`); on this Windows machine the active path is
`C:\Users\tuank\.opencode\skills\adaptive-swarm-pipeline`. Keep one canonical
copy and do not edit both.

## Run

Panel mode is the default and preserves the original behavior:

```bash
python3 .agents/job.py --plan plan.json --mode panel --reviewer wave --decision approve
```

Meeting mode adds independent Round 1 plus at most one targeted rebuttal:

```bash
python3 .agents/job.py --plan plan.json --mode meeting --reviewer wave --decision approve
```

`--mode meeting` requires `--reviewer wave`. Use it for risky or conflicting
tasks; use panel/heuristic for small tasks.

## Dashboard

Linux/macOS:

```bash
./dashboard.sh
# or:
python3 .agents/dashboard.py --root . --port 8080 --open
```

Windows:

```powershell
.\dashboard.cmd
# or:
python .agents\dashboard.py --root . --port 8080 --open
```

The server binds `127.0.0.1` only. Read-only dashboard mode does not need a
token; control actions (`/api/run`, `/api/control`) require the token printed by
`job.py` or stored in `.agents/tmp/plan_token.txt`.

## Verify your platform

```bash
python3 .agents/tests/test_protocol.py
python3 .agents/tests/test_round1.py
python3 .agents/tests/test_rebuttal_policy.py
python3 .agents/tests/test_adapter.py
python3 .agents/tests/test_meeting_ui.py
python3 .agents/tests/test_sse.py
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
