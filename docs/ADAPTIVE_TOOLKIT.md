# Adaptive Toolkit — P0–P2 Lightweight Primitives

> Status: implemented as dependency-free Python primitives in `.agents/v2/adaptive.py`
> and wired into v2 E1–E4 through `.agents/v2/capability_runtime.py`.
> Five core capabilities (repo map, ACI, failure lessons, bounded memory and
> events) are complete for the v2.1 release-candidate scope. These are
> **mechanism extractions**, not imports of the upstream frameworks.
> Last updated: 2026-11-07

| # | Source idea | Implemented primitive | What it does | Safety boundary |
|---|---|---|---|---|
| 1 | aider repo map | `RepoMapper` / `RepoMap` | Python AST symbols, import/reference graph, personalized PageRank, token-capped map | Names/paths only; no file contents/secrets; optional tokenizer |
| 2 | SWE-agent ACI | `ActionPolicy` / `Action` / `Observation` | Allowlisted list/read/open/scroll/search/edit_check/verify; line-window views and unique-edit validation | No write through ACI; no arbitrary shell; path-rooted; SystemBot verify |
| 3 | mem0 | `LessonStore` / `Lesson` | JSONL failure lessons, signature upsert/dedupe, retrieval by file/term, compact hints | Local only; advice cannot override verify |
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
- No automatic merge during execution; delivery requires explicit `v2 merge`.
- No claim that these primitives are adversarially proven; the live pilot and
  uniform E1–E4 tests passed, and Git worktree is still not an OS sandbox.

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

## Release integration (v2.1.0 RC)

The five release-critical capabilities are no longer reference-only:

| Capability | Source pattern | v2 integration | Evidence |
|---|---|---|---|
| Repo map | aider | planner/coder context | `test_v2_planner_repo_map.py` |
| Read-only ACI | SWE-agent | coder context + pre-apply validation | `test_v2_aci_integration.py` |
| Failure lessons | mem0 | plan/execution hints | `test_v2_lessons_integration.py` |
| Bounded memory | Letta | phase context budget | `test_capabilities_uniform.py` |
| Event runtime | OpenHands | append-only event stream | `test_v2_event_state.py` |

The bundle is injected uniformly into E1–E4 by `capability_runtime.py`. No
DSPy/Mem0/Letta/LiteLLM/AutoGen runtime dependency was added.

## Acceptance

```text
V2 ADAPTIVE: 42/42 PASS
V2 CAPABILITIES UNIFORM: 23/23 PASS
```

Tests cover PageRank relevance/token cap, windowed ACI and edit validation,
lesson upsert/persistence/retrieval, context budgets, event schema, offline
proposal, gateway selection, mode presets, speaker selection and guarded
benchmarks.
