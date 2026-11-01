#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2 plan schema, dependency validation and immutable hashes.

The plan is a proposal until the user signs its exact hash. Any later change
must return to AWAITING_SIGNOFF; there is no "minor plan edit".
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .budget import BudgetConfig, Envelope, PhaseBudget
from .errors import V2Error

SCHEMA_VERSION = "2.1"
MAX_PHASES = 6
MAX_WORK_ORDERS = 12
DEFAULT_FORBIDDEN = (".git", ".env", ".agents/tmp", ".agents/config.json")


class PlanValidationError(V2Error):
    code = "PLAN_INVALID"

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("plan invalid: " + "; ".join(self.problems[:8]),
                         detail="\n".join(self.problems))


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_obj(payload: Mapping[str, Any] | None) -> str:
    return hashlib.sha256(canonical_json(payload or {}).encode("utf-8")).hexdigest()


def plan_hash(plan: Mapping[str, Any]) -> str:
    body = {
        key: value
        for key, value in plan.items()
        if key not in {"plan_hash", "signature", "_meta"}
    }
    return _hash_obj(body)


def model_snapshot_hash(plan: Mapping[str, Any]) -> str:
    snapshot = plan.get("model_snapshot") or {}
    return _hash_obj(snapshot if isinstance(snapshot, Mapping) else {"value": snapshot})


