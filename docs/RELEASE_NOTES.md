# Adaptive Swarm Pipeline — Release Notes

## v3.0.0 — Unified Engine

**Release status:** production release
**Release date:** 2026-11-10
**Default engine:** v2 runtime behind the unified `swarm` entrypoint
**Frozen fallback:** `v1.0.5` tag
**Historical tags:** `v2.1.0` remains untouched

v3.0.0 is the first release where the v2 worktree/budget engine, the unified
orchestrator, the War Room, and the human-facing CLI are one product.

## The journey

```text
v1.0.5       legacy direct-write workflow (frozen fallback)
v2.1.0       opt-in isolated foundation and /v2 page
v3.0.0       unified engine: swarm + War Room + v2 runtime
```

The v1 commands remain in the repository as a deterministic test oracle and
emergency compatibility path. They are not the new-user interface.

## What is new

### Unified Orchestrator
- One pipeline:
  ```text
  preflight → classify/tier → planner → Usage gate
  → isolated worktree → execute → verify → checkpoint → handoff
  ```
- `swarm "task"` is the official entrypoint.
- The orchestrator never auto-merges; delivery is explicit.

### E1–E4 execution tiers
| Tier | Review |
|---|---|
| E1 | coder only, explicit low-risk opt-in |
| E2 | coder + security reviewer |
| E3 | coder + four independent reviewers |
| E4 | four-reviewer Blackboard meeting |

Planner levels P1–P3 are selected with the tier. P2/P3 councils execute
deterministic seats by default; optional model-backed planner seats are
available through `--planner-model`.

### War Room
- One dashboard at `http://127.0.0.1:8501`.
- `--port 8080` remains a compatibility alias.
- Shows run timeline, tier/planner, spend/tokens, Usage approval, E4
  Blackboard, handoff diff, explicit merge/rollback and run history.
- API endpoints are under `/api/unified/`.
- Loopback-only binding, token-bound writes, secret redaction and diff caps.

### Delivery and resilience
- Explicit `swarm merge` with a verify gate.
- `swarm rollback` restores the pre-merge state.
- `swarm resume` recovers a killed phase or resumes a paused run.
- Git worktree isolation and safe_writer remain mandatory.
- Free-only and strict presets remain available.

### Parity and evidence
- Three deterministic parity fixtures compare v1 legacy, v2 without merge, and
  v2 with explicit merge: single-file, multi-file, and security-sensitive.
- Live free-model smoke passed:
  ```text
  run: v2_20261110T070617_56c0a0
  tier: E2 / P1
  status: HANDOFF_READY
  verify: exit 0
  merge: b347e725a4777588019917b8b7c16d9ea923141c
  ```
- Process-kill/recover and merge/rollback tests pass.
- Soak evidence: full 8-hour fake soak (2026-11-07) completed 459/459 cycles
  `HANDOFF_READY`; the post-Phase 4 short smoke added 3/3 cycles. Product Owner
  accepted the Phase 4 evidence for release.

## Verification

```text
V1/V2 PARITY          45/45 PASS
PHASE 4B              14/14 PASS
PHASE CRASH           11/11 PASS
UNIFIED ORCHESTRATOR  63/63 PASS
UNIFIED CONTRACTS     40/40 PASS
SWARM CLI             44/44 PASS
V2 TIERS              40/40 PASS
V2 DELIVERY           13/13 PASS
V2 M1/M2/M3/M5        PASS
Full repository       56 test files, 0 failures
```

Final gate:

```powershell
python .agents\tests\test_v1_v2_parity.py
python .agents\tests\test_phase4b.py
swarm --version   # swarm 3.0.0
```

## Install / first task

```powershell
git clone https://github.com/khoacat12345/adaptive-swarm-pipeline.git
cd adaptive-swarm-pipeline
python .agents\scout.py --write --verify
swarm doctor
swarm "fix the failing add() test"
```

In OpenCode:

```text
/swarm fix the failing add() test
```

## Delivery boundary

- Main is untouched during execution.
- Review `swarm status --last`.
- Apply with `swarm merge --run-id <id>`.
- Undo with `swarm rollback --run-id <id>`.
- Git worktree protects files, **not the OS**. v3.0.0 does not claim a
  container/VM sandbox.

## Upgrade / rollback

- `v1.0.5` remains available as an emergency fallback tag.
- Do not delete or retag `v2.1.0`; it is historical evidence.
- New installations should use `swarm`; old v1 commands are compatibility-only.
- Blind Evaluation has a detailed implementation specification in
  `docs/BLIND_EVALUATION_SPEC.md`; it is not part of the v3.0.0 runtime yet.
