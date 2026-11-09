#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn a goal, a v1 plan or a tasks file into a valid v2 MasterPlan.

The adapter is deterministic on purpose: it never calls a model. A caller that
wants LLM planning can generate a v1-shaped plan first (``planner.py``) and
then adapt it here. The result is always validated by ``v2.plan`` before it is
returned, so an invalid proposal cannot reach a worktree.

    python .agents/v2/planner_adapter.py --goal "fix src.py" --verify "python check.py"
    python .agents/v2/planner_adapter.py --plan v1_plan.json --out v2_plan.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tier_policy  # noqa: E402

from v2.errors import V2Error  # noqa: E402
from v2.execution import is_sensitive_path, normalize_tier  # noqa: E402
from v2.plan import (  # noqa: E402
    DEFAULT_FORBIDDEN,
    MAX_PHASES,
    PlanValidationError,
    SCHEMA_VERSION,
    plan_hash,
    validate_plan,
)

TARGET_TOKEN_RE = re.compile(
    r"(?<![\w./\\-])("
    r"(?:[\w.-]+[/\\])*[\w.-]+"
    r"\.(?:py|pyi|js|jsx|ts|tsx|mjs|cjs|md|json|ya?ml|toml|ini|cfg|txt|"
    r"html|css|scss|sh|bash|ps1|go|rs|java|kt|kts|c|h|cc|cpp|hpp|cs|rb|php|sql)"
    r")(?![\w/\\-])",
    re.IGNORECASE,
)
TEST_TARGET_RE = re.compile(
    r"(^test_.*\.py$|.*_test\.py$|^conftest\.py$)", re.IGNORECASE
)


class PlanningError(V2Error):
    code = "PLANNING_ERROR"


COUNCIL_ROLES = ("architect", "pragmatist", "verification")
COUNCIL_LEVELS = {"P1": 1, "P2": 2, "P3": 3}


@dataclass(frozen=True)
class CouncilSeat:
    """One planner seat's proposal and review verdict."""

    role: str
    level: str
    proposal_hash: str
    valid: bool
    verdict: str
    score: int = 0
    notes: tuple[str, ...] = ()
    model: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "level": self.level,
            "proposal_hash": self.proposal_hash,
            "valid": bool(self.valid),
            "verdict": self.verdict,
            "score": int(self.score),
            "notes": list(self.notes),
            "model": self.model,
        }


