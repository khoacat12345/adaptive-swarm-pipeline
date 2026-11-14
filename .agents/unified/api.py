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
    GET  /api/unified/runs/<id>/report        (JSON, ?format=md for Markdown)
    GET  /api/unified/runs/<id>/inbox
    POST /api/unified/usage/approve
    POST /api/unified/merge
    POST /api/unified/rollback
    POST /api/unified/control
    POST /api/unified/inbox/ask
    POST /api/unified/inbox/reply
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
from unified.inbox import InboxError  # noqa: E402
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
    "/api/unified/runs/<id>/report",
    "/api/unified/runs/<id>/report.md",
    "/api/unified/runs/<id>/inbox",
)
POST_ROUTES = (
    "/api/unified/usage/approve",
    "/api/unified/merge",
    "/api/unified/rollback",
    "/api/unified/control",
    "/api/unified/inbox/ask",
    "/api/unified/inbox/reply",
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
    inbox = detail.get("inbox") if isinstance(detail.get("inbox"), Mapping) else {}
    detail["needs_attention"] = bool(inbox.get("needs_attention")) or str(
        detail.get("status") or ""
    ) in {"BLOCKED", "AWAITING_USER_CONFIRMATION", "PAUSED"}
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


def run_report(root: str | Path, run_id: str, *,
               state_root: str | Path | None = None,
               include_v1: bool = False) -> dict[str, Any]:
    """F11: full completion report as JSON."""
    from unified import report as unified_report

    return unified_report.build_report(
        root, run_id, state_root=state_root, include_v1=include_v1,
    )


def run_report_markdown(root: str | Path, run_id: str, *,
                        state_root: str | Path | None = None,
                        include_v1: bool = False) -> dict[str, Any]:
    """F11: Markdown rendering for the ``Download Markdown`` button."""
    from unified import report as unified_report

    payload = unified_report.build_report(
        root, run_id, state_root=state_root, include_v1=include_v1,
    )
    return {
        "run_id": payload.get("run_id") or str(run_id),
        "filename": f"run-report-{run_id}.md",
        "markdown": unified_report.render_markdown(payload),
        "status": payload.get("status"),
        "available": True,
    }


def run_inbox(root: str | Path, run_id: str, *,
              state_root: str | Path | None = None) -> dict[str, Any]:
    """F03: pending decisions for one run."""
    from unified.inbox import inbox_summary

    return redact(inbox_summary(root, run_id, state_root=state_root))


def ask_decision(root: str | Path, run_id: str, question: str, *,
                 state_root: str | Path | None = None,
                 gate: str | None = None,
                 blocking: bool = True,
                 task_id: str | None = None,
                 author: str = "worker") -> dict[str, Any]:
    """Record a worker question; a blocking question pauses the run."""
    from unified.inbox import inbox_for_run

    inbox = inbox_for_run(root, run_id, state_root=state_root)
    message = inbox.ask(
        question, blocking=blocking, gate=gate, task_id=task_id, author=author,
    )
    _append_decision_event(root, run_id, state_root, message)
    paused = _pause_for_blocking(root, run_id, state_root, message)
    if message.blocks_run:
        _notify(root, "DECISION_PENDING", {
            "run_id": run_id,
            "status": "AWAITING_DECISION",
            "reason": f"{message.id} ({message.gate or message.kind}): "
                      f"{message.question[:160]}",
        })
    return redact({"ok": True, "message": message.as_dict(), "paused": paused})


def reply_decision(root: str | Path, message_id: str, answer: str, *,
                   run_id: str | None = None,
                   state_root: str | Path | None = None,
                   answered_by: str = "dashboard-user") -> dict[str, Any]:
    """Answer a message and resume the run once no blocking gate remains."""
    from unified.inbox import find_message, inbox_for_run

    if run_id:
        inbox = inbox_for_run(root, run_id, state_root=state_root)
        message = inbox.get(message_id)
    else:
        inbox, message = find_message(root, message_id, state_root=state_root)
    answered = inbox.reply(message_id, answer, answered_by=answered_by)
    _append_decision_event(root, answered.run_id, state_root, answered)
    resumed = _resume_if_unblocked(root, answered.run_id, state_root, inbox)
    _notify(root, "DECISION_ANSWERED", {
        "run_id": answered.run_id,
        "status": "ANSWERED",
        "reason": f"{answered.id} answered by {answered.answered_by}",
    })
    return redact({
        "ok": True,
        "message": answered.as_dict(),
        "resumed": resumed,
    })


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


# ---------------------------------------------------------------------------
# F03 decision inbox helpers (shared by CLI + API)
# ---------------------------------------------------------------------------
def _notify(root: str | Path, event: str, payload: Mapping[str, Any]) -> None:
    """One-way operator notification; never raises and never blocks the call."""
    try:
        from v2.alerts import notify_event

        notify_event(root, event, dict(payload))
    except Exception:  # noqa: BLE001 - notifications are strictly optional
        pass


def _append_decision_event(root: str | Path, run_id: str,
                           state_root: str | Path | None,
                           message: Any) -> None:
    try:
        store = _runner(root, state_root).load(str(run_id))
        if message.status == "answered":
            suffix = "answer"
        elif message.status == "cancelled":
            suffix = "cancelled"
        else:
            suffix = message.kind
        store.append_event(f"decision.{suffix}", {
            "message_id": message.id,
            "question": message.question,
            "status": message.status,
            "blocking": message.blocking,
            "gate": message.gate,
            "answer": message.answer,
            "answered_by": message.answered_by,
            "task_id": message.task_id,
        })
    except Exception:  # noqa: BLE001 - the inbox file is authoritative
        pass


def _pause_for_blocking(root: str | Path, run_id: str,
                        state_root: str | Path | None,
                        message: Any) -> bool:
    if not getattr(message, "blocks_run", False):
        return False
    try:
        runner = _runner(root, state_root)
        status = str(runner.load(str(run_id)).load().get("status") or "")
        if status in {"PROVISIONING", "PHASE_EXECUTING", "PHASE_VERIFYING",
                      "PLANNING"}:
            runner.pause(str(run_id), reason=f"blocking decision {message.id}")
            return True
    except Exception:  # noqa: BLE001 - pausing is best effort
        pass
    return False


def _resume_if_unblocked(root: str | Path, run_id: str,
                         state_root: str | Path | None,
                         inbox: Any) -> bool:
    try:
        if inbox.needs_attention():
            return False
        runner = _runner(root, state_root)
        status = str(runner.load(str(run_id)).load().get("status") or "")
        if status == "PAUSED":
            runner.resume(str(run_id))
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


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
            if suffix in {"report", "report.md"}:
                if suffix == "report.md" or _one(query, "format").lower() in {"md", "markdown"}:
                    return 200, run_report_markdown(
                        root, run_id, state_root=state_root,
                        include_v1=_flag(query, "include_v1", False),
                    )
                return 200, run_report(
                    root, run_id, state_root=state_root,
                    include_v1=_flag(query, "include_v1", False),
                )
            if suffix == "inbox":
                return 200, run_inbox(root, run_id, state_root=state_root)
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
        if route == "/api/unified/inbox/reply":
            target_run = str(payload.get("run_id") or "")
            if target_run:
                _assert_token(root, target_run, token, state_root)
            return 200, reply_decision(
                root, str(payload.get("message_id") or payload.get("id") or ""),
                str(payload.get("answer") or ""),
                run_id=str(payload.get("run_id") or "") or None,
                state_root=state_root,
                answered_by=str(payload.get("answered_by") or "dashboard-user"),
            )
        if route == "/api/unified/inbox/ask":
            return 200, ask_decision(
                root, run_id,
                str(payload.get("question") or ""),
                state_root=state_root,
                gate=payload.get("gate") or None,
                blocking=bool(payload.get("blocking", True)),
                task_id=payload.get("task_id") or None,
                author=str(payload.get("author") or "worker"),
            )
        return 404, {"error": "not found"}
    except PermissionError as exc:
        return 403, {"error": redact_text(exc)}
    except (PlanChangedError, StateTransitionError) as exc:
        return 409, {"error": redact_text(exc)}
    except InboxError as exc:
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
