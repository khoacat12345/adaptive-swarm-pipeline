#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Explicit v2 delivery actions: apply a reviewed handoff, then safely revert it.

`v2 merge` is intentionally a separate operator command. It does not run as
part of execute/handoff, and it never bypasses the plan's verify commands.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ManifestError, StateTransitionError
from .runner import V2Runner
from .state import resolve_state_root

DIFF_FILE_RE = re.compile(r"^diff --git a/(.*?) b/(.*?)$")


class DeliveryError(RuntimeError):
    """Raised when a merge/rollback would be unsafe or incomplete."""


def _git(cwd: str | Path, *args: str, check: bool = True,
         input_text: str | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(cwd), *[str(arg) for arg in args]],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        input=input_text,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:800]
        raise DeliveryError(f"git {' '.join(args)} failed ({result.returncode}): {detail}")
    return result


def _normalize_path(value: str) -> str:
    path = str(value or "").strip().strip('"').replace("\\", "/")
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    while path.startswith("./"):
        path = path[2:]
    return path


def _patch_paths(patch: str) -> list[str]:
    paths: set[str] = set()
    for line in str(patch or "").splitlines():
        match = DIFF_FILE_RE.match(line)
        if match:
            paths.add(_normalize_path(match.group(1)))
            paths.add(_normalize_path(match.group(2)))
            continue
        if line.startswith("--- ") or line.startswith("+++ "):
            value = line[4:].split("\t", 1)[0].strip()
            if value and value != "/dev/null":
                paths.add(_normalize_path(value))
    return sorted(path for path in paths if path)


def _plan_allowed_paths(plan: dict[str, Any]) -> set[str]:
    allowed: set[str] = set()
    for phase in plan.get("phases") or []:
        for item in phase.get("allowed_paths") or []:
            allowed.add(_normalize_path(str(item)))
        for order in phase.get("work_orders") or []:
            for item in order.get("target_files") or []:
                allowed.add(_normalize_path(str(item)))
    return {item for item in allowed if item}


def _path_is_allowed(path: str, allowed: set[str]) -> bool:
    normalized = _normalize_path(path)
    if not normalized or normalized.startswith(("/", "\\")):
        return False
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    if normalized == ".git" or normalized.startswith(".git/"):
        return False
    for prefix in (".agents/tmp/", ".agents/backups/", ".agents/draft/"):
        if normalized.startswith(prefix):
            return False
    for item in allowed:
        item = _normalize_path(item)
        if normalized == item or normalized.startswith(item.rstrip("/") + "/"):
            return True
    return False


def validate_handoff_patch(patch: str, plan: dict[str, Any]) -> list[str]:
    """Validate patch target paths against the signed plan."""
    paths = _patch_paths(patch)
    if not paths:
        raise DeliveryError("handoff patch does not contain a file diff")
    allowed = _plan_allowed_paths(plan)
    if not allowed:
        raise DeliveryError("plan has no allowed_paths/target_files; refusing merge")
    forbidden = [path for path in paths if not _path_is_allowed(path, allowed)]
    if forbidden:
        raise DeliveryError(
            "handoff patch touches paths outside the signed plan: "
            + ", ".join(forbidden[:20])
        )
    return paths


def _state_relative(root: Path, state_root: Path) -> str:
    try:
        return state_root.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return ""


def _assert_clean(root: Path, state_root: Path) -> None:
    result = _git(root, "status", "--porcelain", "--untracked-files=all", check=False)
    if result.returncode != 0:
        raise DeliveryError("cannot inspect main worktree status")
    state_rel = _state_relative(root, state_root)
    dirty: list[str] = []
    for line in (result.stdout or "").splitlines():
        if not line.strip():
            continue
        path = _normalize_path(line[3:].strip())
        if state_rel and (path == state_rel or path.startswith(state_rel + "/")):
            continue
        # Default v2 runtime state is ignored by the project; tolerate it for
        # repositories that have not copied .agents/.gitignore.
        if path.startswith(".agents/tmp/"):
            continue
        dirty.append(line.strip())
    if dirty:
        raise DeliveryError(
            "main worktree is not clean; commit/stash changes before merge/rollback: "
            + "; ".join(dirty[:8])
        )