def _safe_path(value: Any) -> bool:
    text = str(value or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return False
    parts = Path(text).parts
    return ".." not in parts and text not in {".", ""}


def _inside(path: str, roots: list[str]) -> bool:
    candidate = str(path).replace("\\", "/").strip("/")
    for root in roots:
        normalized = str(root).replace("\\", "/").strip("/")
        if candidate == normalized or candidate.startswith(normalized + "/"):
            return True
    return False


def _budget_value(data: Mapping[str, Any], names: tuple[str, ...], default: Any) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return default


def budget_from_plan(plan: Mapping[str, Any]) -> BudgetConfig:
    global_data = plan.get("budget") or {}
    if isinstance(global_data, Mapping) and "global" in global_data:
        global_data = global_data.get("global") or {}
    envelope = Envelope.from_dict(
        {
            "max_requests": _budget_value(
                global_data, ("requests", "max_requests"), 120
            ),
            "max_cost_usd": _budget_value(
                global_data, ("cost_usd", "max_cost_usd"), 3.5
            ),
            "max_wall_seconds": _budget_value(
                global_data, ("wall_seconds", "max_wall_seconds"), 36000
            ),
            "emergency_reserve_pct": _budget_value(
                global_data, ("emergency_reserve_pct",), 10.0
            ),
            "max_repair_attempts_per_phase": _budget_value(
                global_data, ("max_repair_attempts_per_phase",), 3
            ),
            "max_consecutive_no_progress": _budget_value(
                global_data, ("max_consecutive_no_progress",), 2
            ),
        }
    )
    phases: dict[str, PhaseBudget] = {}
    for phase in plan.get("phases") or []:
        if not isinstance(phase, Mapping):
            continue
        phase_id = str(phase.get("id") or "").strip()
        if not phase_id:
            continue
        raw = phase.get("budget") or {}
        phases[phase_id] = PhaseBudget.from_dict(
            {
                "max_requests": _budget_value(raw, ("requests", "max_requests"), 1),
                "max_cost_usd": _budget_value(raw, ("cost_usd", "max_cost_usd"), 0.0),
                "max_wall_seconds": _budget_value(
                    raw, ("wall_seconds", "max_wall_seconds"), 1
                ),
                "repair_reserve_pct": _budget_value(
                    raw, ("repair_reserve_pct",), 20.0
                ),
            }
        )
    return BudgetConfig(envelope=envelope, phases=phases)


def _validate_work_order(
    item: Mapping[str, Any],
    *,
    index: int,
    phase_id: str,
    allowed_paths: list[str],
    forbidden_paths: list[str],
    worktree: Path | None,
    allow_new_files: bool,
) -> list[str]:
    problems: list[str] = []
    if not isinstance(item, Mapping):
        return [f"{phase_id}: work order {index} must be an object"]
    work_id = str(item.get("id") or f"W{index + 1}")
    required = ("objective", "acceptance_criteria", "verify_command", "target_files")
    for field in required:
        if not str(item.get(field) or "").strip():
            problems.append(f"{phase_id}/{work_id}: missing {field}")
    targets = item.get("target_files") or []
    if not isinstance(targets, list) or not targets:
        problems.append(f"{phase_id}/{work_id}: target_files must be a non-empty list")
        targets = []
    for relative in targets:
        if not _safe_path(relative):
            problems.append(f"{phase_id}/{work_id}: unsafe target file: {relative}")
            continue
        if not _inside(relative, allowed_paths):
            problems.append(f"{phase_id}/{work_id}: target outside allowed_paths: {relative}")
        if _inside(relative, forbidden_paths):
            problems.append(f"{phase_id}/{work_id}: target is forbidden: {relative}")
        if worktree is not None and not allow_new_files and not (worktree / relative).is_file():
            problems.append(f"{phase_id}/{work_id}: target does not exist: {relative}")
    level = item.get("level", 1)
    if level not in (0, 1, 2, 3):
        problems.append(f"{phase_id}/{work_id}: invalid level {level!r}")
    if level == 3 and len(targets) < 2:
        problems.append(f"{phase_id}/{work_id}: level 3 needs at least 2 targets")
    if level != 3 and len(targets) > 1:
        problems.append(f"{phase_id}/{work_id}: level {level} accepts one target")
    max_rounds = item.get("max_rounds", 1)
    if not isinstance(max_rounds, int) or not 1 <= max_rounds <= 4:
        problems.append(f"{phase_id}/{work_id}: max_rounds must be in 1..4")
    command = str(item.get("verify_command") or "").strip()
    if command:
        try:
            import system_bot  # type: ignore

            guard = system_bot.check_command(command)
            if not guard.get("allowed"):
                problems.append(
                    f"{phase_id}/{work_id}: verify_command blocked: {guard.get('reason')}"
                )
        except ImportError:
            problems.append(f"{phase_id}/{work_id}: system_bot unavailable for verify validation")
    return problems


def validate_plan(
    plan: Mapping[str, Any],
    *,
    worktree: str | Path | None = None,
) -> dict[str, Any]:
    """Validate a master plan and return its immutable hashes."""
    errors: list[str] = []
    if not isinstance(plan, Mapping):
        raise PlanValidationError(["plan must be an object"])
    if str(plan.get("schema_version") or "") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    for field in ("run_id", "goal", "base_sha", "budget", "phases"):
        if not plan.get(field):
            errors.append(f"missing top-level {field}")
    phases = plan.get("phases") or []
    if not isinstance(phases, list) or not phases:
        errors.append("phases must be a non-empty list")
        phases = []
    if len(phases) > MAX_PHASES:
        errors.append(f"too many phases: {len(phases)} > {MAX_PHASES}")

    phase_ids: list[str] = []
    dependency_map: dict[str, list[str]] = {}
    worktree_path = Path(worktree).resolve() if worktree else None
    for index, phase in enumerate(phases):
        if not isinstance(phase, Mapping):
            errors.append(f"phase {index} must be an object")
            continue
        phase_id = str(phase.get("id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", phase_id):
            errors.append(f"invalid phase id: {phase_id!r}")
            continue
        if phase_id in phase_ids:
            errors.append(f"duplicate phase id: {phase_id}")
        phase_ids.append(phase_id)
        allowed = phase.get("allowed_paths") or []
        forbidden = list(DEFAULT_FORBIDDEN) + list(phase.get("forbidden_paths") or [])
        if not isinstance(allowed, list) or not allowed:
            errors.append(f"{phase_id}: allowed_paths must be a non-empty list")
            allowed = []
        for relative in allowed:
            if not _safe_path(relative):
                errors.append(f"{phase_id}: unsafe allowed path: {relative}")
        for relative in forbidden:
            if not _safe_path(relative):
                errors.append(f"{phase_id}: unsafe forbidden path: {relative}")
        work_orders = phase.get("work_orders") or []
        if not isinstance(work_orders, list) or not work_orders:
            errors.append(f"{phase_id}: work_orders must be a non-empty list")
            work_orders = []
        if len(work_orders) > MAX_WORK_ORDERS:
            errors.append(f"{phase_id}: too many work orders: {len(work_orders)}")
        depends_on = phase.get("depends_on") or []
        if not isinstance(depends_on, list):
            errors.append(f"{phase_id}: depends_on must be a list")
            depends_on = []
        dependency_map[phase_id] = [str(item) for item in depends_on]
        if not isinstance(phase.get("budget"), Mapping):
            errors.append(f"{phase_id}: budget is required")
        allow_new = bool(phase.get("allow_new_files", False))
        for order_index, item in enumerate(work_orders):
            errors.extend(
                _validate_work_order(
                    item,
                    index=order_index,
                    phase_id=phase_id,
                    allowed_paths=[str(value) for value in allowed],
                    forbidden_paths=[str(value) for value in forbidden],
                    worktree=worktree_path,
                    allow_new_files=allow_new,
                )
            )

    # Dependency graph: every reference must exist and be acyclic.
    known = set(phase_ids)
    for phase_id, deps in dependency_map.items():
        for dep in deps:
            if dep not in known:
                errors.append(f"{phase_id}: unknown dependency {dep}")
            if dep == phase_id:
                errors.append(f"{phase_id}: cannot depend on itself")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            errors.append(f"dependency cycle at {node}")
            return
        if node in visited:
            return
        visiting.add(node)
        for dep in dependency_map.get(node, []):
            if dep in known:
                visit(dep)
        visiting.remove(node)
        visited.add(node)

    for phase_id in phase_ids:
        visit(phase_id)

    # Environment/seed files are optional but must stay inside the worktree.
    environment = plan.get("environment") or {}
    if not isinstance(environment, Mapping):
        errors.append("environment must be an object")
        environment = {}
    seed_files = environment.get("seed_files") or []
    if not isinstance(seed_files, list):
        errors.append("environment.seed_files must be a list")
        seed_files = []
    for relative in seed_files:
        if not _safe_path(relative):
            errors.append(f"unsafe seed file: {relative}")
        if worktree_path is not None and not (worktree_path / relative).is_file():
            errors.append(f"seed file does not exist: {relative}")

    if not errors:
        try:
            budget = budget_from_plan(plan).validate()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"budget invalid: {exc}")
        else:
            return {
                "plan": dict(plan),
                "plan_hash": plan_hash(plan),
                "budget_hash": budget.digest,
                "model_snapshot_hash": model_snapshot_hash(plan),
                "budget": budget,
                "phase_ids": phase_ids,
            }
    raise PlanValidationError(errors)


def load_plan(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanValidationError([f"cannot read plan: {exc}"]) from exc
    if not isinstance(payload, dict):
        raise PlanValidationError(["plan root must be an object"])
    return payload


def save_plan(path: str | Path, plan: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_example(run_id: str, base_sha: str, *, phase_ids: list[str] | None = None) -> dict[str, Any]:
    """Small valid plan used by tests and the CLI preview."""
    phases = []
    for index, phase_id in enumerate(phase_ids or ["P1"], start=1):
        phases.append(
            {
                "id": phase_id,
                "title": f"Phase {index}",
                "depends_on": [phase_ids[index - 2]] if index > 1 and phase_ids else [],
                "allowed_paths": ["README.md"],
                "forbidden_paths": [".git", ".env", ".agents/tmp"],
                "budget": {"requests": 24, "cost_usd": 0.70, "wall_seconds": 7200},
                "work_orders": [
                    {
                        "id": "S1",
                        "objective": "Update the README",
                        "boundaries": "Only README.md",
                        "input_context": "The repository README",
                        "acceptance_criteria": "README exists",
                        "verify_command": "python -c \"print('ok')\"",
                        "target_files": ["README.md"],
                        "level": 1,
                        "max_rounds": 1,
                    }
                ],
                "rollback": {"mode": "last_checkpoint"},
                "cleanup": "disabled",
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "goal": "example",
        "base_sha": base_sha,
        "budget": {
            "requests": 120,
            "cost_usd": 3.5,
            "wall_seconds": 36000,
            "emergency_reserve_pct": 10,
        },
        "environment": {"env_allow": ["PATH", "PYTHONPATH"], "network_for_verify": False},
        "phases": phases,
    }