@dataclass(frozen=True)
class CouncilOutcome:
    """Result of executing the P1/P2/P3 planning council."""

    level: str
    required_planners: int
    rounds: int
    seats: tuple[CouncilSeat, ...]
    selected_role: str
    selected_plan_hash: str
    consensus: bool
    notes: tuple[str, ...] = ()

    @property
    def valid_seats(self) -> int:
        return sum(1 for seat in self.seats if seat.valid)

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "required_planners": self.required_planners,
            "rounds": self.rounds,
            "seats": [seat.as_dict() for seat in self.seats],
            "valid_seats": self.valid_seats,
            "selected_role": self.selected_role,
            "selected_plan_hash": self.selected_plan_hash,
            "consensus": bool(self.consensus),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class PlanAdapterResult:
    plan: dict[str, Any]
    plan_hash: str
    tier: str
    planner: str
    source: str
    source_hash: str
    summary: str
    council: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "plan_hash": self.plan_hash,
            "tier": self.tier,
            "planner": self.planner,
            "source": self.source,
            "source_hash": self.source_hash,
            "summary": self.summary,
            "council": self.council,
        }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _sha256_json(payload: Any) -> str:
    return _sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _normalize_relative(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip("/")


def _is_test_target(relative: str) -> bool:
    path = _normalize_relative(relative).lower()
    name = path.rsplit("/", 1)[-1]
    return "/tests/" in f"/{path}" or bool(TEST_TARGET_RE.match(name))


def _require_targets(targets: list[str], *, root: Path, allow_new_files: bool) -> list[str]:
    cleaned: list[str] = []
    for value in targets:
        relative = _normalize_relative(value)
        if not relative or relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
            raise PlanningError(f"unsafe target path: {value!r}")
        if ".." in Path(relative).parts:
            raise PlanningError(f"unsafe target path: {value!r}")
        if _is_test_target(relative):
            raise PlanningError(f"v2 cannot target a test file: {relative}")
        if not allow_new_files and not (root / relative).is_file():
            raise PlanningError(f"target does not exist: {relative}")
        if relative not in cleaned:
            cleaned.append(relative)
    if not cleaned:
        raise PlanningError("plan has no target files")
    return cleaned


def detect_targets(
    goal: str, root: str | Path = ".", *, allow_new_files: bool = False
) -> list[str]:
    """Extract path-like tokens from a natural-language goal.

    By default only existing files are returned. When ``allow_new_files`` is
    requested, tokens that do not exist yet are also accepted so a goal can
    create a new module without repeating it via ``--target``.
    """
    base = Path(root).expanduser().resolve()
    found: list[str] = []
    for match in TARGET_TOKEN_RE.finditer(str(goal or "")):
        relative = _normalize_relative(match.group(1))
        if not relative or relative in found:
            continue
        if (base / relative).is_file() or allow_new_files:
            found.append(relative)
    return found


def _extra_allowed_paths(
    values: Any,
    *,
    root: Path,
    allow_new_files: bool,
) -> list[str]:
    """Validate extra scopes a v1 plan declared as writable.

    Targets stay the only files the coder may submit; these scopes only keep
    plan validation from rejecting a declared helper (for example an existing
    check script). Test files are never widened.
    """
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for value in values:
        relative = _normalize_relative(value)
        if not relative or relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
            raise PlanningError(f"unsafe allowed path: {value!r}")
        if ".." in Path(relative).parts:
            raise PlanningError(f"unsafe allowed path: {value!r}")
        if _is_test_target(relative):
            # Tests are frozen by the execution engine; declaring them as
            # writable is a configuration error, not a licence to edit them.
            continue
        if not allow_new_files and not (root / relative).is_file() and not (
            root / relative
        ).is_dir():
            raise PlanningError(f"allowed path does not exist: {relative}")
        if relative not in cleaned:
            cleaned.append(relative)
    return cleaned


def default_verify_command(targets: list[str]) -> str:
    """A conservative, runnable verify for a target set."""
    if targets and all(str(item).lower().endswith((".py", ".pyi")) for item in targets):
        joined = " ".join(f'"{item}"' for item in targets)
        return f"python -m py_compile {joined}"
    first = _normalize_relative(targets[0]) if targets else "README.md"
    if first.lower().endswith((".md", ".txt")):
        return (
            "python -c \"from pathlib import Path; "
            f"assert Path('{first}').is_file()\""
        )
    return (
        "python -c \"from pathlib import Path; "
        f"assert Path('{first}').is_file()\""
    )


def _planner_for_tier(tier: str) -> str:
    spec = tier_policy.TIERS.get(str(tier), tier_policy.TIERS["E2"])
    return spec.planner_level


def _propose_tier(
    phases: list[Mapping[str, Any]],
    targets: list[str],
    *,
    explicit: str | None,
    low_risk_opt_in: bool,
) -> str:
    if explicit:
        tier = normalize_tier(explicit)
    else:
        sensitive = any(is_sensitive_path(item) for item in targets)
        multi_phase = len(phases) > 1
        risky = any(
            int(work_order.get("level", 1) or 1) >= 2
            for phase in phases
            for work_order in phase.get("work_orders") or []
        )
        tier = "E3" if sensitive or multi_phase or risky else "E2"
    if tier == "E1":
        if not low_risk_opt_in:
            raise PlanningError(
                "E1 is low-risk opt-in: pass low_risk_opt_in=True explicitly"
            )
        blocked = [item for item in targets if is_sensitive_path(item)]
        if blocked:
            raise PlanningError(
                "E1 cannot target sensitive paths: " + ", ".join(blocked)
            )
    return tier


def _council_level(result: PlanAdapterResult) -> str:
    level = str(result.planner or "").upper()
    if level not in COUNCIL_LEVELS:
        level = _planner_for_tier(result.tier)
    return level


def _clone_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(plan, ensure_ascii=False))


def _review_proposal(
    role: str,
    plan: Mapping[str, Any],
    *,
    level: str,
    root: Path,
) -> tuple[bool, int, tuple[str, ...]]:
    """Validate one council proposal with role-specific checks.

    ``validate_plan`` stays authoritative; the role only adds a second opinion
    about scope, budget and verification quality. A proposal that fails the
    schema is never selected.
    """
    try:
        validate_plan(plan, worktree=root)
    except PlanValidationError as exc:
        return False, 0, tuple(exc.problems)
    notes: list[str] = []
    score = 100
    if role == "architect":
        phases = plan.get("phases") or []
        if not phases:
            return False, 0, ("architect: plan has no phases",)
        if any(not (phase.get("work_orders") or []) for phase in phases):
            return False, 0, ("architect: a phase has no work order",)
        notes.append(f"architect: {len(phases)} phase(s) with dependencies")
    elif role == "pragmatist":
        phases = plan.get("phases") or []
        budget = plan.get("budget") or {}
        envelope = int((budget.get("global") or budget).get("requests", 0) or 0)
        requested = sum(
            int((phase.get("budget") or {}).get("requests", 0) or 0)
            for phase in phases
        )
        if envelope and requested > envelope:
            notes.append("pragmatist: phase requests exceed the plan envelope")
            score -= 40
        if len(phases) > 3:
            notes.append("pragmatist: more than three phases")
            score -= 10
        notes.append(f"pragmatist: {requested}/{envelope} requests allocated")
    elif role == "verification":
        missing = [
            f"{phase.get('id')}/{work.get('id')}"
            for phase in plan.get("phases") or []
            for work in phase.get("work_orders") or []
            if not str(work.get("verify_command") or "").strip()
        ]
        if missing:
            return False, 0, (
                "verification: missing verify_command: " + ", ".join(missing),
            )
        notes.append(
            "verification: every work order has a real verify_command"
        )
    else:
        notes.append(f"{role}: proposal accepted by schema validation")
    notes.append(f"{role}: {level} council seat")
    return True, max(0, score), tuple(notes)


