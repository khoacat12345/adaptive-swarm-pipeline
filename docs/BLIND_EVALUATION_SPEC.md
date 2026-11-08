# Blind Evaluation — Implementation Specification

> Status: **design specification**, not implemented yet.
> Version: `1.0` draft
> Last updated: 2026-11-08
> Related: [`ADAPTIVE_TOOLKIT.md`](ADAPTIVE_TOOLKIT.md),
> [`RESEARCH_BACKLOG.md`](RESEARCH_BACKLOG.md),
> [`UNIFIED_ENGINE_MASTERPLAN.md`](UNIFIED_ENGINE_MASTERPLAN.md).

---

## 1. Purpose

Blind Evaluation measures whether one agent/model/pipeline configuration is
actually better than another **without letting the evaluator know which
candidate produced which artifact**.

The feature is for:

- comparing v2 E1/E2/E3/E4 configurations;
- comparing free vs paid model seats;
- comparing planner/model/preset combinations;
- comparing v1.0.5 legacy behavior with v2 on the same fixture;
- regression evaluation before a release;
- human or LLM judging of code quality when machine verification alone is not
  enough.

The feature is **not**:

- a leaderboard of public model names;
- a replacement for `verify_command`;
- an auto-merge mechanism;
- a promise that a model is “best” from a small sample;
- a way to hide a failed verification result.

Core rule:

> A candidate is done only when an independent verify command exits `0`.
> Blindness applies to quality judging, not to the machine gate.

---

## 2. Definitions

| Term | Meaning |
|---|---|
| **Eval run** | One immutable evaluation session with a task pack and candidates. |
| **Task pack** | Frozen set of fixtures, prompts, targets and verify commands. |
| **Candidate** | A configuration under test (engine, tier, model, preset). |
| **Artifact** | The diff, files, verify result and metrics produced by one candidate on one task. |
| **Blind label** | Random opaque identifier shown to judges, e.g. `cand-3f9a`. |
| **Blinding key** | Local mapping from blind label to real candidate, hidden until reveal. |
| **Judge** | Machine verifier, human reviewer or LLM reviewer scoring an artifact. |
| **Reveal** | Explicit operation that unlocks the label→candidate mapping. |
| **Score** | Machine result and/or rubric result for one artifact. |

---

## 3. Design principles

1. **Machine gate first.** A failed `verify_command` cannot be rescued by a high
   style score.
2. **Blind by default.** Candidate names, model IDs, engine versions and paths
   are removed from judge packets.
3. **One variable per comparison.** Do not compare different tasks, budgets or
   retry counts while claiming a model difference.
4. **Frozen tasks.** Task instructions, tests and verify commands are hashed
   before any candidate runs.
5. **No main changes.** Every candidate runs in a disposable worktree or temp
   repository.
6. **Deterministic where possible.** Seeded label assignment and run order.
7. **No identity leakage through metadata.** Diffs, logs, timestamps, branch
   names, session IDs and model names are sanitized before judging.
8. **Small samples are not significance.** Report confidence intervals and
   sample sizes.
9. **Unavailable is not failed.** Quota/auth/timeout is recorded separately.
10. **Reveal is deliberate.** The key is not included in reports or judge
    packets.

---

## 4. Proposed file layout

```text
.agents/
  blind_eval.py                 # CLI entrypoint
  v2/
    blind_eval.py               # core implementation
    blind_eval_tasks.py         # task-pack loader/validator
    blind_eval_runner.py        # candidate execution
    blind_eval_judge.py         # human/LLM judge adapters
    blind_eval_stats.py         # rates, intervals, pairwise statistics
  tests/
    test_blind_eval.py
    test_blind_eval_runner.py
    test_blind_eval_judge.py
  tmp/
    blind-eval/<eval_id>/       # runtime only; ignored by git
```

Optional dashboard surface later:

```text
GET /api/blind-eval/runs
GET /api/blind-eval/runs/<id>
POST /api/blind-eval/runs/<id>/judge
POST /api/blind-eval/runs/<id>/reveal
```

---

