#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified War Room HTTP API.

Route handlers are plain Python functions so the local HTTP server and tests can
call exactly the same code.  Security rules are enforced here, not in JavaScript:

* the server only binds loopback (see ``dashboard.make_server``);
* every write endpoint requires the per-run sign-off token;
* responses are redacted with the shared scanner;
* handoff diffs are capped by ``unified.state.MAX_DIFF_BYTES``.

Routes::

    GET  /api/unified/runs
    GET  /api/unified/runs/<id>
    GET  /api/unified/runs/<id>/events
    GET  /api/unified/runs/<id>/diff
    POST /api/unified/usage/approve
    POST /api/unified/merge
    POST /api/unified/rollback
    POST /api/unified/control
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from unified import state as unified_state  # noqa: E402
from unified.state import (  # noqa: E402
    MAX_DIFF_BYTES,
    MAX_EVENT_LIMIT,
    redact,
    redact_text,
)
from v2.errors import (  # noqa: E402
    ManifestError,
    PlanChangedError,
    StateTransitionError,
)
from v2.plan import plan_hash  # noqa: E402
from v2.runner import V2Runner  # noqa: E402

CONTROL_ACTIONS = ("pause", "resume", "stop", "accept", "recover")
GET_ROUTES = (
    "/api/unified/runs",
    "/api/unified/runs/<id>",
    "/api/unified/runs/<id>/events",
    "/api/unified/runs/<id>/diff",
)
POST_ROUTES = (
    "/api/unified/usage/approve",
    "/api/unified/merge",
    "/api/unified/rollback",
    "/api/unified/control",
)


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------
def list_runs(root: str | Path, *, state_root: str | Path | None = None,
              include_v1: bool = True, query: str | None = None,
              limit: int | None = None) -> dict[str, Any]:
    runs = unified_state.list_runs(
        root, state_root=state_root, include_v1=include_v1,
        query=query, limit=limit,
    )
    return redact({
        "runs": runs,
        "summary": unified_state.summary(
            root, state_root=state_root, include_v1=include_v1,
        ),
        "cap_diff_bytes": MAX_DIFF_BYTES,
    })


def _runner(root: str | Path, state_root: str | Path | None = None) -> V2Runner:
    return V2Runner(root, state_root=state_root, require_clean=False)


def _capabilities(root: str | Path, run_id: str,
                  state_root: str | Path | None = None) -> dict[str, bool]:
    try:
        manifest = _runner(root, state_root).load(run_id).load()
    except Exception:  # noqa: BLE001
        return {"pause": False, "resume": False, "stop": False, "accept": False,
                "usage_approve": False, "merge": False, "rollback": False}
    merged = (Path(state_root).expanduser().resolve()
              if state_root else unified_state.state_root_for(root))
    merge_record = merged / "runs" / run_id / "merge.json"
    manifest = dict(manifest)
    manifest["_merged"] = merge_record.exists()
    return unified_state.capabilities(manifest)


def run_detail(root: str | Path, run_id: str, *,
               state_root: str | Path | None = None,
               include_v1: bool = True) -> dict[str, Any]:
    detail = unified_state.read_run(
        root, run_id, state_root=state_root, include_v1=include_v1,
    )
    detail["can_control"] = _capabilities(root, run_id, state_root=state_root)
    detail["cap_diff_bytes"] = MAX_DIFF_BYTES
    return redact(detail)


def run_events(root: str | Path, run_id: str, *,
               state_root: str | Path | None = None,
               cursor: int | str | None = 0,
               limit: int = unified_state.DEFAULT_EVENT_LIMIT,
               event_type: str | None = None) -> dict[str, Any]:
    return redact(unified_state.read_events(
        root, run_id, state_root=state_root, cursor=cursor,
        limit=min(MAX_EVENT_LIMIT, max(1, int(limit or 1))),
        event_type=event_type,
    ))


def run_diff(root: str | Path, run_id: str, *,
             state_root: str | Path | None = None,
             max_bytes: int | None = None) -> dict[str, Any]:
    return redact(unified_state.run_diff(
        root, run_id, state_root=state_root, max_bytes=max_bytes,
    ))