def _verify_commands(plan: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    for phase in plan.get("phases") or []:
        for order in phase.get("work_orders") or []:
            command = str(order.get("verify_command") or "").strip()
            if command:
                commands.append(command)
    if not commands:
        raise DeliveryError("signed plan has no verify_command; refusing merge")
    return commands


def _run_verify(root: Path, plan: dict[str, Any], timeout: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in _verify_commands(plan):
        try:
            completed = subprocess.run(
                command, cwd=str(root), shell=True, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=max(1, int(timeout)),
            )
            results.append({
                "command": command,
                "exit_code": int(completed.returncode),
                "output_tail": ((completed.stdout or "") + (completed.stderr or ""))[-2000:],
            })
        except subprocess.TimeoutExpired:
            results.append({"command": command, "exit_code": 124, "output_tail": "verify timeout"})
    return results


def merge(root: str | Path, run_id: str, *, state_root: str | Path | None = None,
          message: str | None = None, verify_timeout: int = 300,
          dry_run: bool = False) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve()
    runner = V2Runner(root_path, state_root=state_root)
    store = runner.load(run_id)
    manifest = store.load()
    if manifest.get("status") not in {"HANDOFF_READY", "DONE"}:
        raise StateTransitionError(
            "merge requires HANDOFF_READY or DONE, got "
            f"{manifest.get('status')!r}"
        )
    workspace = manifest.get("workspace") or {}
    if workspace and Path(str(workspace.get("main_root") or "")).resolve() != root_path:
        raise DeliveryError("run workspace does not belong to this repository")
    patch_path = store.run_dir / "handoff" / "handoff.patch"
    if not patch_path.exists():
        raise ManifestError(f"handoff patch not found: {patch_path}")
    plan_path = store.run_dir / "plan.json"
    if not plan_path.exists():
        raise ManifestError(f"run plan not found: {plan_path}")
    patch = patch_path.read_text(encoding="utf-8", errors="replace")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    paths = validate_handoff_patch(patch, plan)
    _assert_clean(root_path, store.state_root)
    check = _git(root_path, "apply", "--check", "--whitespace=nowarn", str(patch_path), check=False)
    if check.returncode != 0:
        raise DeliveryError(
            "handoff patch does not apply cleanly: "
            + (check.stderr or check.stdout or "").strip()[:600]
        )
    base_sha = str(_git(root_path, "rev-parse", "HEAD").stdout or "").strip()
    expected_base = str(workspace.get("base_sha") or "").strip()
    if expected_base and base_sha != expected_base:
        raise DeliveryError(
            "main branch advanced after the v2 run base; rebase/re-run before merge "
            f"(base={expected_base[:12]}, head={base_sha[:12]})"
        )
    if dry_run:
        return {
            "ok": True, "dry_run": True, "run_id": run_id,
            "paths": paths, "base_sha": base_sha,
            "verify_commands": _verify_commands(plan),
        }

    applied = _git(root_path, "apply", "--whitespace=nowarn", str(patch_path), check=False)
    if applied.returncode != 0:
        raise DeliveryError(
            "handoff patch apply failed: "
            + (applied.stderr or applied.stdout or "").strip()[:600]
        )
    verify: list[dict[str, Any]] = []
    try:
        verify = _run_verify(root_path, plan, verify_timeout)
        failed = [item for item in verify if item["exit_code"] != 0]
        if failed:
            raise DeliveryError(
                "merge verify gate failed: "
                + "; ".join(f"{item['command']} -> {item['exit_code']}" for item in failed)
            )
        staged = _git(root_path, "add", "--", *paths, check=False)
        if staged.returncode != 0:
            raise DeliveryError(
                "merge staging failed: "
                + (staged.stderr or staged.stdout or "").strip()[:600]
            )
        commit_message = message or f"v2 merge: {manifest.get('goal') or run_id} ({run_id})"
        committed = _git(root_path, "commit", "-m", commit_message, check=False)
        if committed.returncode != 0:
            raise DeliveryError(
                "merge commit failed: "
                + (committed.stderr or committed.stdout or "").strip()[:600]
            )
        merge_sha = str(_git(root_path, "rev-parse", "HEAD").stdout or "").strip()
    except Exception:
        # Reverse only the patch we just applied. If reverse fails, restore the
        # exact pre-merge commit rather than leaving a half-merged tree.
        reverse = _git(root_path, "apply", "--reverse", str(patch_path), check=False)
        if reverse.returncode != 0:
            _git(root_path, "reset", "--hard", base_sha, check=False)
        else:
            _git(root_path, "reset", "--", *paths, check=False)
        raise

    record = {
        "run_id": run_id,
        "status": "merged",
        "pre_merge_sha": base_sha,
        "merge_sha": merge_sha,
        "paths": paths,
        "verify": verify,
        "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        "merged_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    (store.run_dir / "merge.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    store.append_event("run.merged", record)
    return {"ok": True, **record}


def rollback(root: str | Path, run_id: str, *, state_root: str | Path | None = None,
             message: str | None = None) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve()
    runner = V2Runner(root_path, state_root=state_root)
    store = runner.load(run_id)
    record_path = store.run_dir / "merge.json"
    if not record_path.exists():
        raise ManifestError(f"run has not been merged: {run_id}")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    merge_sha = str(record.get("merge_sha") or "")
    if not merge_sha:
        raise DeliveryError("merge record has no merge_sha")
    _assert_clean(root_path, store.state_root)
    exists = _git(root_path, "cat-file", "-e", f"{merge_sha}^{{commit}}", check=False)
    if exists.returncode != 0:
        raise DeliveryError(f"merge commit not found: {merge_sha}")
    ancestor = _git(root_path, "merge-base", "--is-ancestor", merge_sha, "HEAD", check=False)
    if ancestor.returncode != 0:
        raise DeliveryError("merge commit is not an ancestor of the current branch")
    reverted = _git(root_path, "revert", "--no-edit", merge_sha, check=False)
    if reverted.returncode != 0:
        raise DeliveryError(
            "rollback revert failed: "
            + (reverted.stderr or reverted.stdout or "").strip()[:600]
        )
    rollback_sha = str(_git(root_path, "rev-parse", "HEAD").stdout or "").strip()
    result = {
        "ok": True,
        "run_id": run_id,
        "status": "rolled_back",
        "merge_sha": merge_sha,
        "rollback_sha": rollback_sha,
        "reverted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    (store.run_dir / "rollback.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    store.append_event("run.rolled_back", result)
    return result
