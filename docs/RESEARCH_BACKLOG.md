# Research Backlog — Adaptive Patterns Worth Borrowing

> Purpose: a coder/reviewer shortlist of open-source mechanisms that fit the
> swarm control plane.
> Rule: **extract ideas, not frameworks**. No dependency is added without a
> separate approval and a safety review.
> Last verified: 2026-11-04 (repository locations and feature claims checked on
> GitHub).

## Verification notes

- `aider` now lives at `Aider-AI/aider`.
- `SWE-agent` now lives at `SWE-agent/SWE-agent`.
- `OpenHands` now lives at `OpenHands/OpenHands`.
- `Roo-Code` now lives at `RooCodeInc/Roo-Code`.
- `Letta`'s current source lives at `letta-ai/letta-code`; `letta` still
  describes the memory-first agent platform.
- `microsoft/autogen` is **archived** and points to `microsoft/agent-framework`.
  Use its group-chat/speaker-selection *ideas* only.
- `Significant-Gravitas/AutoGPT` is now a platform; the benchmark is the
  separate `agbenchmark` package. Do not call AutoGPT itself an eval harness.
- `DSPy` is a framework. Treat it as an offline experiment, not a runtime
  dependency.
- Repo-map token savings are workload-dependent; measure before claiming a
  percentage.

## Local source findings (downloaded repos)

These are the parts that are **not obvious from README** and matter for our
design:

### aider — `aider/repomap.py`

- Repo map is not just "list symbols": it builds a symbol/reference graph and
  uses **personalized PageRank** (`networkx.pagerank`) to rank definitions.
- It caches tree-sitter tags with mtime checks and has a map cache.
- It uses the model tokenizer, not a character approximation, and keeps a hard
  map-token budget.
- **Gap in our primitive:** `RepoMapper` currently uses AST + term scoring only.
  Next upgrade: PageRank over file/import/symbol edges + cache + exact token cap.

### SWE-agent — `tools/`

- Real ACI is a bundle of scripts, not a prompt: `windowed` (`open`, `goto`,
  `scroll_up/down`), `search` (`find_file`, `search_file`, `search_dir`),
  `edit_*` with linting, and `submit`.
- `windowed_file.py` keeps numbered line windows; observations are compact.
- `windowed_edit_linting` validates the edit and helps reject malformed/ambiguous
  replacements.
- **Gap in our primitive:** `ActionPolicy` has list/read/search/verify, but no
  line-window viewer, no edit uniqueness validation and no submit gate.

### mem0 — `mem0/memory/main.py`

- `Memory.add` can extract facts with an LLM and decide **ADD / UPDATE / DELETE**
  against related memories, with entity linking and hybrid keyword/semantic
  search.
- It is more than a vector store; the valuable part is the **memory mutation
  policy**.
- **Gap in our primitive:** `LessonStore` is append-only. Add dedupe by
  signature, latest-fix/supersede semantics, confidence and provenance.

### Letta — `letta-code`

- The current implementation is an agent harness with memory blocks and
  context/archival management, not a small Python memory library.
- **Gap in our primitive:** `ContextPacker` is a static token packer. Add
  per-tier budgets and eviction policy before using it for 8–10h runs.

### OpenHands — event/action architecture

- The valuable boundary is action -> observation -> event with a canonical SDK
  stream. Docker sandbox is useful but heavier than our worktree.
- **Gap:** our `adaptive_event` needs stable event IDs, action references and
  replay compatibility with the existing ledger.

### LiteLLM / Roo-Code / AutoGen / AutoGPT

- LiteLLM: mature router/fallback/budget; we already have 9Router/OpenCode, so
  only borrow metadata fields.
- Roo-Code: modes are **tool permission groups**, not just prompt personas.
- AutoGen: archived; group-chat selector is concept-only.
- AutoGPT: benchmark is `agbenchmark`; our `run_benchmark` is a lightweight
  local equivalent, not AutoGPT runtime.

## Selection criteria

A repository is worth studying only if it improves one of these:

1. context selection without token waste;
2. execution safety or action boundaries;
3. learning from failed runs;
4. long-term memory with provenance;
5. provider/runtime portability;
6. measurable evaluation.

It is **not** a reason to add LangGraph, DSPy, Mem0 or another orchestration
framework to the runtime.

---

## Top 10 — one feature selected per repository