# ---------------------------------------------------------------------------
# Write endpoints
# ---------------------------------------------------------------------------
def _assert_token(root: str | Path, run_id: str, token: str,
                  state_root: str | Path | None = None) -> None:
    if not _runner(root, state_root).verify_signoff_token(run_id, token):
        raise PermissionError("invalid run token")


def _locked(root: str | Path):
    """Best-effort repository lock for an explicit delivery action."""
    import run_lock

    return run_lock.RepoLock(root, owner="dashboard").acquire()


def approve_usage(root: str | Path, run_id: str, token: str = "",
                  *, state_root: str | Path | None = None,
                  approved_by: str = "dashboard-user") -> dict[str, Any]:
    """Approve the exact plan + estimate already on disk.

    The browser never supplies a plan body: the hash is recomputed from the
    persisted plan, so an approval cannot be moved to a different plan.
    """
    runner = _runner(root, state_root)
    _assert_token(root, run_id, token, state_root)
    store = runner.load(run_id)
    store.assert_state("AWAITING_SIGNOFF")
    plan_path = store.run_dir / "plan.json"
    if not plan_path.exists():
        raise StateTransitionError("run has no plan")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    expected = plan_hash(plan)
    manifest = store.load()
    if manifest.get("plan_hash") and str(manifest.get("plan_hash")) != expected:
        raise PlanChangedError("plan file changed after submission")
    result = runner.signoff(run_id, approved_by=str(approved_by or "dashboard-user"))
    return redact({"ok": True, **result})


def merge(root: str | Path, run_id: str, token: str = "",
          *, state_root: str | Path | None = None,
          message: str | None = None, dry_run: bool = False,
          verify_timeout: int = 300) -> dict[str, Any]:
    """Apply a HANDOFF_READY patch.  Explicit action, never automatic."""
    _assert_token(root, run_id, token, state_root)
    from v2.delivery import merge as merge_handoff

    lock = None
    if not dry_run:
        lock = _locked(root)
    try:
        result = merge_handoff(
            root, run_id, state_root=state_root, message=message,
            verify_timeout=max(1, int(verify_timeout or 300)), dry_run=bool(dry_run),
        )
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:  # noqa: BLE001
                pass
    return redact({"ok": True, **result})


def rollback(root: str | Path, run_id: str, token: str = "",
             *, state_root: str | Path | None = None,
             message: str | None = None) -> dict[str, Any]:
    _assert_token(root, run_id, token, state_root)
    from v2.delivery import rollback as rollback_merge

    lock = _locked(root)
    try:
        result = rollback_merge(
            root, run_id, state_root=state_root, message=message,
        )
    finally:
        try:
            lock.release()
        except Exception:  # noqa: BLE001
            pass
    return redact({"ok": True, **result})


def control(root: str | Path, run_id: str, token: str = "", action: str = "",
            *, state_root: str | Path | None = None) -> dict[str, Any]:
    runner = _runner(root, state_root)
    _assert_token(root, run_id, token, state_root)
    action = str(action or "").strip().lower()
    if action not in CONTROL_ACTIONS:
        raise StateTransitionError(f"unknown control action: {action!r}")
    if action == "pause":
        manifest = runner.pause(run_id)
    elif action == "resume":
        manifest = runner.resume(run_id)
    elif action == "stop":
        manifest = runner.stop(run_id)
    elif action == "accept":
        manifest = runner.accept_handoff(run_id)
    else:  # recover
        result = runner.recover(run_id)
        return redact({"ok": True, "action": action, **result})
    return redact({
        "ok": True,
        "action": action,
        "status": manifest.get("status"),
        "run_id": run_id,
        "next": unified_state.capabilities(manifest),
    })


# ---------------------------------------------------------------------------
# Dispatchers used by dashboard.py
# ---------------------------------------------------------------------------
def _flag(query: Mapping[str, Any], name: str, default: bool = True) -> bool:
    raw = query.get(name)
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _one(query: Mapping[str, Any], name: str, default: str = "") -> str:
    raw = query.get(name)
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else default
    return str(raw if raw is not None else default)


