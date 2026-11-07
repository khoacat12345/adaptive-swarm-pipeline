#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic Spec ↔ implementation convergence check (P1-06).

This is deliberately evidence-first:

* source artifact hashes still match the plan;
* changed files stay inside the plan's allowed paths;
* every verify result attached to the run exited 0;
* every work-order target is accounted for.

It does not claim semantic equivalence; it detects machine-visible drift.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60,
    )


def _changed_files(worktree: Path, base_sha: str) -> list[str]:
    result = _git(worktree, "diff", "--name-only", base_sha)
    if result.returncode != 0:
        return []
    # Runtime evidence (backups/tmp/ledger/v2 state) is not implementation
    # drift. It lives under .agents/ and is never part of the product diff.
    return [
        line.strip() for line in result.stdout.splitlines()
        if line.strip() and not line.strip().replace("\\", "/").startswith(".agents/")
    ]


def converge(
    root: str | Path,
    plan: Mapping[str, Any],
    *,
    worktree: str | Path | None = None,
    test_evidence: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    worktree_path = Path(worktree).resolve() if worktree else root_path
    drift: list[str] = []
    evidence: list[dict[str, Any]] = []

    artifacts = plan.get("source_artifacts") or {}
    if isinstance(artifacts, Mapping):
        for name, expected in artifacts.items():
            relative = str((plan.get("source") or {}).get(name) or "")
            if not relative:
                drift.append(f"source artifact has no path: {name}")
                continue
            path = (root_path / relative).resolve()
            if not path.is_file():
                drift.append(f"source artifact missing: {name} ({relative})")
                continue
            actual = _sha256(path)
            evidence.append({"artifact": name, "path": relative,
                             "expected": expected, "actual": actual})
            if actual != expected:
                drift.append(f"source artifact changed: {name} ({relative})")

    allowed: set[str] = set()
    targets: set[str] = set()
    for phase in plan.get("phases") or []:
        for item in (phase.get("allowed_paths") or []):
            allowed.add(str(item).replace("\\", "/").strip("/"))
        for order in (phase.get("work_orders") or []):
            for item in (order.get("target_files") or []):
                targets.add(str(item).replace("\\", "/").strip("/"))
    base = str(plan.get("base_sha") or "")
    changed = _changed_files(worktree_path, base) if base else []
    for relative in changed:
        normalized = relative.replace("\\", "/").strip("/")
        if allowed and not any(
            normalized == root or normalized.startswith(root + "/")
            for root in allowed
        ):
            drift.append(f"changed file outside allowed_paths: {relative}")
    for relative in sorted(targets):
        if changed and relative not in {item.replace("\\", "/").strip("/")
                                        for item in changed}:
            drift.append(f"target not changed: {relative}")

    verifies: list[dict[str, Any]] = []
    for item in test_evidence or []:
        item = dict(item or {})
        code = item.get("exit_code")
        verifies.append({
            "command": item.get("command"),
            "exit_code": code,
            "status": "pass" if code in (0, "0") else "fail",
        })
        if code not in (0, "0"):
            drift.append(f"verify failed: {item.get('command') or '?'} exit={code}")

    return {
        "ok": not drift,
        "drift": drift,
        "changed_files": changed,
        "target_files": sorted(targets),
        "allowed_paths": sorted(allowed),
        "artifacts": evidence,
        "verify_evidence": verifies,
        "plan_hash": plan.get("plan_hash"),
    }


def evidence_from_ledger(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in events or []:
        if event.get("event_type") not in {"safe_writer.verify", "recipe.verify",
                                           "verify.result"}:
            continue
        result.append(dict(event))
    return result