def _parse_council_payload(text: Any) -> dict[str, Any]:
    if isinstance(text, Mapping):
        return dict(text)
    raw = str(text or "").strip()
    if not raw:
        raise PlanningError("planner returned an empty response")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(raw[start:end + 1])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    raise PlanningError("planner did not return a valid JSON plan object")


def _normalize_council_plan(
    payload: Mapping[str, Any], base: Mapping[str, Any]
) -> dict[str, Any]:
    candidate = dict(payload)
    candidate.setdefault("schema_version", SCHEMA_VERSION)
    candidate["run_id"] = base.get("run_id")
    candidate["base_sha"] = base.get("base_sha")
    candidate.setdefault("goal", base.get("goal") or "Adapted plan")
    candidate.setdefault("budget", _clone_plan(base.get("budget") or {}))
    candidate.setdefault("environment", _clone_plan(base.get("environment") or {}))
    candidate.setdefault("execution_tier", base.get("execution_tier"))
    candidate.setdefault("planner_level", base.get("planner_level"))
    if base.get("low_risk_opt_in") is True:
        candidate.setdefault("low_risk_opt_in", True)
    if not candidate.get("phases"):
        raise PlanningError("council proposal has no phases")
    return candidate


def _council_prompt(
    role: str,
    *,
    base: Mapping[str, Any],
    level: str,
    proposals: Mapping[str, Any] | None = None,
) -> str:
    role_rules = {
        "architect": "Design the smallest complete phase graph with safe paths.",
        "pragmatist": (
            "Challenge scope, split oversized work and keep every phase inside "
            "the budget envelope."
        ),
        "verification": (
            "Require a real machine verify_command for every work order; do not "
            "weaken tests to make a plan pass."
        ),
    }
    suffix = ""
    if proposals:
        suffix = (
            "\nOther proposals (truncated):\n"
            + json.dumps(proposals, ensure_ascii=False)[:6000]
            + "\nReturn a revised complete plan as one JSON object only."
        )
    return (
        f"You are the {role} planner ({level}) of an autonomous coding council.\n"
        f"Goal: {base.get('goal')}\n"
        f"Base: {base.get('base_sha')}\n"
        f"Budget: {json.dumps(base.get('budget') or {}, ensure_ascii=False)}\n"
        f"Your role: {role_rules.get(role, role_rules['architect'])}\n"
        "Return one JSON object with schema_version 2.1, phases, work_orders, "
        "allowed_paths and a verify_command for every work order. "
        "Return JSON only, no markdown fences."
        + suffix
    )


