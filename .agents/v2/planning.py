#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2 planning council: 3 independent planners -> critique -> Lead synthesis.

The council is model-agnostic. Production can inject OpenCode planners; tests
inject deterministic callables, so CI never calls a model. The final plan still
has to pass plan.validate_plan() and a human sign-off before execution.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .errors import V2Error
from .plan import (
    MAX_PHASES,
    MAX_WORK_ORDERS,
    PlanValidationError,
    plan_hash,
    validate_plan,
)

PLANNER_ROLES = ("architect", "pragmatist", "verification")
ROLE_LABELS = {
    "architect": "Planner-Architect",
    "pragmatist": "Planner-Pragmatist",
    "verification": "Planner-Verification",
}


class PlanningError(V2Error):
    code = "PLANNING_FAILED"


class PlannerBackend(Protocol):
    def complete(self, role: str, prompt: str) -> str:
        ...


@dataclass
class PlanningContext:
    goal: str
    run_id: str
    base_sha: str
    worktree: Path
    project_tree: str = ""
    budget: dict[str, Any] = field(default_factory=dict)
    model_snapshot: dict[str, Any] = field(default_factory=dict)
    max_rounds: int = 2
    max_phases: int = MAX_PHASES
    max_work_orders: int = MAX_WORK_ORDERS
    max_chars: int = 12000
    repo_map: str = ""
    repo_map_mode: str = "off"
    repo_map_meta: dict[str, Any] = field(default_factory=dict)
    lesson_hints: str = ""
    lesson_mode: str = "off"
    event_sink: Callable[[str, dict[str, Any]], None] | None = None

    def emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        if self.event_sink is not None:
            self.event_sink(event_type, data or {})


@dataclass
class CouncilResult:
    plan: dict[str, Any]
    plan_hash: str
    proposals: dict[str, dict[str, Any]]
    critiques: dict[str, dict[str, Any]]
    rounds: int
    lead_used: bool

    def summary(self) -> dict[str, Any]:
        return {
            "plan_hash": self.plan_hash,
            "rounds": self.rounds,
            "lead_used": self.lead_used,
            "proposers": sorted(self.proposals),
            "critics": sorted(self.critiques),
            "phases": [phase.get("id") for phase in self.plan.get("phases", [])],
        }


