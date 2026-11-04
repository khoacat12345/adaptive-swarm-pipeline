#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2 sign-off: bind the user's approval to exact plan/budget/model hashes."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .constitution import load_constitution
from .errors import PlanChangedError, V2Error
from .plan import (
    budget_from_plan,
    model_snapshot_hash,
    plan_hash,
    validate_plan,
)
from .state import RunStore, atomic_write_json

SIGNOFF_FILENAME = "signoff.json"


class SignoffError(V2Error):
    code = "SIGNOFF_INVALID"


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def signoff_path(store: RunStore) -> Path:
    return store.run_dir / SIGNOFF_FILENAME


def load_signoff(store: RunStore) -> dict[str, Any]:
    path = signoff_path(store)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SignoffError(f"cannot read signoff: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def create_signoff(
    store: RunStore,
    plan: Mapping[str, Any],
    *,
    worktree: str | Path | None = None,
    approved_by: str = "local-user",
    auto_merge: bool = False,
    allow_dirty_snapshot: bool = False,
) -> dict[str, Any]:
    """Validate and persist an immutable sign-off; caller owns the transition."""
    workspace = (store.load().get("workspace") or {})
    wt = worktree or workspace.get("worktree_path")
    validated = validate_plan(plan, worktree=wt)
    budget = budget_from_plan(plan)
    constitution = load_constitution(store.root)
    record = {
        "schema_version": "2.1",
        "run_id": store.run_id,
        "plan_hash": validated["plan_hash"],
        "budget_hash": validated["budget_hash"],
        "model_snapshot_hash": validated["model_snapshot_hash"],
        "constitution_hash": constitution["hash"],
        "constitution_path": constitution["path"],
        "constitution_complete": constitution["complete"],
        "approved_by": str(approved_by or "local-user"),
        "approved_at": _now(),
        "nonce": secrets.token_hex(16),
        "auto_merge": bool(auto_merge),
        "allow_dirty_snapshot": bool(allow_dirty_snapshot),
        "plan_file": str(store.run_dir / "plan.json"),
        "budget": budget.as_dict(),
    }
    # Save the exact signed plan and signature under the run state root.
    plan_path = store.run_dir / "plan.json"
    atomic_write_json(plan_path, dict(plan))
    atomic_write_json(signoff_path(store), record)
    store.update(
        plan_hash=record["plan_hash"],
        budget_hash=record["budget_hash"],
        model_snapshot_hash=record["model_snapshot_hash"],
        plan_file=str(plan_path),
    )
    store.append_event("signoff.created", {
        "plan_hash": record["plan_hash"],
        "budget_hash": record["budget_hash"],
        "model_snapshot_hash": record["model_snapshot_hash"],
        "constitution_hash": record["constitution_hash"],
        "approved_by": record["approved_by"],
    })
    return record


def assert_signoff(store: RunStore, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed if the plan/budget/model snapshot changed after approval."""
    record = load_signoff(store)
    if not record:
        raise PlanChangedError("no signoff exists for this run")
    expected = {
        "plan_hash": plan_hash(plan),
        "budget_hash": budget_from_plan(plan).digest,
        "model_snapshot_hash": model_snapshot_hash(plan),
    }
    # Constitution is project state, not part of the plan body. Bind its hash
    # separately so editing the constitution invalidates an old approval.
    expected["constitution_hash"] = load_constitution(store.root)["hash"]
    for key, value in expected.items():
        if record.get(key) != value:
            raise PlanChangedError(
                f"{key} changed after signoff: {record.get(key)} != {value}"
            )
    if record.get("run_id") != store.run_id:
        raise PlanChangedError("signoff run_id does not match the manifest")
    return record


def needs_reapproval(store: RunStore, plan: Mapping[str, Any]) -> bool:
    try:
        assert_signoff(store, plan)
        return False
    except PlanChangedError:
        return True


def signoff_summary(store: RunStore) -> dict[str, Any]:
    record = load_signoff(store)
    return {
        "signed": bool(record),
        "plan_hash": record.get("plan_hash"),
        "budget_hash": record.get("budget_hash"),
        "model_snapshot_hash": record.get("model_snapshot_hash"),
        "constitution_hash": record.get("constitution_hash"),
        "constitution_path": record.get("constitution_path"),
        "constitution_complete": bool(record.get("constitution_complete", False)),
        "approved_by": record.get("approved_by"),
        "approved_at": record.get("approved_at"),
        "auto_merge": bool(record.get("auto_merge", False)),
        "allow_dirty_snapshot": bool(record.get("allow_dirty_snapshot", False)),
    }