## 5. Runtime state layout

```text
.agents/tmp/blind-eval/<eval_id>/
├── eval.json                   # immutable run manifest
├── task-pack.json              # copied task pack + hash
├── key.json                    # label -> candidate mapping (secret)
├── labels.json                 # public labels only
├── runs/
│   └── <blind_label>/
│       ├── task-<task_id>/
│       │   ├── artifact.diff
│       │   ├── artifact-files.json
│       │   ├── verify.json
│       │   ├── metrics.json
│       │   └── redactions.json
├── judge-packets/
│   └── <blind_label>/<task_id>.json
├── scores/
│   └── <judge_id>.json
├── report.md
└── report.json
```

`key.json` requirements:

- mode `0600` where the OS supports it;
- never included in report or judge packet;
- not printed by `status`;
- only read by `reveal` and internal scoring;
- if the OS cannot enforce permissions, warn loudly and require `--allow-insecure-key`
  for local-only evaluation.

---

## 6. Task pack schema

The task pack must be immutable during an eval run.

```json
{
  "schema_version": "1.0",
  "pack_id": "core-coding-v1",
  "description": "Small deterministic coding fixtures",
  "tasks": [
    {
      "id": "T01",
      "title": "Fix add()",
      "goal": "Make add(a,b) return a+b without changing the tests",
      "fixture": "fixtures/t01",
      "base_sha": "optional-fixture-commit",
      "target_files": ["calc.py"],
      "allowed_paths": ["calc.py"],
      "forbidden_paths": [".git", ".env", "tests/"],
      "verify_command": "python check.py",
      "hidden_tests": ["hidden/test_calc.py"],
      "timeout_seconds": 300,
      "cost_cap_usd": 0.20,
      "tags": ["python", "logic"],
      "tier_hint": "E2"
    }
  ]
}
```

Validation rules:

- `verify_command` is mandatory and non-empty.
- `target_files` must exist or be explicitly created by the task.
- `hidden_tests` must never be copied into a candidate worktree before judging.
- The pack hash is recorded before execution and rechecked before report.
- A changed pack aborts the run.
- Task IDs are unique and deterministic.

---

## 7. Candidate schema

```json
{
  "id": "v2-free-e2",
  "label": null,
  "engine": "v2",
  "tier": "E2",
  "preset": "free-only",
  "coder_model": "opencode/deepseek-v4.1-flash-free",
  "reviewers": {
    "security": "opencode/glm-5.3-flash-free"
  },
  "planner": "P1",
  "max_rounds": 1,
  "enabled": true
}
```

Rules:

- `id` is never shown to a blind judge.
- Missing reviewer seats are allowed only when the tier contract permits it.
- A candidate cannot override the task's verify command.
- Cost/time caps are per task and per eval.
- `v1.0.5` legacy candidates may be included only as a reference; they still run
  in a disposable copy, not in the main worktree.

---

## 8. Blinding algorithm

### 8.1 Label assignment

For each eval:

```text
seed = explicit --seed or random 32-byte value
candidate_order = deterministic_shuffle(candidates, seed)
label[i] = "cand-" + hash(seed, candidate_order[i].id, salt)[:8]
```

Requirements:

- Labels are opaque and do not encode model/tier/index.
- Label mapping is written only to `key.json`.
- Seed and mapping are recorded for reproducibility.
- If a label already exists in the eval directory, abort; never reuse a label
  across runs.

### 8.2 Artifact sanitization

Before creating a judge packet, remove or replace:

| Leak source | Action |
|---|---|
| branch names (`swarm/<run_id>`) | replace with `worktree` |
| commit hashes | replace with stable artifact hash |
| model IDs / provider names | remove |
| engine names (`v1`, `v2`) | remove |
| session IDs, run IDs | remove |
| timestamps / durations | remove from quality packet |
| absolute paths / home directories | replace with relative paths |
| `.git` metadata | exclude |
| backup/log/ledger files | exclude |
| secrets and sensitive paths | run `redact_secrets`; fail closed if unresolved |