def run_planning_council(
    result: PlanAdapterResult,
    *,
    level: str | None = None,
    root: str | Path = ".",
    planner_models: Sequence[str] = (),
    planner_call: Callable[[str, str | None, str], Any] | None = None,
    rounds: int = 1,
) -> PlanAdapterResult:
    """Execute the P1/P2/P3 planning council for an adapted plan.

    ``P1`` is a single seat, ``P2`` is proposal + critique and ``P3`` is a
    three-seat council. Without an injected model callable the council runs
    deterministically: every seat produces a reviewable proposal derived from
    the validated base plan, and role-specific checks decide the winner. The
    selected plan hash stays identical to the base plan because ``_meta`` is
    excluded from the hash, so existing E1-E4 behavior does not change.

    When ``planner_call`` is provided it is invoked as
    ``planner_call(role, model, prompt)`` and may return a JSON string or an
    object. Every model proposal must still pass ``validate_plan``.
    """
    base = result.plan
    base_root = Path(root).expanduser().resolve()
    chosen = str(level or _council_level(result)).upper()
    if chosen not in COUNCIL_LEVELS:
        raise PlanningError(f"unknown planner level: {chosen!r}")
    required = COUNCIL_LEVELS[chosen]
    roles = COUNCIL_ROLES[:required]
    models = [str(item) for item in planner_models if str(item).strip()]
    seat_rounds = max(1, min(2, int(rounds or 1)))
    seats: list[CouncilSeat] = []
    valid_plans: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    if planner_call is None:
        for role in roles:
            candidate = _clone_plan(base)
            meta = dict(candidate.get("_meta") or {})
            meta["council_proposal"] = role
            candidate["_meta"] = meta
            valid, score, notes = _review_proposal(
                role, candidate, level=chosen, root=base_root,
            )
            seats.append(
                CouncilSeat(
                    role=role,
                    level=chosen,
                    proposal_hash=plan_hash(candidate),
                    valid=valid,
                    verdict="approve" if valid else "reject",
                    score=score,
                    notes=notes,
                    model=models[len(seats)] if len(seats) < len(models) else None,
                )
            )
            if valid:
                valid_plans[role] = candidate
    else:
        proposals: dict[str, Any] = {}
        for index, role in enumerate(roles):
            model = models[index] if index < len(models) else None
            prompt = _council_prompt(role, base=base, level=chosen)
            try:
                payload = _parse_council_payload(planner_call(role, model, prompt))
                candidate = _normalize_council_plan(payload, base)
                proposals[role] = candidate
            except (PlanningError, PlanValidationError) as exc:
                errors.append(f"{role}: {exc}")
                seats.append(
                    CouncilSeat(
                        role=role,
                        level=chosen,
                        proposal_hash="",
                        valid=False,
                        verdict="reject",
                        score=0,
                        notes=(str(exc),),
                        model=model,
                    )
                )
                continue
            valid, score, notes = _review_proposal(
                role, candidate, level=chosen, root=base_root,
            )
            seats.append(
                CouncilSeat(
                    role=role,
                    level=chosen,
                    proposal_hash=plan_hash(candidate),
                    valid=valid,
                    verdict="approve" if valid else "reject",
                    score=score,
                    notes=notes,
                    model=model,
                )
            )
            if valid:
                valid_plans[role] = candidate
        if seat_rounds >= 2 and proposals:
            for index, role in enumerate(roles):
                model = models[index] if index < len(models) else None
                prompt = _council_prompt(
                    role, base=base, level=chosen, proposals=proposals,
                )
                try:
                    revised = _normalize_council_plan(
                        _parse_council_payload(planner_call(role, model, prompt)),
                        base,
                    )
                except (PlanningError, PlanValidationError) as exc:
                    errors.append(f"{role} critique: {exc}")
                    continue
                valid, score, notes = _review_proposal(
                    role, revised, level=chosen, root=base_root,
                )
                if not valid:
                    errors.extend(notes)
                    continue
                valid_plans[role] = revised
                seats.append(
                    CouncilSeat(
                        role=f"{role}-revised",
                        level=chosen,
                        proposal_hash=plan_hash(revised),
                        valid=True,
                        verdict="approve",
                        score=score,
                        notes=notes,
                        model=model,
                    )
                )

    if not valid_plans:
        raise PlanningError(
            "planning council produced no valid plan: "
            + "; ".join(dict.fromkeys(errors))[:800]
        )

    ranked = sorted(
        valid_plans,
        key=lambda role: (
            -next(
                (seat.score for seat in seats if seat.role == role and seat.valid),
                0,
            ),
            COUNCIL_ROLES.index(role.rsplit("-revised", 1)[0]),
        ),
    )
    selected_role = ranked[0]
    selected = valid_plans[selected_role]
    outcome = CouncilOutcome(
        level=chosen,
        required_planners=required,
        rounds=seat_rounds if planner_call is not None else 1,
        seats=tuple(seats),
        selected_role=selected_role,
        selected_plan_hash=plan_hash(selected),
        consensus=all(seat.valid for seat in seats[:required]),
        notes=tuple(
            note for seat in seats for note in seat.notes
        ),
    )
    updated = _clone_plan(selected)
    meta = dict(updated.get("_meta") or {})
    meta["council"] = outcome.as_dict()
    updated["_meta"] = meta
    summary = result.summary
    if required > 1:
        summary += (
            f" | {chosen} council {outcome.valid_seats}/{required} valid"
        )
    return replace(
        result,
        plan=updated,
        plan_hash=plan_hash(updated),
        summary=summary,
        council=outcome.as_dict(),
    )


def _maybe_council(
    result: PlanAdapterResult,
    *,
    council: bool | None,
    root: str | Path,
    planner_models: Sequence[str] = (),
    planner_call: Callable[[str, str | None, str], Any] | None = None,
    council_rounds: int = 1,
) -> PlanAdapterResult:
    if council is False:
        return result
    level = _council_level(result)
    if council is None and COUNCIL_LEVELS.get(level, 1) <= 1:
        return result
    return run_planning_council(
        result,
        level=level,
        root=root,
        planner_models=planner_models,
        planner_call=planner_call,
        rounds=council_rounds,
    )