| # | Repository | Selected feature | Why it fits | Priority | Guardrail |
|---|---|---|---|---|---|
| 1 | `Aider-AI/aider` | **Repo Map**: tree-sitter symbols + relevance ranking + token cap | Planner chooses target files without dumping the repo | **P0 next** | Cap tokens; exclude secrets; measure accuracy on this repo |
| 2 | `SWE-agent/SWE-agent` | **ACI**: typed actions + structured observations; no free shell | Aligns with `system_bot`/worktree safety | **P0/P1** | Deny-by-default action allowlist; no mid-run shell invention |
| 3 | `stanfordnlp/dspy` | **Optimizer**: metric-driven prompt/few-shot tuning | Improves prompts only after a baseline exists | **P1 research / P2 runtime** | Offline only; dataset + cost + approval required |
| 4 | `mem0ai/mem0` | **Context-aware memory**: extract useful lessons, retrieve only relevant ones | Turn repeated failures into hints | **P1** | Local JSONL first; no secrets/raw code; memory cannot override verify |
| 5 | `OpenHands/OpenHands` | **Event stream**: action -> observation -> status event | Improves replay/audit beyond current ledger | **P1/P2** | Add fields, do not replace ledger; Docker sandbox later |
| 6 | `letta-ai/letta-code` | **Tiered memory**: working / recall / archival | Keeps 8–10h context bounded | **P1/P2** | Start as local context policy; no memory server |
| 7 | `BerriAI/litellm` | **Router metadata**: fallback, budget headers, usage | We already have 9Router/OpenCode; borrow metadata model | **P2** | Do not add a second gateway by default |
| 8 | `RooCodeInc/Roo-Code` | **Custom modes**: role-specific tools and boundaries | Hard policy profiles for planner/coder/reviewer | **P2** | Encode in our policy config, not IDE fork |
| 9 | `microsoft/autogen` (archived) | **Group-chat speaker selection**: pick next role by evidence | Could improve meeting/wave routing | **P2** | Concept only; do not import archived framework |
| 10 | `Significant-Gravitas/AutoGPT` / `agbenchmark` | **Benchmark harness**: fixed tasks + success/cost/time metrics | Prevents “better” releases without evidence | **P2** | Use SWE-bench/own tasks; do not adopt AutoGPT runtime |

## Next 5 — watchlist only

| # | Repository | Pattern | Why defer |
|---|---|---|---|
| 11 | `tree-sitter/tree-sitter` | Parser for multi-language repo maps | Native dependency; start with Python `ast` |
| 12 | `SWE-bench/SWE-bench` | Real issue-resolution benchmark | Heavy setup; after live pilot |
| 13 | `sweepai/sweep` | Issue -> PR workflow | Product overlap; study handoff UX |
| 14 | `AgentOps-AI/agentops` / `langfuse/langfuse` | Tracing/eval observability | Local ledger may be enough |
| 15 | `geekan/MetaGPT` / `OpenBMB/ChatDev` | Company-style role decomposition | Conceptual overlap with planning council |

---

## Implementation order (features, not repos)

### P0 — next two features

1. **Repo Map** (`aider` mechanism)
   - Python `ast` first; tree-sitter only if multi-language is required.
   - Input: task + repo tree.
   - Output: ranked symbols/files within a token cap.
   - Test: target-file recall on known tasks; no secrets.

2. **ACI / action-observation contract** (`SWE-agent` mechanism)
   - Define allowed actions: read, search, open, patch, verify.
   - Every action returns a compact structured observation.
   - No raw shell; all commands pass `system_bot`.
   - Test: unsafe command is rejected; malformed observation fails closed.

### P1 — memory and replay

3. **Failure lessons memory** (`mem0` mechanism)
   - Store: `failure_signature -> cause -> fix -> files/tests`.
   - Retrieve by file/symbol/task similarity.
   - Seed from existing `ledger.jsonl` and `memory.py`.

4. **Tiered context** (`letta` mechanism)
   - Working: current phase.
   - Recall: recent failures/decisions.
   - Archival: constitution/spec/plan/lessons.
   - Inject only on demand with provenance.

5. **Event schema extension** (`OpenHands` mechanism)
   - Add action/observation/status fields to existing ledger.
   - Keep replay compatibility; do not replace the ledger.

### P2 — optimization and scale

6. **DSPy-style offline optimizer** — only after a frozen benchmark.
7. **Gateway metadata** (`LiteLLM` ideas) — fallback/cost fields, not a new proxy.
8. **Role modes** (`Roo-Code` ideas) — policy presets, not IDE extension.
9. **Speaker selection** (`AutoGen` concept) — only if meeting routing becomes a bottleneck.
10. **Benchmark harness** (`SWE-bench`/`agbenchmark`) — eval after pilot.

## Do not take

- Spec Kit Git extension — conflicts with worktree/checkpoint/handoff.
- Prose `tasks.md` parser — use canonical `tasks.yaml`.
- Spec Kit free-form execution model — keep `safe_writer`, budget, verify.
- DSPy/Mem0/Letta/LiteLLM/AutoGen as runtime dependencies now.
- AutoGPT as an eval harness — use `agbenchmark`/SWE-bench/own tests.

## Guardrails for every adoption

- No new dependency without a measured benefit and a rollback plan.
- Repo maps and memory must exclude secrets and respect worktree boundaries.
- Memory is **advice**, not authority; `verify_command` remains the gate.
- Prompt optimization must be offline, versioned, and approved before use.
- Gateway fallback must be recorded in the ledger with actual model and usage.
- Benchmarks must run with mocks/free models in CI.

## Mapping to current modules

```text
aider repo map        -> planner context / future v2/repo_map.py
SWE-agent ACI         -> runtime.py action contract + system_bot allowlist
mem0 failure memory   -> v2 memory layer / existing memory.py hints
letta tiered memory   -> state/context manager
OpenHands events      -> ledger event schema + replay
litellm ideas         -> models.py gateway metadata (not transport)
Roo role modes        -> presets/policy profiles
autogen speaker       -> meeting policy
AutoGPT/agbenchmark   -> tests/eval harness
```

## Decision

Study **aider repo map** and **SWE-agent ACI** first.

DSPy stays **P1 research / P2 runtime** until there is a benchmark baseline and
budget for offline optimization. Do not add any of these frameworks to the
runtime in this phase.