Cost/token/latency metrics are withheld from quality judges and shown only in
the final technical report, because they can reveal configuration identity.

### 8.3 Pairwise order randomization

For pairwise A/B:

```text
presentation_order = seeded_coin_flip()
judge sees: Option 1 / Option 2
internal key: option1_label, option2_label
```

Both orders must be evaluated across the sample to reduce position bias. Record
the order per comparison, not in the packet shown to the human judge.

---

## 9. Candidate execution

### 9.1 Isolation

For each candidate × task:

1. Create a fresh temp repo/fixture copy.
2. Apply seed files.
3. Verify base `verify_command` fails as expected, if the task declares a
   failing baseline.
4. Create an isolated Git worktree or temp directory.
5. Run the candidate through the v2 runner/orchestrator.
6. Run independent `verify_command`.
7. Capture diff, files, test result, cost, time and errors.
8. Delete the worktree after evidence is captured.
9. Never merge into main.

### 9.2 Fairness rules

- Same task pack and same base fixture for all candidates.
- Same timeout unless the candidate declares a different cost/time policy, in
  which case both are reported.
- Same number of repair rounds.
- Same verify timeout.
- No candidate sees another candidate's output.
- Quota/auth failure is `unavailable`, not `fail`.
- A failed verify is `fail`, regardless of style score.

### 9.3 Evidence record

```json
{
  "label": "cand-3f9a",
  "task": "T01",
  "status": "passed|failed|unavailable|timeout",
  "verify_exit": 0,
  "verify_output_sha256": "...",
  "artifact_sha256": "...",
  "files_changed": 1,
  "diff_lines": 12,
  "requests": 2,
  "tokens_in": 1234,
  "tokens_out": 345,
  "cost_usd": 0.0,
  "duration_seconds": 19.4,
  "safety_findings": [],
  "error": null
}
```

---

## 10. Judges

### 10.1 Machine judge

Mandatory and non-blindable:

- `verify_command` exit code;
- hidden tests, if configured;
- secret scan;
- path policy;
- test-file integrity.

A machine failure is shown to report readers as a hard failure, even if a human
or LLM judge gives a high style score.

### 10.2 Human judge

The UI/CLI shows:

- task description;
- anonymized diff/files;
- verify result (`pass`/`fail` only);
- rubric.

The human judge never sees:

- candidate label mapping;
- model/provider names;
- engine/tier;
- cost/time;
- other candidates' scores.

Rubric (0–4 each):

| Dimension | What it measures |
|---|---|
| correctness | likely behavior and edge cases |
| scope | only requested files/behavior changed |
| security | no secret/path/auth regression |
| maintainability | clear, minimal, idiomatic change |
| tests | adequate proof and no test tampering |

### 10.3 LLM judge

Optional, but must be blinded and constrained:

- judge prompt includes only sanitized task + patch;
- no model/tier/cost metadata;
- JSON-only response with dimension scores and reasons;
- judge must not be the same model ID as the candidate when a different-family
  judge is available;
- record judge model and confidence, but keep candidate label hidden in the
  quality packet;
- cross-family judge is the default for pairwise comparisons;
- LLM judge cannot override machine verify.

Suggested judge output:

```json
{
  "correctness": 3,
  "scope": 4,
  "security": 4,
  "maintainability": 3,
  "tests": 2,
  "verdict": "pass",
  "reason": "short evidence-based rationale"
}
```

### 10.4 Judge quality controls