def _phase_from_subtask(
    subtask: Mapping[str, Any],
    *,
    index: int,
    phase_id: str,
    targets: list[str],
    root: Path,
    allow_new_files: bool,
    extra_allowed: list[str] | None = None,
) -> dict[str, Any]:
    max_rounds = int(subtask.get("max_rounds", 1) or 1)
    max_rounds = max(1, min(4, max_rounds))
    verify = str(subtask.get("verify_command") or "").strip()
    if not verify:
        verify = default_verify_command(targets)
    objective = str(subtask.get("objective") or subtask.get("title") or "").strip()
    if not objective:
        raise PlanningError(f"subtask {subtask.get('id') or index} has no objective")
    acceptance = str(
        subtask.get("acceptance_criteria")
        or f"verify exits 0 and {', '.join(targets)} contains the requested change"
    )
    return {
        "id": phase_id,
        "title": str(subtask.get("title") or f"Phase {index + 1}"),
        "depends_on": [],
        "allowed_paths": sorted(set(targets) | set(extra_allowed or [])),
        "forbidden_paths": list(DEFAULT_FORBIDDEN),
        "budget": {
            "requests": max(4, max_rounds + 4),
            "cost_usd": 0.50,
            "wall_seconds": 1200,
        },
        "work_orders": [
            {
                "id": str(subtask.get("id") or f"W{index + 1}"),
                "objective": objective,
                "boundaries": str(
                    subtask.get("boundaries")
                    or "Only the target files listed in this phase"
                ),
                "input_context": str(subtask.get("input_context") or ""),
                "acceptance_criteria": acceptance,
                "verify_command": verify,
                "target_files": targets,
                "level": int(subtask.get("level", 1) or 1),
                "max_rounds": max_rounds,
            }
        ],
        "rollback": {"mode": "last_checkpoint"},
        "cleanup": "disabled",
        "allow_new_files": bool(allow_new_files),
    }


def _normalize_subtasks(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = source.get("subtasks")
    if raw is None:
        raw = source.get("work_orders")
    if not isinstance(raw, list) or not raw:
        raise PlanningError("source plan has no subtasks/work_orders")
    subtasks: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise PlanningError(f"subtask {index} must be an object")
        subtasks.append(dict(item))
    return subtasks


def from_v1_plan(
    source: Mapping[str, Any],
    *,
    run_id: str,
    base_sha: str,
    root: str | Path = ".",
    tier: str | None = None,
    low_risk_opt_in: bool = False,
    allow_new_files: bool = False,
    verify_command: str | None = None,
    council: bool | None = None,
    planner_models: Sequence[str] = (),
    planner_call: Callable[[str, str | None, str], Any] | None = None,
    council_rounds: int = 1,
) -> PlanAdapterResult:
    """Adapt an approved v1 plan into a validated v2 MasterPlan."""
    base = Path(root).expanduser().resolve()
    if not isinstance(source, Mapping):
        raise PlanningError("source plan must be an object")
    subtasks = _normalize_subtasks(source)
    if len(subtasks) > MAX_PHASES:
        raise PlanningError(
            f"v1 plan has {len(subtasks)} subtasks; v2 allows at most {MAX_PHASES} phases"
        )

    phases: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    subtask_to_phase: dict[str, str] = {}
    all_targets: list[str] = []
    job_allowed = _extra_allowed_paths(
        source.get("allowed_paths"), root=base, allow_new_files=allow_new_files,
    )
    job_verify = str(source.get("verify_command") or "").strip()
    for index, subtask in enumerate(subtasks):
        raw_id = str(subtask.get("id") or f"S{index + 1}")
        phase_id = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_id).strip("-") or f"P{index + 1}"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", phase_id):
            phase_id = f"P{index + 1}"
        candidate = phase_id
        suffix = 2
        while candidate in used_ids:
            candidate = f"{phase_id}-{suffix}"
            suffix += 1
        phase_id = candidate
        used_ids.add(phase_id)
        subtask_to_phase[raw_id] = phase_id

        targets = _require_targets(
            [str(item) for item in (subtask.get("target_files") or [])],
            root=base,
            allow_new_files=allow_new_files,
        )
        for item in targets:
            if item not in all_targets:
                all_targets.append(item)
        declared_scope = subtask.get("allowed_paths")
        if declared_scope is None:
            declared_scope = subtask.get("allowed")
        extra_allowed = list(job_allowed)
        for relative in _extra_allowed_paths(
            declared_scope, root=base, allow_new_files=allow_new_files,
        ):
            if relative not in extra_allowed:
                extra_allowed.append(relative)
        item = dict(subtask)
        if verify_command:
            item["verify_command"] = str(verify_command)
        elif job_verify and not str(item.get("verify_command") or "").strip():
            item["verify_command"] = job_verify
        phases.append(
            _phase_from_subtask(
                item,
                index=index,
                phase_id=phase_id,
                targets=targets,
                root=base,
                allow_new_files=allow_new_files,
                extra_allowed=extra_allowed,
            )
        )

    for index, subtask in enumerate(subtasks):
        phase = phases[index]
        raw_id = str(subtask.get("id") or f"S{index + 1}")
        deps: list[str] = []
        for dep in subtask.get("depends_on") or []:
            mapped = subtask_to_phase.get(str(dep))
            if not mapped:
                raise PlanningError(f"{raw_id}: unknown dependency {dep}")
            if mapped != phase["id"] and mapped not in deps:
                deps.append(mapped)
        phase["depends_on"] = deps

    proposed = _propose_tier(
        phases, all_targets, explicit=tier, low_risk_opt_in=low_risk_opt_in
    )
    total_requests = sum(int(phase["budget"]["requests"]) for phase in phases) + 8
    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(run_id),
        "goal": str(source.get("objective") or source.get("goal") or "").strip()
        or "Adapted plan",
        "base_sha": str(base_sha),
        "execution_tier": proposed,
        "planner_level": _planner_for_tier(proposed),
        "budget": {
            "requests": total_requests,
            "cost_usd": round(0.70 * max(1, len(phases)), 2),
            "wall_seconds": 1200 * max(1, len(phases)),
            "emergency_reserve_pct": 10,
        },
        "environment": {
            "env_allow": ["PATH", "PYTHONPATH"],
            "network_for_verify": False,
            "seed_files": [],
        },
        "phases": phases,
    }
    if proposed == "E1":
        plan["low_risk_opt_in"] = True

    validated = validate_plan(plan, worktree=base)
    digest = validated["plan_hash"]
    source_digest = _sha256_json(source)
    summary = (
        f"{proposed}/{plan['planner_level']}: {len(phases)} phase(s), "
        f"{len(all_targets)} target(s), tier proposed from "
        f"{'explicit request' if tier else 'risk heuristic'}"
    )
    result = PlanAdapterResult(
        plan=plan,
        plan_hash=digest,
        tier=proposed,
        planner=str(plan["planner_level"]),
        source="v1_plan",
        source_hash=source_digest,
        summary=summary,
    )
    return _maybe_council(
        result,
        council=council,
        root=base,
        planner_models=planner_models,
        planner_call=planner_call,
        council_rounds=council_rounds,
    )


