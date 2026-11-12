# Adaptive Swarm Pipeline

[![PyPI version](https://img.shields.io/pypi/v/adaptive-swarm-pipeline.svg)](https://pypi.org/project/adaptive-swarm-pipeline/)
[![PyPI Downloads](https://img.shields.io/badge/PyPI%20Downloads-live-blue.svg)](https://pypi.org/project/adaptive-swarm-pipeline/#files)
[![tests](https://github.com/khoacat12345/adaptive-swarm-pipeline/actions/workflows/cross-platform.yml/badge.svg?branch=main)](https://github.com/khoacat12345/adaptive-swarm-pipeline/actions/workflows/cross-platform.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![OpenCode Skill](https://img.shields.io/badge/OpenCode-Skill%20Ready-success.svg)](docs/OPENCODE_SKILL_SETUP.md)

### The Autonomous Multi-Agent Software Firm in Your Terminal.

Adaptive Swarm is a verification-first, multi-agent coding pipeline for
[OpenCode](https://opencode.ai). It combines zero-trust Git worktree isolation,
council planning, maker-checker review and verification gates. Multiple models
plan, code and review in isolation, while a human remains in control of the
final merge.

The public workflow is one command:

```bash
swarm "Refactor the auth service to use JWT"
```

Nothing is written to your `main` branch during execution. Review the handoff,
then merge explicitly:

```bash
swarm merge --run-id <id>
```

**v3.0.0 is the Unified Autonomous Engine release.** It consolidates the
previously separate v1/v2 entrypoints into one CLI and one orchestrator. The
dashboard is now a **single-run viewer**, opened with a run ID from the terminal.

---

## Quick Installation

### 1. Fastest: run without installing with `uvx`

```bash
uvx --from adaptive-swarm-pipeline swarm "Refactor auth service to use JWT"
```

`uvx` needs `--from ... swarm` because the package exposes the executable
`swarm`, not an executable named `adaptive-swarm-pipeline`.

### 2. Recommended: install globally with pip

```bash
pip install adaptive-swarm-pipeline
swarm "Task description"
```

Verify the installation:

```bash
swarm --version
# swarm 3.0.0
```

[PyPI package](https://pypi.org/project/adaptive-swarm-pipeline/) ·
[Release notes](docs/RELEASE_NOTES.md)

### 3. OpenCode users

Use the project-local skill and slash command:

```text
/swarm fix the failing authentication test
```

or mention the skill explicitly:

```text
@adaptive-swarm-pipeline fix the failing authentication test
```

The skill setup and discovery instructions are in
[`docs/OPENCODE_SKILL_SETUP.md`](docs/OPENCODE_SKILL_SETUP.md). The OpenCode
skill is shipped in the repository's `.opencode/` directory; a PyPI install
provides the terminal `swarm` command, not the project-local skill.

### 4. Contributors: install from source

```bash
git clone https://github.com/khoacat12345/adaptive-swarm-pipeline.git
cd adaptive-swarm-pipeline

python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

python -m pip install -e ".[dev]"
swarm --version

# Discover and write the local free-only model config
python .agents/scout.py --write --verify
```

Requirements: **Python 3.11+**, **Git**, and the **OpenCode CLI** for model
execution. The Python package itself has no third-party runtime dependencies.

---

## Why Adaptive Swarm?

Most coding agents ask you to trust a model with direct write access. Adaptive
Swarm separates **doing the work** from **accepting the result**.

| Ordinary coding agents | Adaptive Swarm v3.0.0 |
|---|---|
| Write directly into the working tree | Execute in an isolated Git worktree |
| One model reviews itself | Maker-checker councils across model families |
| A model can silently weaken tests | Frozen test hashes, test-path rejection and AST syntax checks |
| Review happens in chat history | Per-run timeline, spend, votes, diff and logbook evidence |
| The agent decides when to commit | The operator explicitly runs `swarm merge` |
| Crash means starting over | `swarm resume` recovers paused or killed runs |
| Errors are hard to undo | `swarm rollback` restores the pre-merge state |

### The safety model

- **Default Git worktree isolation.** Every execution and verification step
  runs in a worktree. The main tree is not touched during the run.
- **Explicit merge gate.** `swarm merge` applies the handoff patch only after
  re-running every plan `verify_command` and receiving exit code `0`.
- **Safe rollback.** `swarm rollback --run-id <id>` reverts a prior explicit
  merge.
- **Crash recovery.** `swarm resume --run-id <id>` continues killed or paused
  phases from the last checkpoint.
- **Anti-tamper test guard.** Test files are frozen with SHA-256 snapshots;
  coder output may not target test paths, and changed test hashes fail with
  `TEST_TAMPER`. Python proposals are also syntax/AST-checked before they reach
  the transaction writer.
- **Budget and time caps.** Per-call usage is reconciled against the reservation,
  and a hard wall-clock limit of 600 seconds prevents runaway or stalled runs.

Git worktree isolation protects **files**, not the operating system. For
untrusted workloads, use an external container or VM sandbox.

---

## Architecture v3.0.0

```text
       +-------------------------------+
       |  swarm CLI / OpenCode / Run  |
       |  Viewer (:8501, run-scoped)   |
       +---------------+---------------+
                       |
                       v
            +----------+----------+
            | UnifiedOrchestrator |
            +----------+----------+
                       |
       preflight -> tier -> planner -> Usage
                       |
                       v
            +----------+----------+
            |  v2 Worktree Runner |
            |  budget + adaptive  |
            +----------+----------+
                       |
       execute -> verify -> checkpoint -> handoff
                       |
              +--------+--------+
              |                 |
              v                 v
       swarm merge        swarm rollback
       (explicit)         (safe revert)
```

The main components:

- **Unified Orchestrator** — owns preflight, tier selection, planning, Usage
  approval, execution and handoff.
- **Worktree Runner** — provides Git isolation, budget enforcement, adaptive
  context and verification.
- **Planner adapter** — turns a goal, v1 plan or v1 `order.json` into a validated
  v2 MasterPlan.
- **Run Viewer** — a single-run dashboard for plan, chat, verify, spend,
  delivery and the generated logbook. It does not expose run history.
- **Delivery layer** — merge and rollback are explicit operator actions.

---

## Core CLI

```bash
# Run a task through the pipeline
swarm "fix the failing add() test"

# Choose a tier or free-only preset
swarm "review the authentication flow" --tier E3 --preset free-only

# Preflight the machine and model routes
swarm doctor

# Produce a plan without executing it
swarm planner --goal "Add rate limiting" --out plan.json

# Inspect runs
swarm status --last
swarm status --run-id <id>

# Open the viewer for exactly one run
swarm ui --run-id <id> --open

# Review the handoff, then deliver explicitly
swarm merge --run-id <id>
swarm rollback --run-id <id>

# Recover a paused or killed run
swarm resume --run-id <id>
```

Useful commands:

| Command | Purpose |
|---|---|
| `swarm "task"` | Run one task through the unified pipeline |
| `swarm planner --goal "task"` | Produce an inspectable plan without executing it |
| `swarm status --last` | Show the latest run and handoff information |
| `swarm ui --run-id <id>` | Open the single-run viewer for one run |
| `swarm merge --run-id <id>` | Verify and apply a handoff to `main` |
| `swarm rollback --run-id <id>` | Revert a previous merge |
| `swarm resume --run-id <id>` | Resume a paused or interrupted run |
| `swarm doctor` | Check Git, worktrees, OpenCode and model routes |

The legacy v1 commands (`planner.py`, `job.py`, `recipe_c.py`) and the v2
`run.py` entrypoint remain in the repository as a frozen compatibility surface
and test oracle. New users should use `swarm`.

---

## Single-Run Viewer

The UI is intentionally scoped to **one run**:

```bash
swarm ui --run-id <id> --open
```

or, from a source checkout:

```bash
python .agents/dashboard.py --root . --run-id <id> --port 8501 --open
```

Windows wrapper:

```powershell
.\dashboard.cmd --run-id <id> --port 8501 --open
```

The default page without `--run-id` only prints instructions; it does **not**
show run history. To view another run, start another viewer process with that
run ID (use separate ports when viewing concurrent runs).

The viewer shows only the selected run:

- goal, tier/planner and final status;
- plan and work orders;
- per-run chat/events, filter and replay;
- verify commands, results and exit codes;
- spend/tokens and remaining budget;
- handoff diff, explicit merge and rollback;
- generated logbook.

### Per-run logbook

`swarm ui --run-id <id>` regenerates a human-readable logbook in:

```text
.agents/tmp/v2/runs/<id>/logbook.md
.agents/tmp/v2/runs/<id>/logbook.json
```

Raw events, patches and state remain the source of truth. The logbook is a
deterministic projection for humans and includes descriptions, file changes,
verification evidence, spend and delivery status.

---

## Execution Tiers and Planning Councils

### E1-E4 execution tiers

| Tier | Review behavior |
|---|---|
| **E1** | Coder only; explicit low-risk opt-in; sensitive paths blocked |
| **E2** | Coder + independent security reviewer |
| **E3** | Coder + four reviewers: security, QA, performance, docs |
| **E4** | Four-reviewer Blackboard meeting with targeted rebuttal and voting |

E3 and E4 fail closed when a required reviewer route is unavailable. A runtime
tier override cannot bypass the tier already signed into the plan and Usage
estimate.

### Planner P1-P3

| Level | Council shape |
|---|---|
| **P1** | One planning seat |
| **P2** | Proposal + critique |
| **P3** | Three-seat planning council |

Planning councils validate target files, dependencies, scope and verification
commands before execution begins. Optional model-backed seats can be enabled
with `--planner-model`.

### Free-only model stack

The full pipeline can run on free model seats:

- DeepSeek: `opencode/deepseek-v4.1-flash-free`
- GLM: `opencode/glm-5.3-flash-free`
- Nemotron: `opencode/nemotron-3.5-lightning-free`

A typical maker-checker setup uses DeepSeek for coding and GLM for audit or
review. Paid models require explicit opt-in; `--preset free-only` locks the run
to free seats.

---

## Verification Gate

v3.0.0 is tested as a release, not just as a collection of files.

| Gate | Evidence |
|---|---|
| Full regression | **58/58 test files pass**, 0 failures, exit code 0 |
| v1/v2 parity | **45/45**, three deterministic fixtures |
| Crash and resume | **11/11** process-kill/recovery checks |
| Phase 4B delivery | **14/14** merge/rollback/soak checks |
| Unified orchestrator | **63/63** |
| Unified contracts | **40/40** |
| Swarm CLI | **44/44** |
| E1-E4 runtime | **40/40** |
| E2/P1 live smoke | `HANDOFF_READY`, verify exit 0, explicit merge recorded |
| Long-run resilience | **459/459** fake-mode cycles in an 8-hour soak |
| Post-Phase 4 smoke | **3/3** `HANDOFF_READY` cycles |

The release also verifies that `main` remains unchanged until explicit merge and
that a runtime tier override cannot bypass the signed plan estimate.

---

## Free and Paid Models

The default setup is free-only. `scout` discovers the available OpenCode models
and writes a local, machine-specific `.agents/config.json`:

```bash
python .agents/scout.py --write --verify
```

Paid models are never selected unless explicitly enabled:

```bash
python .agents/scout.py --write --allow-paid --verify
```

Use `--preset free-only` when you want the run itself to enforce the free-only
boundary.

---

## Development

Run the full local gate:

```bash
python -m pip install -e ".[dev]"
python -m build
python -m twine check dist/*

# Run the release regression suite
python .agents/tests/test_v1_v2_parity.py
python .agents/tests/test_phase4b.py
python .agents/tests/test_phase_process_crash.py
python .agents/tests/test_unified_orchestrator.py
python .agents/tests/test_swarm_cli.py
```

The complete suite consists of all `test_*.py` files under `.agents/tests/`.

Key project documents:

- [Operator guide](docs/OPERATOR_GUIDE.md)
- [Release notes](docs/RELEASE_NOTES.md)
- [Unified engine masterplan](docs/UNIFIED_ENGINE_MASTERPLAN.md)
- [v1 feature parity matrix](docs/V1_FEATURE_PARITY.md)
- [Blind evaluation specification](docs/BLIND_EVALUATION_SPEC.md)
- [Adaptive toolkit](docs/ADAPTIVE_TOOLKIT.md)
- [OpenCode skill setup](docs/OPENCODE_SKILL_SETUP.md)
- [Install guide](docs/INSTALL.md)

---

## Repository Layout

| Path | Purpose |
|---|---|
| `.agents/v2/swarm_cli.py` | Official `swarm` CLI |
| `.agents/unified/orchestrator.py` | Unified pipeline |
| `.agents/unified/contracts.py` | Unified request/result contracts |
| `.agents/unified/state.py` | v2 state plus read-only v1 history |
| `.agents/unified/api.py` | Unified API for the single-run viewer |
| `.agents/unified/logbook.py` | Per-run Markdown/JSON logbook generator |
| `.agents/v2/` | Worktree runner, budgets, tiers, planner, delivery |
| `.agents/dashboard.py` | Single-run viewer server at `127.0.0.1:8501` |
| `.agents/run_ui.html` | Single-run viewer UI (no history/War Room) |
| `.agents/tests/` | Release regression suite |
| `swarm.cmd` / `swarm.sh` | Source-checkout CLI wrappers |
| `pyproject.toml` | PEP 517 packaging and PyPI metadata |

---

## Limitations

- **No auto-merge.** This is intentional. The operator decides when a handoff
  becomes a commit on `main`.
- **Git worktree is not a VM.** It isolates files, not processes or the OS.
- **Verification is only as strong as `verify_command`.** A weak test produces
  false confidence.
- **Model availability matters.** E3/E4 abort if a required reviewer route is
  unavailable.
- **Some v1 features are deferred.** See the
  [v1 feature parity matrix](docs/V1_FEATURE_PARITY.md) for the owner-approved
  deferred list.

---

## License

MIT. See [LICENSE](LICENSE).

Built with AI assistance; every release change is machine-verified.