def handle_get(root: str | Path, route: str,
               query: Mapping[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """Dispatch one unified GET route.  Returns ``(status, json_payload)``."""
    query = query or {}
    route = str(route or "").rstrip("/") or "/"
    try:
        if route == "/api/unified/runs":
            limit_raw = _one(query, "limit", "")
            limit = int(limit_raw) if limit_raw else None
            payload = list_runs(
                root,
                state_root=_one(query, "state_root") or None,
                include_v1=_flag(query, "include_v1", True),
                query=_one(query, "q") or _one(query, "query") or None,
                limit=limit,
            )
            return 200, payload
        if route == "/api/unified/summary":
            return 200, redact(unified_state.summary(
                root,
                state_root=_one(query, "state_root") or None,
                include_v1=_flag(query, "include_v1", True),
            ))
        prefix = "/api/unified/runs/"
        if route.startswith(prefix):
            rest = route[len(prefix):].strip("/")
            if not rest:
                return 404, {"error": "not found"}
            parts = rest.split("/")
            run_id = parts[0]
            suffix = parts[1] if len(parts) > 1 else ""
            state_root = _one(query, "state_root") or None
            if not suffix:
                return 200, run_detail(
                    root, run_id, state_root=state_root,
                    include_v1=_flag(query, "include_v1", True),
                )
            if suffix == "events":
                limit_raw = _one(query, "limit", str(unified_state.DEFAULT_EVENT_LIMIT))
                payload = run_events(
                    root, run_id, state_root=state_root,
                    cursor=_one(query, "cursor", "0"),
                    limit=int(limit_raw or unified_state.DEFAULT_EVENT_LIMIT),
                    event_type=_one(query, "event_type") or None,
                )
                return 200, payload
            if suffix == "diff":
                max_raw = _one(query, "max_bytes", "")
                max_bytes = int(max_raw) if max_raw else None
                return 200, run_diff(
                    root, run_id, state_root=state_root, max_bytes=max_bytes,
                )
            if suffix == "blackboard":
                return 200, redact(unified_state.blackboard(
                    root, run_id, state_root=state_root,
                ))
        return 404, {"error": "not found"}
    except FileNotFoundError as exc:
        return 404, {"error": redact_text(exc)}
    except PermissionError as exc:
        return 403, {"error": redact_text(exc)}
    except ValueError as exc:
        return 400, {"error": redact_text(exc)}
    except Exception as exc:  # noqa: BLE001
        return 500, {"error": redact_text(f"{type(exc).__name__}: {exc}", limit=300)}


def handle_post(root: str | Path, route: str,
                payload: Mapping[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """Dispatch one unified POST route.  Returns ``(status, json_payload)``."""
    payload = payload or {}
    route = str(route or "").rstrip("/") or "/"
    token = str(payload.get("token") or "")
    run_id = str(payload.get("run_id") or "")
    state_root = str(payload.get("state_root") or "") or None
    try:
        if route == "/api/unified/usage/approve":
            return 200, approve_usage(
                root, run_id, token, state_root=state_root,
                approved_by=str(payload.get("approved_by") or "dashboard-user"),
            )
        if route == "/api/unified/merge":
            return 200, merge(
                root, run_id, token, state_root=state_root,
                message=payload.get("message") or None,
                dry_run=bool(payload.get("dry_run")),
                verify_timeout=int(payload.get("verify_timeout") or 300),
            )
        if route == "/api/unified/rollback":
            return 200, rollback(
                root, run_id, token, state_root=state_root,
                message=payload.get("message") or None,
            )
        if route == "/api/unified/control":
            return 200, control(
                root, run_id, token,
                str(payload.get("action") or ""),
                state_root=state_root,
            )
        return 404, {"error": "not found"}
    except PermissionError as exc:
        return 403, {"error": redact_text(exc)}
    except (PlanChangedError, StateTransitionError) as exc:
        return 409, {"error": redact_text(exc)}
    except ManifestError as exc:
        return 404, {"error": redact_text(exc)}
    except ValueError as exc:
        return 400, {"error": redact_text(exc)}
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        status = 409 if name in {"LockBusy", "DeliveryError", "StateTransitionError"} else 500
        return status, {"error": redact_text(f"{name}: {exc}", limit=300)}


# Convenience aliases used by callers/tests.
approve = approve_usage
merge_run = merge
rollback_run = rollback
