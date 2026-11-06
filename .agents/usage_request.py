#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generic Usage approval request for direct recipe_c runs.

A direct Work Order is not a job plan.  Keep its approval request in a separate
file so it can never overwrite `.agents/tmp/plan.json` or the job token.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Mapping

import approval_policy

REQUEST_FILENAME = "usage_request.json"
TOKEN_FILENAME = "usage_request_token.txt"
APPROVAL_FILENAME = "usage_request_approval.json"


def _tmp(root: str | Path) -> Path:
    return Path(root).resolve() / ".agents" / "tmp"


def request_path(root: str | Path) -> Path:
    return _tmp(root) / REQUEST_FILENAME


def token_path(root: str | Path) -> Path:
    return _tmp(root) / TOKEN_FILENAME


def approval_path(root: str | Path) -> Path:
    return _tmp(root) / APPROVAL_FILENAME


def write_request(
    root: str | Path,
    *,
    plan: Mapping[str, Any],
    mode: str,
    reviewer: str,
    models: list[str],
    decision: approval_policy.UsageDecision,
    label: str = "direct recipe",
) -> dict[str, Any]:
    path = request_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    token_path(root).write_text(token, encoding="utf-8")
    try:
        approval_path(root).unlink()
    except FileNotFoundError:
        pass
    payload = {
        "schema_version": 1,
        "source": "recipe",
        "label": str(label)[:200],
        "created_at": time.time(),
        "mode": str(mode or "panel"),
        "reviewer": str(reviewer or "heuristic"),
        "models": [str(item) for item in models],
        "plan": dict(plan),
        "decision": decision.as_dict(),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return {**payload, "token": token}


def read_request(root: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(request_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def verify_token(root: str | Path, token: str) -> bool:
    try:
        expected = token_path(root).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return bool(expected) and secrets.compare_digest(str(token or ""), expected)


def clear(root: str | Path) -> None:
    for path in (request_path(root), token_path(root), approval_path(root)):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def evaluate_request(root: str | Path, request: Mapping[str, Any] | None = None):
    request = dict(request or read_request(root))
    plan = request.get("plan") or {}
    if not plan:
        raise ValueError("no direct Usage request")
    return approval_policy.evaluate(
        plan,
        mode=str(request.get("mode") or "panel"),
        reviewer=str(request.get("reviewer") or "heuristic"),
        models=[str(item) for item in (request.get("models") or [])],
        root=root,
    )


def status(root: str | Path) -> dict[str, Any]:
    request = read_request(root)
    if not request:
        return {}
    decision = evaluate_request(root, request)
    return {
        "source": "recipe",
        "label": request.get("label"),
        "plan": request.get("plan") or {},
        "mode": request.get("mode"),
        "reviewer": request.get("reviewer"),
        "decision": decision.as_dict(),
        "status": approval_policy.approval_status(approval_path(root), decision),
    }


def approve(root: str | Path, token: str, *, approved_by: str = "dashboard-user") -> dict:
    if not verify_token(root, token):
        raise PermissionError("invalid Usage request token")
    request = read_request(root)
    if not request:
        raise ValueError("no direct Usage request")
    decision = evaluate_request(root, request)
    record = approval_policy.create_approval(
        approval_path(root), decision, approved_by=approved_by,
    )
    return {"ok": True, "status": "approved", "record": record}


def reject(root: str | Path, token: str) -> dict:
    if not verify_token(root, token):
        raise PermissionError("invalid Usage request token")
    try:
        approval_path(root).unlink()
    except FileNotFoundError:
        pass
    return {"ok": True, "status": "rejected"}