def from_goal(
    goal: str,
    *,
    run_id: str,
    base_sha: str,
    root: str | Path = ".",
    targets: list[str] | None = None,
    verify_command: str | None = None,
    tier: str | None = None,
    low_risk_opt_in: bool = False,
    allow_new_files: bool = False,
    level: int | None = None,
    max_rounds: int = 2,
    council: bool | None = None,
    planner_models: Sequence[str] = (),
    planner_call: Callable[[str, str | None, str], Any] | None = None,
    council_rounds: int = 1,
) -> PlanAdapterResult:
    """Build a one-phase v2 plan from a natural-language goal."""
    text = str(goal or "").strip()
    if not text:
        raise PlanningError("goal is empty")
    selected = [str(item) for item in (targets or [])]
    if not selected:
        selected = detect_targets(text, root, allow_new_files=allow_new_files)
    if not selected:
        raise PlanningError(
            "goal does not name a target file; pass targets explicitly "
            "(or --allow-new-files when the goal creates a new file)"
        )
    chosen_level = int(level) if level is not None else (3 if len(selected) > 1 else 1)
    subtask = {
        "id": "S1",
        "title": text[:120],
        "objective": text,
        "boundaries": "Only the target files listed by the adapter",
        "input_context": "Generated by v2.planner_adapter from a goal",
        "acceptance_criteria": (
            verify_command
            or f"verify exits 0 and {', '.join(selected)} contains the change"
        ),
        "verify_command": verify_command or default_verify_command(selected),
        "target_files": selected,
        "level": max(0, min(3, chosen_level)),
        "max_rounds": max(1, min(4, int(max_rounds or 1))),
        "depends_on": [],
    }
    source = {"job_id": "J1", "objective": text, "subtasks": [subtask]}
    result = from_v1_plan(
        source,
        run_id=run_id,
        base_sha=base_sha,
        root=root,
        tier=tier,
        low_risk_opt_in=low_risk_opt_in,
        allow_new_files=allow_new_files,
        verify_command=verify_command,
        council=council,
        planner_models=planner_models,
        planner_call=planner_call,
        council_rounds=council_rounds,
    )
    return replace(
        result,
        source="goal",
        source_hash=_sha256_text(text + "\n" + "\n".join(selected)),
    )


