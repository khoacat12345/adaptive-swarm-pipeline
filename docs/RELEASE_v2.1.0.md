# Adaptive Swarm Pipeline v2.1.0

> Historical initial v2.1.0 notes. For the current release-candidate matrix,
> explicit merge/rollback, presets, alerts and adaptive completion, see
> [`RELEASE_NOTES.md`](RELEASE_NOTES.md).

**Release type:** opt-in foundation release
**Baseline:** v1.0.5 remains the default execution path
**Specification:** [`SWARM_V2.1_SPEC.md`](SWARM_V2.1_SPEC.md)

## What is included

v2.1.0 packages the isolated autonomous foundation built across M1–M5:

- guarded Git worktree; the main worktree is not written by v2.1;
- per-call and per-phase budget reservations, wall-clock and repair caps;
- three-seat planning council and immutable plan/budget/model sign-off;
- execution engine with `safe_writer.Transaction`, test-file hash freezing,
  secret scanning, cross-family reviewers and a security hard veto;
- pause/resume/stop/handoff-accept controls and a local `/v2` page;
- catalog/auth-aware free-model selection and paid-cost blocking;
- deterministic M1/M2/M3/M5 acceptance tests.

## Quick start

```bash
# Windows uses python instead of python3
python3 .agents/scout.py --write --verify
python3 .agents/v2/run.py prepare --goal "your task" --phase P1
python3 .agents/v2/run.py plan --run-id <id> --plan plan.json
python3 .agents/v2/run.py signoff --run-id <id>
python3 .agents/v2/run.py execute --run-id <id> --free
```

The local control surface is:

```text
http://127.0.0.1:8080/v2
```

The token is printed by `prepare` and is required for dashboard sign-off and
control.

## Model/provider selection

- `scout` reads the local OpenCode catalog and auth status into
  `.agents/config.json`.
- `--free` selects free models from that catalog; it does not hardcode provider
  names.
- Explicit provider/model IDs can be passed with `--coder-model` and
  `--reviewer role=model`.
- A paid model is blocked unless a per-call cost estimate is supplied with
  `--paid-cost`; request, wall-clock and phase caps remain hard gates.

## Verification

```text
V2 M1: 43/43 PASS
V2 M2: 38/38 PASS
V2 M3: 24/24 PASS
V2 M5: 14/14 PASS
Phase 4 full: 36/36 PASS
Phase 6A: 72/72 PASS
Skill consistency: 16/16 PASS
CI cross-platform: PASS
```

## Known limits

- The live free-model pilot and the 8–10 hour wall-clock soak have **not** been
  run in this release. M5 currently provides the deterministic acceptance
  harness and fault-injection tests.
- No automatic merge. A successful run produces a guarded worktree/branch plus
  `handoff.patch` and `report.md`.
- Git worktree isolation protects files, not the OS. Tests can still perform
  OS-level actions; a container/VM sandbox is future work.
- Notifications are optional and not part of v2.1.0.
- v1.0.5 remains the default until the live pilot is reviewed.

## Upgrade notes

No migration is required for v1. v2.1 state lives under
`.agents/tmp/v2/`; worktrees live outside the repository. A v2 run requires a
clean main worktree and an explicit sign-off.
