# Spec Kit Adoption — Specify → Plan → Tasks → Bridge → v2 → Converge

> Status: adopted and exercised on a real Git repository (`C:\Users\tuank\my-project`).
> P1-05 and P1-12 are complete.

## Pipeline

```text
Spec Kit Specify
  → Spec Kit Plan
  → Spec Kit Tasks (canonical tasks.yaml)
  → v2 bridge_spec_kit()
  → MasterPlan.json
  → Usage approval
  → v2 isolated execution (worktree + budget + verify)
  → checkpoint / handoff
  → converge (spec artifacts ↔ changed files ↔ verify evidence)
```

## Artifact contract

The bridge accepts canonical `tasks.yaml`/`tasks.json`, **not prose
`tasks.md`**. Required source artifacts are bound by SHA-256:

```text
source.spec  = specs/<feature>/spec.md
source.plan  = specs/<feature>/plan.md
source.tasks = specs/<feature>/tasks.yaml
```

The generated plan carries:

- `schema_version: 2.1`
- `plan_hash`, `budget_hash`, `model_snapshot_hash`
- `source_artifacts` hashes
- phase `allowed_paths`, `forbidden_paths`, `budget`, `work_orders`
- `verify_command` for every work order

## Commands

```bash
python .agents/v2/run.py prepare --root <project> --goal "feature" --phase P1
python .agents/v2/run.py bridge \
  --root <project> --run-id <run> \
  --tasks specs/<feature>/tasks.yaml
python .agents/v2/run.py usage-approve --root <project> --run-id <run>
python .agents/v2/run.py execute --root <project> --run-id <run> \
  --coder-model opencode/deepseek-v4.1-flash-free \
  --reviewer security=opencode/glm-5.3-flash-free \
  --reviewer qa=opencode/glm-5.3-flash-free \
  --reviewer perf=opencode/glm-5.3-flash-free \
  --reviewer docs=opencode/glm-5.3-flash-free
python .agents/v2/run.py converge --root <project> --run-id <run>
python .agents/v2/run.py handoff --root <project> --run-id <run>
```

## Safety boundaries

- Main worktree is never written; v2 execution happens in a Git worktree.
- `safe_writer` remains the only writer.
- Budget reservation/settlement and cross-family reviewer checks remain active.
- Usage approval binds the exact signed plan + estimate.
- No auto-merge; handoff is reviewed by the operator.
- Converge ignores runtime evidence under `.agents/` (backups, state, ledger).

## Real Git pilot evidence

Fixture: `C:\Users\tuank\my-project`

```text
feature          : specs/000-p0-06-pilot
task             : demo.txt value=1 -> value=2
run_id           : v2_20261107T045042_561bf2
base_sha         : cf0094d3a2fccbe5d334801dcfe06479e09df576
plan_hash        : 87023c55384cdd35bf729e5bcc1788a045e66f4302cb175d5dc6471ee016f0e8
status           : HANDOFF_READY
checkpoint       : 58ac699eccc0993b122755b7fffac5070aa9199f
verify           : exit 0
main worktree    : demo.txt = value=1 (unchanged)
execution tree   : demo.txt = value=2
converge         : ok=true, changed_files=["demo.txt"]
handoff          : handoff.patch + report.md generated
```

The test also proves that runtime artifacts in `.agents/tmp`/backups do not
count as implementation drift.

## Adoption rules

1. Do not parse free-form `tasks.md`; require canonical `tasks.yaml`.
2. Do not bypass v2 with a direct write; bridge → signoff/Usage → execute.
3. Keep `allowed_paths`, `forbidden_paths`, `verify_command` in every work order.
4. Re-run `converge` after any spec/plan/tasks edit.
5. Keep the main branch untouched until the operator accepts the handoff.