def from_order(
    order: Mapping[str, Any],
    *,
    run_id: str,
    base_sha: str,
    root: str | Path = ".",
    tier: str | None = None,
    low_risk_opt_in: bool = False,
    allow_new_files: bool = True,
    verify_command: str | None = None,
    council: bool | None = None,
    planner_models: Sequence[str] = (),
    planner_call: Callable[[str, str | None, str], Any] | None = None,
    council_rounds: int = 1,
) -> PlanAdapterResult:
    """Adapt a v1 ``order.json`` (one validated Work Order) into a v2 plan.

    The shim keeps v1's Work Order field names and does not re-implement the
    v1 executor: the order becomes a one-subtask v1-shaped plan and then goes
    through the same validated adapter. Orders may target a new file (v1 allowed
    this), so existence checks are relaxed unless the caller says otherwise.
    """
    if not isinstance(order, Mapping):
        raise PlanningError("order must be an object")
    if isinstance(order.get("subtasks"), list) or isinstance(order.get("phases"), list):
        adapted = adapt_source(
            order,
            run_id=run_id,
            base_sha=base_sha,
            root=root,
            tier=tier,
            low_risk_opt_in=low_risk_opt_in,
            allow_new_files=allow_new_files,
            verify_command=verify_command,
            council=council,
            planner_models=planner_models,
            planner_call=planner_call,
            council_rounds=council_rounds,
        )
        return replace(
            adapted,
            source="order",
            source_hash=_sha256_json(order),
        )
    objective = str(order.get("objective") or "").strip()
    targets = [str(item) for item in (order.get("target_files") or [])]
    verify = str(order.get("verify_command") or "").strip()
    if not objective:
        raise PlanningError("order has no objective")
    if not targets:
        raise PlanningError("order has no target_files")
    if not verify:
        raise PlanningError("order has no verify_command")
    subtask = {
        "id": str(order.get("task_id") or "S1"),
        "title": str(order.get("title") or objective[:120]),
        "objective": objective,
        "boundaries": str(order.get("boundaries") or "Only the target files"),
        "input_context": str(order.get("input_context") or ""),
        "acceptance_criteria": str(
            order.get("acceptance_criteria")
            or "verify exits 0 and the target contains the requested change"
        ),
        "verify_command": verify,
        "target_files": targets,
        "level": int(order.get("level", 1) or 1),
        "max_rounds": int(order.get("max_rounds", 1) or 1),
        "depends_on": [],
    }
    source = {
        "job_id": str(order.get("task_id") or "J1"),
        "objective": objective,
        "subtasks": [subtask],
        "allowed_paths": list(order.get("sandbox_files") or []),
        "verify_command": verify,
    }
    result = from_v1_plan(
        source,
        run_id=run_id,
        base_sha=base_sha,
        root=root,
        tier=tier,
        low_risk_opt_in=low_risk_opt_in,
        allow_new_files=allow_new_files,
        verify_command=verify_command,
        council=council,
        planner_models=planner_models,
        planner_call=planner_call,
        council_rounds=council_rounds,
    )
    return replace(
        result,
        source="order",
        source_hash=_sha256_json(order),
    )


def load_source_plan(path: str | Path) -> dict[str, Any]:
    source_path = Path(path)
    try:
        text = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanningError(f"cannot read plan: {exc}") from exc
    if source_path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise PlanningError(
                "YAML plans need PyYAML; convert the plan to JSON first"
            ) from exc
        try:
            payload = yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001
            raise PlanningError(f"cannot parse YAML plan: {exc}") from exc
    else:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PlanningError(f"cannot parse JSON plan: {exc}") from exc
    if not isinstance(payload, dict):
        raise PlanningError("plan root must be an object")
    return payload


def adapt_source(
    source: Mapping[str, Any],
    *,
    run_id: str,
    base_sha: str,
    root: str | Path = ".",
    tier: str | None = None,
    low_risk_opt_in: bool = False,
    allow_new_files: bool = False,
    verify_command: str | None = None,
    council: bool | None = None,
    planner_models: Sequence[str] = (),
    planner_call: Callable[[str, str | None, str], Any] | None = None,
    council_rounds: int = 1,
) -> PlanAdapterResult:
    """Accept either a v1 plan, a tasks object or an existing v2 MasterPlan."""
    base = Path(root).expanduser().resolve()
    if str(source.get("schema_version") or "") == SCHEMA_VERSION and source.get("phases"):
        existing = dict(source)
        proposed = _propose_tier(
            list(existing.get("phases") or []),
            [
                str(target)
                for phase in existing.get("phases") or []
                for work_order in phase.get("work_orders") or []
                for target in work_order.get("target_files") or []
            ],
            explicit=tier or existing.get("execution_tier"),
            low_risk_opt_in=low_risk_opt_in
            or existing.get("low_risk_opt_in") is True,
        )
        existing["execution_tier"] = proposed
        existing["planner_level"] = _planner_for_tier(proposed)
        if proposed == "E1":
            existing["low_risk_opt_in"] = True
        validated = validate_plan(existing, worktree=base)
        result = PlanAdapterResult(
            plan=existing,
            plan_hash=validated["plan_hash"],
            tier=proposed,
            planner=str(existing["planner_level"]),
            source="v2_plan",
            source_hash=_sha256_json(source),
            summary=f"{proposed}/{existing['planner_level']}: existing v2 plan",
        )
        return _maybe_council(
            result,
            council=council,
            root=base,
            planner_models=planner_models,
            planner_call=planner_call,
            council_rounds=council_rounds,
        )
    return from_v1_plan(
        source,
        run_id=run_id,
        base_sha=base_sha,
        root=root,
        tier=tier,
        low_risk_opt_in=low_risk_opt_in,
        allow_new_files=allow_new_files,
        verify_command=verify_command,
        council=council,
        planner_models=planner_models,
        planner_call=planner_call,
        council_rounds=council_rounds,
    )


