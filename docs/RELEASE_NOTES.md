# Adaptive Swarm Pipeline — Release Notes

## v2.1.0 Release Candidate

**Release status:** release candidate (v2.1.0)
**Default execution path:** `v1.0.5`
**v2 path:** opt-in, isolated preview
**Last updated:** 2026-11-07

This document is the release story for the v2.1.0 release candidate. It replaces
the older `RELEASE_v2.1.0.md` summary while keeping that file as historical
context.

## v1.0.5 vs v2.1.0

| Area | v1.0.5 (default) | v2.1.0 (opt-in preview) |
|---|---|---|
| Entry point | `job.py`, `recipe_c.py`, dashboard | `v2/run.py`, `/v2` dashboard page |
| File isolation | `safe_writer` backups/transactions | guarded Git worktree + `safe_writer` |
| Verification | `verify_command` before apply | `verify_command` in isolated worktree, then explicit merge gate |
| Reviewers | panel/wave/meeting policies | four mandatory v2 reviewers, cross-family checks |
| Budget | request/time caps | reserve/settle per call, per phase and global caps |
| Crash recovery | transaction journal | v2 state recovery + worktree quarantine |
| Delivery | manual acceptance | explicit `v2 merge` / `v2 rollback`; no auto-merge during execution |
| Adaptive context | selected v1 paths | uniform repo-map, ACI, lessons, bounded memory, event runtime across E1–E4 |
| Default safety claim | stable production path | release candidate; keep v1 for critical work until accepted |

## What is new in this release candidate

- Explicit **one-click delivery** commands:
  ```powershell
  python .agents\v2\run.py merge --run-id <id>
  python .agents\v2\run.py rollback --run-id <id>
  ```
  Merge accepts `HANDOFF_READY` or accepted `DONE` runs, validates every patched path against the
  signed plan, runs every `verify_command`, and commits only after exit code 0.
  If verification fails, the tree is restored. Rollback uses a safe Git revert
  of the recorded merge commit.
- **Presets** at prepare time:
  ```powershell
  python .agents\v2\run.py prepare --goal "task" --preset free-only
  python .agents\v2\run.py prepare --goal "task" --preset strict
  ```
  `free-only` locks seats to free models and blocks paid models. `strict`
  keeps four reviewers, secret scanning, and lowers the Usage approval gate to
  one request / $0.01.
- `/v2` spend reconciliation with estimated vs actual tokens, requests, cost,
  reserved budget, remaining budget, handoff runs and web diff preview.
- Safe Telegram/webhook alerts for `USAGE_WAITING`, `HANDOFF_READY`,
  `BUDGET_OVERRUN` and `CRASH`, with secret/path redaction.
- `v2 doctor` preflight and `v2 prune` worktree retention.
- Uniform adaptive integration for the five extracted capabilities:
  1. repo map (aider-style),
  2. read-only ACI (SWE-agent-style),
  3. failure lessons (mem0-style),
  4. bounded tiered memory (Letta-style),
  5. event/observation runtime (OpenHands-style).
  They are implemented as dependency-free primitives and injected into v2
  E1–E4 execution. No new runtime dependency is required.

## Security boundary (P2-05)

**Git worktree protects project files, not the operating system.** A v2 run
uses an isolated worktree and `safe_writer`, but a model with shell access can
still perform OS-level actions. v2.1.0 does **not** claim an OS/container/VM
sandbox. Use an external container, VM or OS user boundary when the workload is
untrusted. Merge and rollback are explicit operator commands, never automatic
during execution.

## Adaptive integration (P2-10 / P2-11)

The five capabilities above are wired into the v2 runtime with evidence from:

- `test_capabilities_uniform.py` — E1–E4 capability injection
- `test_v2_adaptive.py` / `test_v2_adaptive_flags.py`
- `test_v2_aci_integration.py`
- `test_v2_lessons_integration.py`
- `test_v2_planner_repo_map.py`
- live v2 free-model pilot and Spec Kit signed execution

Deferred, non-blocking improvements (tree-sitter/tags cache, richer ACI tool
bundles, macOS CI runner) are tracked in `OPEN_PROBLEMS.md` but are not release
blockers for the v2.1.0 candidate.

## Verification

```text
V2 M1                 43/43 PASS
V2 M2                 47/47 PASS
V2 M3                 25/25 PASS
V2 M5                 14/14 PASS
V2 P0                 15/15 PASS
V2 PARITY             11/11 PASS
V2 DELIVERY           13/13 PASS
V2 PRESETS            14/14 PASS
V2 WEB UI             23/23 PASS
V2 ALERTS              5/5  PASS
V2 DOCTOR             17/17 PASS
V2 PRUNE              10/10 PASS
CAPABILITIES UNIFORM  23/23 PASS
SPECKIT CONVERGE      11/11 PASS
```

## Known limits

- v2.1.0 is a release candidate, not the default path. v1.0.5 remains the
  conservative default until the owner accepts the v2 release.
- Merge is explicit and local; there is no automatic deployment or remote
  branch protection.
- Git worktree is not an adversarial OS sandbox.
- Notifications are optional; disabled configuration fails closed by skipping.
- `v2 prune` preserves run evidence but does not implement disk quotas or a
  remote archive tier.

## Upgrade / rollback

- v1 users need no migration.
- v2 state lives under `.agents/tmp/v2/`; keep it out of version control.
- To undo an accepted v2 delivery, run `v2 rollback --run-id <id>`; this
  records a revert commit and preserves the original handoff and merge records.
- To retain the ledger while reclaiming disk, run `v2 prune --days N --dry-run`
  before removing worktrees.
