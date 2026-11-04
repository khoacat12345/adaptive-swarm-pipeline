# Adaptive Swarm Pipeline Constitution

> Version: 1.0.0
> Ratified: 2026-11-04
> Last amended: 2026-11-04

This constitution is a machine-bound project artifact. A sign-off records its
SHA-256 hash; any change requires a new approval.

## I. Safety boundary first

- v2.1 project writes happen only in a guarded Git worktree.
- The main worktree is never reset, cleaned, or checked out by the runner.
- `safe_writer` remains the only approved write path for patch-mode runtimes.
- Agentic runtimes must use an explicitly approved transactional workspace mode.
- No automatic merge. A run ends in handoff.

## II. Machine evidence over agent claims

- A phase is complete only when its `verify_command` exits `0` in the worktree.
- An agent saying "done" is not evidence.
- The ledger is append-only; reports are derived from evidence, not memory.
- Security blockers are hard stops; they are never overridden by a vote.

## III. Budget before every model call

- Requests, cost, tokens and wall-clock are reserved before a provider call.
- A call that cannot fit the phase or global envelope must not start.
- Unknown paid cost is blocked unless an explicit estimate is signed.
- Gateway fallback must be recorded and cost-accounted.
- Phase budget cannot be raised silently during a run.

## IV. Immutable intent

- The signed plan, budget, model snapshot and constitution hash are immutable.
- Any change to these artifacts returns the run to `AWAITING_SIGNOFF`.
- There is no "minor plan edit" exception.
- Canonical machine artifacts use explicit schemas; prose is not a contract.

## V. Specification before execution

- Specs describe requirements and acceptance criteria.
- Plans describe phases, dependencies, allowed paths and verification.
- Tasks are machine-readable work orders with `verify_command` and target files.
- `tasks.md` may remain human-readable, but execution uses `tasks.yaml`/JSON.
- If the bridge cannot validate an artifact, it stops; it does not guess.

## VI. Isolated, auditable change

- Each phase has a checkpoint and an evidence trail.
- Failed work is quarantined before rollback.
- Test files and verify commands are frozen after sign-off.
- Cross-family reviewers must be independent of the coder.
- Handoff includes patch, report, checkpoints and unresolved risks.

## VII. Least privilege and secret safety

- Secrets never enter prompts, logs, patches or worktrees.
- Providers and gateways are trusted only within an explicit, documented boundary.
- Credentials stay machine-local.
- OS/container sandboxing is a future boundary; worktree isolation alone is not
  a claim of complete process isolation.

## VIII. Compatibility and reversible rollout

- v1 remains available as the default until v2.1 passes its live pilot and soak.
- Runtime adapters are additive; they must not weaken the control plane.
- No client, gateway or model may bypass worktree, budget or verify gates.
- Every release documents its known gaps and rollback path.

## Governance

- Amendments require a reviewed commit, updated hash, and passing acceptance tests.
- Constitution violations are highest-severity findings.
- The project owner approves any change that expands runtime or provider authority.