- Duplicate one hidden gold artifact among packets to detect inattentive judges.
- Track inter-rater agreement (Cohen's kappa or simple agreement).
- Flag judges who approve a failed-verify artifact.
- Do not publish a leaderboard from fewer than the configured minimum sample
  size.
- Record judge version/hash.

---

## 11. CLI

Proposed entrypoint:

```text
python .agents/blind_eval.py <command>
```

Or through the unified CLI:

```text
swarm eval blind <command>
```

Commands:

```text
create      create an eval run from a task pack and candidates
run         execute candidate × task matrix
label       print public blind labels only
judge       score blinded packets
reveal      reveal label → candidate mapping
report      render Markdown/JSON report
status      show progress without leaking the key
validate    validate a task pack or candidate file
```

Examples:

```powershell
python .agents\blind_eval.py validate --pack task-pack.json

python .agents\blind_eval.py create `
  --name "e2-vs-e3" `
  --pack task-pack.json `
  --candidate candidates.json `
  --seed 20261108 `
  --out .agents\tmp\blind-eval\e2-vs-e3

python .agents\blind_eval.py run `
  --eval .agents\tmp\blind-eval\e2-vs-e3 `
  --max-cost 2.00 `
  --concurrency 1

python .agents\blind_eval.py judge `
  --eval .agents\tmp\blind-eval\e2-vs-e3 `
  --judge human

python .agents\blind_eval.py reveal `
  --eval .agents\tmp\blind-eval\e2-vs-e3

python .agents\blind_eval.py report `
  --eval .agents\tmp\blind-eval\e2-vs-e3 `
  --format md
```

`status` must show labels and counts only. It must never show model/tier identity
before `reveal`.

---

## 12. Reports

### 12.1 Blind report

Safe to share with judges:

```text
Candidate   Tasks  Verify pass  Machine score
cand-3f9a   3/3    3/3          1.00
cand-7b21   3/3    2/3          0.67
```

### 12.2 Revealed report

Only after explicit reveal:

```text
label       candidate          engine  tier  model/preset        pass rate
cand-3f9a   v2-free-e2         v2      E2    deepseek-free       1.00
cand-7b21   v2-strict-e3       v2      E3    glm-strict          0.67
```

Include:

- pass@1 and sample size;
- Wilson 95% confidence interval;
- per-task breakdown;
- safety findings;
- unavailable/timeouts;
- actual cost/time;
- judge agreement;
- explicit “not statistically significant” warning below threshold;
- task-pack hash and eval seed.

### 12.3 Pairwise report

```text
Comparison: E2 vs E3
Sample: 12 tasks
E2 wins: 6
E3 wins: 3
Ties: 3
Win rate (excluding ties): 0.667
95% Wilson interval: [0.39, 0.86]
Position bias check: 50/50 order
```

Do not call a winner when the interval crosses 0.5 or the sample is too small.

---

## 13. Statistics

Minimum implementation:

- pass rate = passed / attempted.
- unavailable excluded from denominator but reported separately.
- 95% Wilson interval for pass rate.
- pairwise win rate excluding ties.
- per-task deltas.
- mean/median duration and cost.
- judge agreement.

Optional later:

- Bradley–Terry / Elo with confidence intervals;
- bootstrap resampling;
- paired tests across the same task;
- cost-quality Pareto frontier.

No statistical claim without:

1. at least the configured minimum tasks per candidate;
2. a recorded seed and task hash;
3. reported sample size and interval.

---

## 14. Security and leakage model

### Threats

- judge sees model name in a diff/comment;
- absolute path reveals user/project;
- branch name or commit message reveals engine;
- cost/token reveals model;
- candidate reads hidden tests;
- candidate changes test files or verify command;
- report accidentally includes `key.json`;
- blind judge packet differs between candidates in a way that leaks order;
- UI reveals labels before scoring is complete.

### Controls

- Sanitize all artifacts before packet creation.
- Copy only allowed paths into the packet.
- Exclude `.git`, `.agents/tmp`, backups, ledger, logs.
- Hash and freeze tests/verify commands.
- Require clean fixture per candidate.
- Store key separately with restrictive permissions.
- Do not include key in report, status, or logs.
- Require explicit `reveal` command after scoring.
- Redact secrets/paths with the existing scanner.
- Keep local-only by default.
- Fail closed if sanitization cannot prove a field is safe.

---

## 15. Integration points

### v2 runner

- Use v2 worktree/isolation and safe_writer.
- Use the tier contract E1–E4.
- Record candidate execution events in the eval artifact, not the main ledger.
- Never call `merge`.

### `swarm`

Add:

```text
swarm eval blind ...
```

but keep blind eval usable as a standalone script when the agent CLI is not in
the loop.

### Existing eval harness

`eval_suite.py` and `benchmark.py` can be reused for task execution and model
cost, but they do **not** provide blinding, judge packets or reveal controls.
This spec must wrap them, not pretend they are already blind.

### Dashboard (later)

- `/eval` list with labels only.
- judge panel with task/diff/rubric.
- reveal button after scores are sealed.
- separate technical metrics tab.

---

## 16. Implementation phases

### Phase B1 — Core blind runner

- task pack loader and validator;
- seeded candidate labels;
- disposable fixture/worktree runner;
- artifact sanitizer;
- key file and status;
- JSON/Markdown blind report.

### Phase B2 — Judges

- human rubric CLI;
- LLM judge adapter with cross-family preference;
- machine verify integration;
- judge agreement checks.

### Phase B3 — Statistics and reveal

- Wilson intervals;
- pairwise order randomization;
- reveal command;
- technical report with cost/time.

### Phase B4 — UI and CI

- dashboard panel;
- CI fixture pack;
- regression gate for v3.

---

## 17. Test plan

### Unit

- pack hash changes abort;
- candidate labels are opaque and unique;
- seed reproduces label order;
- sanitizer removes model/provider/engine/branch/path/timestamp;
- key is not in judge packet;
- key file permissions warning path;
- Wilson interval math;
- tie/unavailable accounting;
- reveal requires explicit command.

### Integration

- two fake candidates × two tasks;
- one passes verify, one fails;
- judge sees only labels;
- human/LLM judge packet contains no identity;
- hidden tests are not present in candidate worktree;
- candidate cannot change test/verify;
- main worktree unchanged;
- unavailable candidate does not count as fail;
- cost cap stops the run.

### Security

- secret in candidate diff redacted;
- `.env` / key path blocked;
- absolute home path replaced;
- report does not include key;
- candidate cannot read other candidate's artifact;
- corrupted key fails closed.

### Cross-platform

- Windows path handling;
- POSIX shebang/wrapper;
- line endings do not change artifact hash incorrectly;
- temp directory cleanup.

---

## 18. Acceptance criteria

A v1 implementation of Blind Evaluation is done when:

- [ ] a task pack and two candidates can be validated;
- [ ] labels are random, opaque and reproducible from a seed;
- [ ] all candidates run in isolated copies;
- [ ] main worktree is unchanged;
- [ ] every artifact has an independent verify result;
- [ ] judge packets contain no candidate identity;
- [ ] `key.json` is absent from reports and status output;
- [ ] `reveal` is required to map labels;
- [ ] blind and revealed reports are generated;
- [ ] pass rate, sample size and confidence interval are reported;
- [ ] unavailable/timeouts are not silently counted as model failures;
- [ ] secrets/paths are redacted;
- [ ] tests pass on Windows and POSIX CI;
- [ ] docs explain how to interpret and **not** over-interpret results.

---

## 19. Non-goals

- No public model leaderboard without owner approval.
- No auto-merge of evaluated candidates.
- No claim that an LLM judge is objective.
- No use of hidden tests to let a coder cheat.
- No replacement for `verify_command`.
- No storing raw secrets or private user paths in eval artifacts.
- No statistical claims from tiny samples.

---

## 20. Open questions for owner

1. Should human judging be the primary v1 judge, or LLM-first?
2. Minimum sample size per candidate for a publishable report?
3. May the reveal happen automatically after all scores are written, or must it
   always be a separate command?
4. Should v1.0.5 legacy be a candidate in the first eval pack?
5. Should cost be shown to human judges after scoring or only in the technical
   report?
6. Which task pack is the first release gate: current 48 suites, SWE-bench-style
   fixtures, or a small curated pack?
7. Do we need encrypted key storage, or are local file permissions sufficient
   for the single-user release?

---

## 21. One-line definition

> Blind Evaluation is a reproducible, identity-hidden comparison harness where
> machine verification decides pass/fail, judges score anonymized artifacts, and
> the candidate mapping is revealed only after scoring is sealed.