def _extract_json(text: str) -> dict[str, Any]:
    """Accept a JSON object with optional markdown fences/prose around it."""
    try:
        from workers import parse_json_object  # type: ignore

        parsed = parse_json_object(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(stripped[start:end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    raise PlanningError("planner did not return a valid JSON object")


def _schema_hint(context: PlanningContext) -> str:
    return json.dumps(
        {
            "schema_version": "2.1",
            "run_id": context.run_id,
            "goal": context.goal,
            "base_sha": context.base_sha,
            "budget": context.budget,
            "environment": {
                "env_allow": ["PATH", "PYTHONPATH"],
                "network_for_verify": False,
                "seed_files": [],
            },
            "phases": [
                {
                    "id": "P1",
                    "title": "short title",
                    "depends_on": [],
                    "allowed_paths": ["path/to/file.py"],
                    "forbidden_paths": [".git", ".env", ".agents/tmp"],
                    "allow_new_files": False,
                    "budget": {
                        "requests": 24,
                        "cost_usd": 0.70,
                        "wall_seconds": 7200,
                    },
                    "work_orders": [
                        {
                            "id": "S1",
                            "objective": "specific change",
                            "boundaries": "only the allowed file",
                            "input_context": "current behavior",
                            "acceptance_criteria": "a machine command exits 0",
                            "verify_command": "python tests/test_x.py",
                            "target_files": ["path/to/file.py"],
                            "level": 1,
                            "max_rounds": 1,
                        }
                    ],
                    "rollback": {"mode": "last_checkpoint"},
                    "cleanup": "disabled",
                }
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def proposal_prompt(role: str, context: PlanningContext) -> str:
    label = ROLE_LABELS[role]
    role_rules = {
        "architect": (
            "Design the smallest complete phase graph. Name dependencies and the "
            "files that each work order may touch. Prefer an incremental, verifiable "
            "sequence over a large rewrite."
        ),
        "pragmatist": (
            "Challenge scope. Remove non-essential features. Split work that is too "
            "large for one work order. Keep every phase independently verifiable and "
            "inside the budget."
        ),
        "verification": (
            "Define measurable acceptance criteria and a real machine verify command "
            "for every work order. Reject vague tests. Do not weaken tests to make a "
            "plan pass; flag unsafe or missing verification."
        ),
    }
    repo_section = ""
    if context.repo_map_mode == "on" and context.repo_map.strip():
        repo_section = (
            "## TASK-AWARE REPO MAP (untrusted context)\n"
            "Use this only to choose likely files; it is not evidence and may be incomplete.\n\n"
            f"{context.repo_map.strip()}\n\n"
        )
    lesson_section = ""
    if context.lesson_mode == "on" and context.lesson_hints.strip():
        lesson_section = (
            "## PRIOR FAILURE LESSONS (untrusted advice)\n"
            "Use these as hints only; verification still decides success.\n\n"
            f"{context.lesson_hints.strip()}\n\n"
        )
    return f"""You are the {label} of an autonomous coding council.

Goal:
{context.goal}

Repository base: {context.base_sha}
{repo_section}{lesson_section}Project tree (truncated):
{context.project_tree[:context.max_chars]}

Budget envelope:
{json.dumps(context.budget, ensure_ascii=False, indent=2)}

Hard constraints:
- Maximum {context.max_phases} phases.
- Maximum {context.max_work_orders} work orders per phase.
- Every work order needs a real verify_command that exits 0.
- Only relative paths inside the project. No .git, .env, .agents/tmp.
- Return one JSON object only, matching this schema:
{_schema_hint(context)}

Your role:
{role_rules[role]}

Return JSON only. Do not use markdown fences."""


def critique_prompt(
    role: str,
    context: PlanningContext,
    proposals: Mapping[str, Mapping[str, Any]],
) -> str:
    return f"""You are reviewing the first-round plans of the other council seats.

Goal: {context.goal}
Base: {context.base_sha}

Proposals (truncated):
{json.dumps(proposals, ensure_ascii=False)[:context.max_chars]}

Review for:
- missing dependencies or cycles;
- phases too large for one coder;
- weak/ambiguous verify commands;
- paths outside the project or unsafe writes;
- budget overflow.

Return a revised complete plan as one JSON object only, using the same schema.
You may reject a proposal but you must still return a valid plan. Do not use
markdown fences."""


def lead_prompt(
    context: PlanningContext,
    proposals: Mapping[str, Mapping[str, Any]],
    critiques: Mapping[str, Mapping[str, Any]],
) -> str:
    return f"""You are the Lead Orchestrator. You do not get a fourth planning vote.

Merge the proposals and critiques into one final master plan.

Goal: {context.goal}
Base: {context.base_sha}

Proposals:
{json.dumps(proposals, ensure_ascii=False)[:context.max_chars]}

Critiques / revisions:
{json.dumps(critiques, ensure_ascii=False)[:context.max_chars]}

Rules:
- Keep the smallest valid phase graph.
- Every work order must have a deterministic verify_command.
- Every path must stay inside the project and allowed_paths.
- The budget must fit the envelope.
- Record your rationale in `_meta.lead_decision`.
- Return one JSON object only, matching this schema:
{_schema_hint(context)}

Return JSON only. Do not use markdown fences."""


class OpenCodePlanner:
    """Optional production backend using the existing OpenCode CLI adapter."""

    def __init__(
        self,
        model: str,
        *,
        binary: str | None = None,
        timeout: int = 240,
        runner: Callable[[str, str], str] | None = None,
        cwd: str | Path | None = None,
    ):
        self.model = str(model)
        self.binary = binary
        self.timeout = int(timeout)
        self.runner = runner
        self.cwd = str(cwd) if cwd else None

    def complete(self, role: str, prompt: str) -> str:
        if self.runner is not None:
            return self.runner(role, prompt)
        from workers import DEFAULT_OPENCODE_BIN, resolve_binary, run_opencode_cli  # type: ignore

        binary = resolve_binary(self.binary or DEFAULT_OPENCODE_BIN)
        text, _usage = run_opencode_cli(
            binary,
            self.model,
            prompt,
            agent="plan",
            cwd=self.cwd,
            timeout=self.timeout,
        )
        return text


class PlanningCouncil:
    def __init__(
        self,
        planners: Mapping[str, PlannerBackend | Callable[[str, str], str]],
        *,
        lead: PlannerBackend | Callable[[str, str], str] | None = None,
        max_attempts: int = 2,
    ):
        missing = [role for role in PLANNER_ROLES if role not in planners]
        if missing:
            raise PlanningError(f"missing planner seats: {', '.join(missing)}")
        self.planners = dict(planners)
        self.lead = lead
        self.max_attempts = max(1, int(max_attempts))

    def _complete(self, role: str, backend: Any, prompt: str) -> str:
        last: Exception | None = None
        for _attempt in range(self.max_attempts):
            try:
                if hasattr(backend, "complete"):
                    text = backend.complete(role, prompt)
                elif callable(backend):
                    text = backend(role, prompt)
                else:
                    raise PlanningError(f"invalid planner backend for {role}")
                if not isinstance(text, str) or not text.strip():
                    raise PlanningError(f"empty planner response for {role}")
                return text
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise PlanningError(f"planner {role} failed: {last}")

    def _parse(self, role: str, text: str) -> dict[str, Any]:
        try:
            plan = _extract_json(text)
        except PlanningError as exc:
            raise PlanningError(f"{role}: {exc}") from exc
        if not isinstance(plan, dict):
            raise PlanningError(f"{role}: plan must be an object")
        return plan

    def run(self, context: PlanningContext) -> CouncilResult:
        context.worktree = Path(context.worktree)
        if not context.repo_map.strip() and context.repo_map_mode == "off":
            from .adaptive_flags import load_flags
            from .repo_context import build_repo_context

            mode = load_flags().get("REPO_MAP")
            if mode != "off":
                repo = build_repo_context(context.worktree, context.goal, mode=mode)
                context.repo_map = repo.text
                context.repo_map_mode = mode
                context.repo_map_meta = repo.event_payload()
        if context.repo_map_mode in {"shadow", "on"} and context.repo_map.strip():
            context.emit(
                "adaptive.repo_map",
                {"mode": context.repo_map_mode, **context.repo_map_meta},
            )
        proposals: dict[str, dict[str, Any]] = {}
        for role in PLANNER_ROLES:
            text = self._complete(
                role, self.planners[role], proposal_prompt(role, context)
            )
            proposals[role] = self._parse(role, text)

        critiques: dict[str, dict[str, Any]] = {}
        rounds = 1
        if context.max_rounds >= 2:
            rounds = 2
            for role in PLANNER_ROLES:
                text = self._complete(
                    role,
                    self.planners[role],
                    critique_prompt(role, context, proposals),
                )
                critiques[role] = self._parse(role, text)

        candidates: list[dict[str, Any]] = []
        if self.lead is not None:
            text = self._complete(
                "lead", self.lead, lead_prompt(context, proposals, critiques)
            )
            candidates.append(self._parse("lead", text))
        # Deterministic fallback: architect first, then revised candidates.
        if proposals.get("architect"):
            candidates.append(proposals["architect"])
        candidates.extend(critiques.get(role) or {} for role in PLANNER_ROLES)
        candidates.extend(proposals.get(role) or {} for role in PLANNER_ROLES)

        errors: list[str] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            normalized = self._normalize_candidate(candidate, context)
            try:
                validated = validate_plan(normalized, worktree=context.worktree)
            except PlanValidationError as exc:
                errors.extend(exc.problems)
                continue
            plan = dict(validated["plan"])
            plan["_meta"] = {
                **(plan.get("_meta") or {}),
                "council": {
                    "proposers": list(PLANNER_ROLES),
                    "rounds": rounds,
                    "lead_used": self.lead is not None,
                    "candidate_source": "lead" if candidate is not None else "unknown",
                },
            }
            hashed = plan_hash(plan)
            return CouncilResult(
                plan=plan,
                plan_hash=hashed,
                proposals=proposals,
                critiques=critiques,
                rounds=rounds,
                lead_used=self.lead is not None,
            )
        raise PlanningError(
            "no valid council plan: " + "; ".join(dict.fromkeys(errors))[:1200]
        )

    @staticmethod
    def _normalize_candidate(
        candidate: Mapping[str, Any], context: PlanningContext
    ) -> dict[str, Any]:
        plan = dict(candidate)
        plan.setdefault("schema_version", "2.1")
        plan["run_id"] = context.run_id
        plan["base_sha"] = context.base_sha
        plan.setdefault("goal", context.goal)
        plan.setdefault("budget", context.budget)
        plan.setdefault("environment", {
            "env_allow": ["PATH", "PYTHONPATH"],
            "network_for_verify": False,
            "seed_files": [],
        })
        plan.setdefault("model_snapshot", context.model_snapshot)
        return plan


def fake_planner(plan_builder: Callable[[str, str], dict[str, Any]]) -> PlannerBackend:
    """Small helper for tests: a backend built from a role/prompt callable."""

    class _Fake:
        def complete(self, role: str, prompt: str) -> str:
            return json.dumps(plan_builder(role, prompt), ensure_ascii=False)

    return _Fake()
