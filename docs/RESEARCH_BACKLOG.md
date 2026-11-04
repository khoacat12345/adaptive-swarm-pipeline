# Research Backlog — Adaptive Patterns Worth Borrowing

> Purpose: a coder/reviewer shortlist of open-source mechanisms that fit the
> swarm control plane.
> Rule: **extract ideas, not frameworks**. No dependency is added without a
> separate approval and a safety review.
> Last updated: 2026-11-04

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

## Top 10 — primary shortlist

| # | Repository | Pattern to study | Fit | Priority | What to keep | What not to copy |
|---|---|---|---|---|---|---|
| 1 | `paul-gauthier/aider` | Tree-sitter repo map / symbol graph | Planner and coder context selection | **P0 next** | Rank symbols by relevance to the task; send a compact map; cap tokens | Do not paste the whole repo; do not assume map equals correctness |
| 2 | `princeton-nlp/SWE-agent` | Agent-Computer Interface (ACI) | Command/action safety | **P0/P1** | Narrow, typed actions; explicit observation format; no free shell | Do not let the agent invent commands outside the allowlist |
| 3 | `stanfordnlp/dspy` | Metric-driven prompt optimization | Offline prompt improvement | **P2 research** | Use verify pass/fail as a metric; optimize offline; compare against baseline | Not automatic "self-evolution"; needs datasets, training cost and approval |
| 4 | `mem0ai/mem0` | Failure-learned memory | Coder hints / reviewer memory | **P1** | Store failure signature -> lesson -> relevant files/tests; inject only high-confidence tips | Do not store secrets or raw code; do not let memory override verify |
| 5 | `All-Hands-AI/OpenHands` | Event-stream action/observation ledger | Replay and audit | **P1/P2** | Action -> observation -> event; stable event IDs; replayable state | Our ledger already covers much of this; do not replace it |
| 6 | `letta-ai/letta` | Tiered memory (working/recall/archival) | Long-run context management | **P1/P2** | Separate hot context from project archive; explicit recall policy | Do not add a memory server for a local one-user harness |
| 7 | `BerriAI/litellm` | Gateway, budget and fallback metadata | Provider gateway | **P2** | Normalize model IDs, usage and fallback events; budget headers | 9Router/OpenCode already cover transport; avoid duplicate gateway |
| 8 | `Roo-Code/Roo-Code` | Persona/role boundaries | Role policy and permissions | **P2** | Hard capability matrix per role (read-only reviewer, writer=Lead) | We already enforce this in the execution engine; do not fork the IDE |
| 9 | `microsoft/autogen` | Dynamic speaker selection | Meeting/wave routing | **P2** | Choose the next role based on evidence, not a fixed turn order | Do not import the framework; our meeting state machine is enough |
| 10 | `Significant-Gravitas/AutoGPT` | Benchmark / eval harness | Regression confidence | **P2** | Fixed tasks, success/cost/time metrics, baseline comparison | Do not adopt its agent runtime |

## Next 5 — watchlist only

| # | Repository | Pattern | Why defer |
|---|---|---|---|
| 11 | `tree-sitter/tree-sitter` | Actual parser used by repo-map implementations | Adds a native dependency; use Python `ast` for Python-only first |
| 12 | `SWE-bench/SWE-bench` | Real issue-resolution benchmark | Heavy setup; useful after live pilot |
| 13 | `sweepai/sweep` | Issue -> PR workflow | Product overlap; study handoff UX |
| 14 | `AgentOps-AI/agentops` / `langfuse/langfuse` | Tracing and eval observability | Our ledger is local; add only if multi-run debugging demands it |
| 15 | `geekan/MetaGPT` / `OpenBMB/ChatDev` | Company-style role decomposition | Conceptual overlap with planning council; no runtime import |

---

## Recommended order after Spec Kit P0

1. **Aider repo map** — biggest immediate token/context win.
2. **SWE-agent ACI** — strengthen the action boundary and observation contract.
3. **Mem0-style failure memory** — local JSONL first, no vector service required.
4. **Letta-style tiered memory** — only after failure memory proves useful.
5. **DSPy-style offline experiments** — after there is a benchmark set and a
   frozen baseline; never mutate production prompts automatically.
6. **LiteLLM/AutoGen/AutoGPT ideas** — only when provider or evaluation scale
   demands it.

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
AutoGPT eval          -> tests/eval harness
```

## Decision

Study **aider ACI/repo map** and **SWE-agent ACI** first.
Do **not** add DSPy, Mem0, Letta, LiteLLM or AutoGen as runtime dependencies in
this phase.