def adapt_file(
    path: str | Path,
    *,
    run_id: str,
    base_sha: str,
    root: str | Path = ".",
    tier: str | None = None,
    low_risk_opt_in: bool = False,
    allow_new_files: bool = False,
    verify_command: str | None = None,
    council: bool | None = None,
    planner_models: Sequence[str] = (),
    planner_call: Callable[[str, str | None, str], Any] | None = None,
    council_rounds: int = 1,
) -> PlanAdapterResult:
    return adapt_source(
        load_source_plan(path),
        run_id=run_id,
        base_sha=base_sha,
        root=root,
        tier=tier,
        low_risk_opt_in=low_risk_opt_in,
        allow_new_files=allow_new_files,
        verify_command=verify_command,
        council=council,
        planner_models=planner_models,
        planner_call=planner_call,
        council_rounds=council_rounds,
    )


def write_plan(path: str | Path, result: PlanAdapterResult) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result.plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="goal/v1 plan/order -> v2 MasterPlan")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--goal")
    source.add_argument("--plan", help="v1 plan JSON/YAML or an existing v2 plan")
    source.add_argument("--order", help="v1 order.json (one Work Order)")
    parser.add_argument("--root", default=".")
    parser.add_argument("--run-id", default="plan-preview")
    parser.add_argument("--base-sha", default="HEAD")
    parser.add_argument("--tier", choices=("E1", "E2", "E3", "E4"), default=None)
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--verify", default=None)
    parser.add_argument("--e1-opt-in", dest="e1_opt_in", action="store_true")
    parser.add_argument("--allow-new-files", action="store_true")
    parser.add_argument(
        "--council", dest="council", action="store_true", default=None,
        help="force the P1/P2/P3 council even for a P1 tier",
    )
    parser.add_argument(
        "--no-council", dest="council", action="store_false",
        help="skip the planning council",
    )
    parser.add_argument("--planner-model", action="append", default=[],
                        help="model for one council seat (repeatable)")
    parser.add_argument("--council-rounds", type=int, default=1,
                        choices=(1, 2))
    parser.add_argument("--out", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    council_kwargs = {
        "council": args.council,
        "planner_models": args.planner_model,
        "council_rounds": args.council_rounds,
    }
    try:
        if args.goal:
            result = from_goal(
                args.goal,
                run_id=args.run_id,
                base_sha=args.base_sha,
                root=args.root,
                targets=args.target,
                verify_command=args.verify,
                tier=args.tier,
                low_risk_opt_in=args.e1_opt_in,
                allow_new_files=args.allow_new_files,
                **council_kwargs,
            )
        elif args.order:
            result = from_order(
                load_source_plan(args.order),
                run_id=args.run_id,
                base_sha=args.base_sha,
                root=args.root,
                tier=args.tier,
                low_risk_opt_in=args.e1_opt_in,
                allow_new_files=args.allow_new_files,
                verify_command=args.verify,
                **council_kwargs,
            )
        else:
            result = adapt_file(
                args.plan,
                run_id=args.run_id,
                base_sha=args.base_sha,
                root=args.root,
                tier=args.tier,
                low_risk_opt_in=args.e1_opt_in,
                allow_new_files=args.allow_new_files,
                verify_command=args.verify,
                **council_kwargs,
            )
    except (PlanningError, PlanValidationError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    payload = result.as_dict()
    if args.out:
        written = write_plan(args.out, result)
        payload["out"] = str(written)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(result.summary)
        print(f"plan_hash: {result.plan_hash}")
        if result.council:
            print(
                f"council: {result.council['level']} "
                f"{result.council['valid_seats']}/{result.council['required_planners']} "
                f"({result.council['selected_role']})"
            )
        if args.out:
            print(f"out: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
