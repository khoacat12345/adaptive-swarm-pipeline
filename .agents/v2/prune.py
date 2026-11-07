#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Retention cleanup for v2 Git worktrees and branches.

Run state (manifest, checkpoints, handoff patch/report, events, budget) is kept.
Only the isolated worktree directory and its temporary `swarm/*` branch are
removed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import isolation
from .state import TERMINAL_STATES, resolve_state_root

COMPLETED_STATES = set(TERMINAL_STATES) | {"HANDOFF_READY"}
ACTIVE_STATES = {
    "CREATED", "PRECHECKED", "PLANNING", "PROVISIONING",
    "PHASE_EXECUTING", "PHASE_VERIFYING", "AWAITING_SIGNOFF",
    "AWAITING_USER_CONFIRMATION", "PAUSED", "BLOCKED",
}


def _parse_time(value: Any) -> datetime | None:
    try:
        text = str(value or "").replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


def _age_days(manifest: dict[str, Any]) -> float:
    created = _parse_time(manifest.get("created_at")) or _parse_time(manifest.get("updated_at"))
    if created is None:
        return 0.0
    return (datetime.now(timezone.utc) - created).total_seconds() / 86400.0


def _manifest_runs(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    base = resolve_state_root(root) / "runs"
    if not base.is_dir():
        return []
    rows = []
    for run_dir in sorted(base.iterdir()):
        manifest = run_dir / "run_manifest.json"
        if not manifest.exists():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            rows.append((run_dir, payload))
    return rows


def prune(root: str | Path, *, days: float = 7, force: bool = False,
          dry_run: bool = False) -> dict[str, Any]:
    """Remove stale/completed run worktrees; keep all evidence under state root."""
    root_path = Path(root).expanduser().resolve()
    removed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for run_dir, manifest in _manifest_runs(root_path):
        run_id = str(manifest.get("run_id") or run_dir.name)
        status = str(manifest.get("status") or "UNKNOWN")
        age = _age_days(manifest)
        workspace_data = manifest.get("workspace")
        if not isinstance(workspace_data, dict):
            skipped.append({"run_id": run_id, "reason": "no workspace"})
            continue
        try:
            workspace = isolation.Workspace.from_dict(workspace_data)
        except Exception as exc:  # noqa: BLE001
            errors.append({"run_id": run_id, "error": f"workspace: {exc}"})
            continue

        active = status in ACTIVE_STATES
        stale = age >= float(days)
        if active:
            skipped.append({"run_id": run_id, "reason": f"active state {status}", "age_days": round(age, 2)})
            continue
        if status not in COMPLETED_STATES and not stale and not force:
            skipped.append({"run_id": run_id, "reason": f"not completed and younger than {days}d",
                            "age_days": round(age, 2)})
            continue

        item: dict[str, Any] = {
            "run_id": run_id, "status": status, "age_days": round(age, 2),
            "worktree": str(workspace.path), "branch": workspace.branch,
        }
        if dry_run:
            item["dry_run"] = True
            removed.append(item)
            continue
        try:
            if workspace.path.exists():
                isolation.remove_worktree(workspace, keep_branch=False)
                item["worktree_removed"] = True
            else:
                # Worktree already gone; still remove the temporary branch.
                if workspace.branch.startswith(isolation.DEFAULT_BRANCH_PREFIX):
                    isolation._git(workspace.main_root, "branch", "-D", workspace.branch, check=False)
                item["worktree_removed"] = False
            removed.append(item)
        except Exception as exc:  # noqa: BLE001
            errors.append({"run_id": run_id, "error": str(exc)[:300]})
    return {
        "ok": not errors,
        "days": days,
        "force": force,
        "dry_run": dry_run,
        "removed": removed,
        "skipped": skipped,
        "errors": errors,
        "kept": "manifest, checkpoints, events, budget and handoff evidence",
    }
