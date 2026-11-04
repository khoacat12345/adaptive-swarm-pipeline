#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Spec Kit -> v2.1 bridge.

Spec Kit's human-facing tasks.md is not a contract. This module accepts only a
canonical YAML (or JSON) ``tasks.yaml`` and converts it to the validated
MasterPlan shape used by sign-off and execution.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .errors import V2Error
from .plan import PlanValidationError, validate_plan

BRIDGE_VERSION = "1.0"
TASK_SCHEMA_VERSION = "1.0"
DEFAULT_FORBIDDEN = [".git", ".env", ".agents/tmp"]


class TasksSchemaError(V2Error):
    code = "TASKS_SCHEMA_INVALID"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_relative(value: Any) -> bool:
    text = str(value or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return False
    return ".." not in Path(text).parts


def load_tasks(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if path.suffix.lower() in {".md", ".markdown"}:
        raise TasksSchemaError(
            "refusing prose tasks.md; provide canonical tasks.yaml (or .json) with verify_command"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TasksSchemaError(f"cannot read tasks file: {exc}") from exc
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(text)
    except ImportError:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TasksSchemaError(
                "PyYAML is not installed; provide tasks.json instead"
            ) from exc
    except Exception as exc:  # noqa: BLE001
        raise TasksSchemaError(f"invalid YAML/JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TasksSchemaError("tasks root must be an object")
    return payload


def _validate_shape(tasks: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    if str(tasks.get("schema_version") or "") != TASK_SCHEMA_VERSION:
        problems.append(f"schema_version must be {TASK_SCHEMA_VERSION}")
    if not str(tasks.get("goal") or "").strip():
        problems.append("goal is required")
    if not isinstance(tasks.get("budget"), Mapping):
        problems.append("top-level budget is required")
    phases = tasks.get("phases")
    if not isinstance(phases, list) or not phases:
        problems.append("phases must be a non-empty list")
        return problems
    if len(phases) > 6:
        problems.append("maximum 6 phases")
    phase_ids: set[str] = set()
    for index, phase in enumerate(phases):
        if not isinstance(phase, Mapping):
            problems.append(f"phase {index} must be an object")
            continue
        phase_id = str(phase.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", phase_id):
            problems.append(f"invalid phase id: {phase_id!r}")
        elif phase_id in phase_ids:
            problems.append(f"duplicate phase id: {phase_id}")
        phase_ids.add(phase_id)
        if not str(phase.get("title") or "").strip():
            problems.append(f"{phase_id}: title is required")
        if not isinstance(phase.get("allowed_paths"), list) or not phase.get("allowed_paths"):
            problems.append(f"{phase_id}: allowed_paths is required")
        if not isinstance(phase.get("budget"), Mapping):
            problems.append(f"{phase_id}: budget is required")
        orders = phase.get("work_orders")
        if not isinstance(orders, list) or not orders:
            problems.append(f"{phase_id}: work_orders is required")
        elif len(orders) > 12:
            problems.append(f"{phase_id}: maximum 12 work orders")
        for order_index, order in enumerate(orders or []):
            if not isinstance(order, Mapping):
                problems.append(f"{phase_id}: work order {order_index} must be an object")
                continue
            for field in ("objective", "acceptance_criteria", "verify_command", "target_files"):
                if not order.get(field):
                    problems.append(f"{phase_id}/WO{order_index}: missing {field}")
            for relative in order.get("target_files") or []:
                if not _safe_relative(relative):
                    problems.append(f"{phase_id}: unsafe target_path {relative}")
    return problems


def _artifact_hash(root: Path, relative: str) -> str:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise TasksSchemaError(f"source artifact escapes root: {relative}") from exc
    if not path.is_file():
        raise TasksSchemaError(f"source artifact does not exist: {relative}")
    return _sha256(path)


def build_master_plan(
    tasks: Mapping[str, Any],
    *,
    root: str | Path,
    run_id: str,
    base_sha: str,
    worktree: str | Path | None = None,
    constitution_hash: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    problems = _validate_shape(tasks)
    if problems:
        raise TasksSchemaError("; ".join(problems[:12]))
    root_path = Path(root).resolve()
    source = dict(tasks.get("source") or {})
    artifact_hashes: dict[str, str] = {}
    for key in ("spec", "plan", "tasks"):
        relative = source.get(key)
        if not relative:
            continue
        if not _safe_relative(relative):
            raise TasksSchemaError(f"unsafe source.{key} path: {relative}")
        artifact_hashes[key] = _artifact_hash(root_path, str(relative))
    phases = []
    for phase in tasks.get("phases") or []:
        phases.append({
            "id": str(phase["id"]),
            "title": str(phase.get("title") or phase["id"]),
            "depends_on": [str(item) for item in (phase.get("depends_on") or [])],
            "allowed_paths": [str(item) for item in (phase.get("allowed_paths") or [])],
            "forbidden_paths": [str(item) for item in (
                phase.get("forbidden_paths") or DEFAULT_FORBIDDEN
            )],
            "allow_new_files": bool(phase.get("allow_new_files", False)),
            "budget": dict(phase.get("budget") or {}),
            "work_orders": [dict(item) for item in (phase.get("work_orders") or [])],
            "rollback": dict(phase.get("rollback") or {"mode": "last_checkpoint"}),
            "cleanup": str(phase.get("cleanup") or "disabled"),
        })
    plan = {
        "schema_version": "2.1",
        "run_id": str(run_id),
        "goal": str(tasks["goal"]),
        "base_sha": str(base_sha),
        "budget": dict(tasks["budget"]),
        "environment": dict(tasks.get("environment") or {
            "env_allow": ["PATH", "PYTHONPATH"],
            "network_for_verify": False,
            "seed_files": [],
        }),
        "model_snapshot": dict(tasks.get("model_snapshot") or {}),
        "source": source,
        "source_artifacts": artifact_hashes,
        "constitution_hash": constitution_hash,
        "bridge": {
            "name": "speckit_bridge",
            "version": BRIDGE_VERSION,
            "tasks_schema_version": TASK_SCHEMA_VERSION,
        },
        "phases": phases,
    }
    try:
        validated = validate_plan(plan, worktree=worktree)
    except PlanValidationError as exc:
        raise TasksSchemaError("; ".join(exc.problems[:12])) from exc
    return plan, validated


def bridge_spec_kit(
    root: str | Path,
    tasks_path: str | Path,
    *,
    run_id: str,
    base_sha: str,
    worktree: str | Path | None = None,
    out: str | Path | None = None,
) -> dict[str, Any]:
    from .constitution import constitution_hash

    tasks_file = Path(tasks_path)
    if not tasks_file.is_absolute():
        tasks_file = Path(root) / tasks_file
    tasks = load_tasks(tasks_file)
    # If source.tasks was omitted, bind the actual file path.
    if not (tasks.get("source") or {}).get("tasks"):
        tasks.setdefault("source", {})["tasks"] = str(tasks_file.resolve().relative_to(Path(root).resolve()))
    plan, validated = build_master_plan(
        tasks,
        root=root,
        run_id=run_id,
        base_sha=base_sha,
        worktree=worktree,
        constitution_hash=constitution_hash(root),
    )
    if out:
        out_path = Path(out)
        if not out_path.is_absolute():
            out_path = Path(root) / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return {
        "plan": plan,
        "validated": {
            "plan_hash": validated["plan_hash"],
            "budget_hash": validated["budget_hash"],
            "model_snapshot_hash": validated["model_snapshot_hash"],
            "phase_ids": validated["phase_ids"],
        },
        "tasks_path": str(tasks_file),
    }
