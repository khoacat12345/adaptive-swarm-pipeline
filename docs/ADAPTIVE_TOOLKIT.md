# Adaptive Toolkit — P0–P2 Lightweight Primitives

> Status: implemented as dependency-free Python primitives in `.agents/v2/adaptive.py`.
> These are **mechanism extractions**, not imports of the upstream frameworks.
> Last updated: 2026-11-04

| # | Source idea | Implemented primitive | What it does | Safety boundary |
|---|---|---|---|---|
| 1 | aider repo map | `RepoMapper` / `RepoMap` | Python AST symbol extraction, task relevance ranking, token-capped map | Names/paths only; no file contents, no secrets |
| 2 | SWE-agent ACI | `ActionPolicy` / `Action` / `Observation` | Allowlisted list/read/search/verify actions with compact observations | No arbitrary shell; path-rooted; verify via SystemBot |
| 3 | mem0 | `LessonStore` / `Lesson` | JSONL failure lessons, retrieval by file/term, compact hints | Local only; advice cannot override verify |
| 4 | Letta | `ContextPacker` | working / recall / archival tiers with token budget | Provenance labels; no unbounded history |
| 5 | OpenHands | `adaptive_event` | action/observation/status event shape | Append-only ledger compatibility; no replacement |
| 6 | DSPy | `PromptOptimizer` | Offline proposals from failure lessons | No automatic prompt mutation; benchmark required |
| 7 | LiteLLM | `GatewayModel` / `select_gateway_model` | Healthy/free/family-aware gateway selection | Decision metadata only; no second proxy |
| 8 | Roo-Code | `MODE_PRESETS` / `apply_mode` | free-only / strict-security / nightly policy packs | Policy only; no IDE fork |
| 9 | AutoGen | `select_speaker` | Next role based on blocker/reject evidence | Concept only; no archived framework import |
| 10 | SWE-bench / agbenchmark | `run_benchmark` | Guarded command benchmark with exit/time/output | Commands pass SystemBot; no arbitrary shell |

## What is deliberately not included

- No LangGraph/DSPy/Mem0/Letta/LiteLLM/AutoGen runtime dependency.
- No direct file writers outside `safe_writer`.
- No automatic prompt updates.
- No auto-merge.
- No claim that these primitives are production-proven; the live pilot remains open.

## Integration order

```text
1. RepoMapper -> planner context
2. ActionPolicy -> runtime/ACI observations
3. LessonStore -> existing memory.py hints
4. ContextPacker -> long-run phase context
5. adaptive_event -> ledger extensions
6. PromptOptimizer -> offline lab only
7. GatewayModel -> model registry metadata
8. MODE_PRESETS -> CLI/config presets
9. select_speaker -> meeting policy
10. run_benchmark -> CI/eval harness
```

## Acceptance

```text
V2 ADAPTIVE: 29/29 PASS
```

Tests cover repo-map relevance/token cap, ACI path guards and blocked actions,
lesson persistence/retrieval, context budgets, event schema, offline proposal,
gateway selection, mode presets, speaker selection and guarded benchmarks.
