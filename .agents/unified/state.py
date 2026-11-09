#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified, read-only state projection for the War Room.

Primary source: v2 per-run state (``.agents/tmp/v2/runs/<run_id>``).
Optional source: the v1 append-only ledger (``.agents/ledger.jsonl``) with a
strictly read-only adapter.  This module never writes either source; the
dashboard calls the writers in ``v2``/``unified.api`` explicitly.

The projection normalizes the fields one operator (and one browser page) needs:

    status / stage / timeline
    tier + planner
    budget / spend / tokens
    verify commands and last result
    handoff diff / merge / rollback
    Usage approval
    warnings

Everything returned by this module is JSON-safe and has been passed through the
same secret scanner the rest of the dashboard uses.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2.state import resolve_state_root  # noqa: E402

try:  # The scanner lives in .agents and is shared with v1.
    import secret_scan as _secret_scan
except Exception:  # pragma: no cover - the scanner is part of the repo
    _secret_scan = None


SCHEMA_VERSION = 1
STATE_ROOT_REL = Path(".agents") / "tmp" / "v2"
V1_LEDGER_REL = Path(".agents") / "ledger.jsonl"

# Hard cap for any diff returned to a browser.  The dashboard and the API both
# import this constant so a future change cannot silently drift.
MAX_DIFF_BYTES = 400_000
MAX_EVENT_BYTES = 4_000
MAX_EVENT_LIMIT = 200
MAX_RUNS = 500
DEFAULT_EVENT_LIMIT = 50

STAGE_ORDER = ("planning", "worktree", "executing", "verifying", "handoff")
STAGE_LABELS = {
    "planning": "Planning",
    "worktree": "Worktree",
    "executing": "Executing",
    "verifying": "Verifying",
    "handoff": "Handoff",
}

# Status -> (stage, tone).  Unknown statuses fall back to "planning"/"info".
STATUS_STAGE = {
    "CREATED": ("planning", "info"),
    "PRECHECKED": ("planning", "info"),
    "PLANNING": ("planning", "active"),
    "AWAITING_SIGNOFF": ("planning", "warn"),
    "AWAITING_USAGE": ("planning", "warn"),
    "PROVISIONING": ("worktree", "active"),
    "PHASE_EXECUTING": ("executing", "active"),
    "PHASE_VERIFYING": ("verifying", "active"),
    "PHASE_COMMITTED": ("verifying", "ok"),
    "HANDOFF_READY": ("handoff", "ok"),
    "DONE": ("handoff", "ok"),
    "PAUSED": ("executing", "warn"),
    "BLOCKED": ("executing", "bad"),
    "RATE_LIMITED": ("executing", "warn"),
    "AWAITING_USER_CONFIRMATION": ("planning", "warn"),
    "USER_CONFIRMED": ("planning", "ok"),
    "USER_REJECTED": ("planning", "bad"),
    "STOPPED": ("handoff", "bad"),
    "FAILED": ("handoff", "bad"),
    "QUARANTINED": ("handoff", "bad"),
}

TERMINAL_STATUSES = {"DONE", "STOPPED", "FAILED", "QUARANTINED"}
ACTIVE_STATUSES = {
    "CREATED", "PRECHECKED", "PLANNING", "AWAITING_SIGNOFF", "AWAITING_USAGE",
    "PROVISIONING", "PHASE_EXECUTING", "PHASE_VERIFYING", "PHASE_COMMITTED",
    "HANDOFF_READY", "PAUSED", "BLOCKED", "RATE_LIMITED",
    "AWAITING_USER_CONFIRMATION", "USER_CONFIRMED", "USER_REJECTED",
}

# Order of fields that may carry a credential.  Redaction is applied to every
# projected string, not just to known event payload keys.
_SECRET_KEY_HINTS = (
    "token", "secret", "password", "passwd", "api_key", "apikey", "authorization",
    "private_key", "access_key", "refresh_token", "signoff_token", "usage_token",
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def state_root_for(root: str | Path, state_root: str | Path | None = None) -> Path:
    return resolve_state_root(root, state_root)


def v1_ledger_for(root: str | Path) -> Path:
    root_path = Path(root).expanduser().resolve()
    base = root_path if root_path.name == ".agents" else root_path / ".agents"
    return base / "ledger.jsonl"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _iso_to_epoch(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
def _redact_string(value: str) -> str:
    text = str(value)
    if _secret_scan is not None:
        try:
            text = _secret_scan.scan_text(text)[0]
        except Exception:  # pragma: no cover - scanner must never break the UI
            pass
    lowered = text.lower()
    if any(hint in lowered for hint in _SECRET_KEY_HINTS) and not any(
        marker in text for marker in ("[REDACTED", "Bearer <", "****")
    ):
        # Scan known key=value shapes that the generic scanner may miss.
        import re

        text = re.sub(
            r"(?i)(\b(?:signoff_token|usage_token|access_token|refresh_token|"
            r"api[_-]?key|authorization|password|secret)\b\s*[:=]\s*)"
            r"(\"[^\"]*\"|'[^']*'|[^\s,;&]+)",
            r"\1[REDACTED_SECRET]",
            text,
        )
    return text


def redact(value: Any, *, depth: int = 0, max_string: int = 20_000) -> Any:
    """Recursively redact secrets while keeping JSON-safe primitives."""
    if depth > 8:
        return "[truncated]"
    if isinstance(value, str):
        return _redact_string(value)[:max_string]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 80:
                break
            name = str(key)
            lowered = name.lower()
            if any(hint in lowered for hint in _SECRET_KEY_HINTS) and isinstance(
                item, (str, int, float, bool)
            ):
                # Keep boolean/None states meaningful, hide every literal value.
                if isinstance(item, bool) or item is None:
                    result[name] = item
                elif name.lower().endswith("_hash"):
                    result[name] = item
                else:
                    result[name] = "[REDACTED_SECRET]"
                continue
            result[name] = redact(item, depth=depth + 1, max_string=max_string)
        return result
    if isinstance(value, (list, tuple, set)):
        return [redact(item, depth=depth + 1, max_string=max_string)
                for item in list(value)[:80]]
    return _redact_string(str(value))[:max_string]


def redact_text(value: Any, *, limit: int | None = None) -> str:
    text = _redact_string("" if value is None else str(value))
    if limit is not None:
        return text[: max(0, int(limit))]
    return text


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def _event_index(events: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        name = str(event.get("event_type") or "")
        indexed.setdefault(name, []).append(dict(event))
    return indexed


def _first_event(events: list[dict[str, Any]], *names: str) -> dict[str, Any]:
    for event in events:
        if str(event.get("event_type")) in names:
            return event
    return {}


def _last_event(events: list[dict[str, Any]], *names: str) -> dict[str, Any]:
    for event in reversed(events):
        if str(event.get("event_type")) in names:
            return event
    return {}


def _plan_tier(plan: Mapping[str, Any], manifest: Mapping[str, Any],
               events: list[dict[str, Any]]) -> str | None:
    for candidate in (
        plan.get("execution_tier"),
        plan.get("tier"),
        (plan.get("_meta") or {}).get("tier") if isinstance(plan.get("_meta"), Mapping) else None,
        (manifest.get("plan_decision") or {}).get("tier")
        if isinstance(manifest.get("plan_decision"), Mapping) else None,
    ):
        if candidate:
            value = str(candidate).strip().upper()
            if value in {"E1", "E2", "E3", "E4"}:
                return value
    selected = _last_event(events, "tier.selected")
    data = selected.get("data") if isinstance(selected.get("data"), Mapping) else {}
    value = str(data.get("tier") or data.get("signed_tier") or "").strip().upper()
    if value in {"E1", "E2", "E3", "E4"}:
        return value
    return "E3" if plan else None


def _plan_planner(plan: Mapping[str, Any], manifest: Mapping[str, Any],
                  events: list[dict[str, Any]], tier: str | None) -> str | None:
    meta = plan.get("_meta") if isinstance(plan.get("_meta"), Mapping) else {}
    for candidate in (
        plan.get("planner"),
        plan.get("planner_level"),
        meta.get("planner"),
        meta.get("planner_level"),
        (manifest.get("plan_decision") or {}).get("planner")
        if isinstance(manifest.get("plan_decision"), Mapping) else None,
    ):
        if candidate:
            value = str(candidate).strip().upper()
            if value in {"P1", "P2", "P3"}:
                return value
    submitted = _last_event(events, "plan.submitted")
    data = submitted.get("data") if isinstance(submitted.get("data"), Mapping) else {}
    value = str(data.get("planner") or "").strip().upper()
    if value in {"P1", "P2", "P3"}:
        return value
    # The tier policy is the source of truth when the plan omits the badge.
    mapping = {"E1": "P1", "E2": "P1", "E3": "P2", "E4": "P3"}
    return mapping.get(str(tier or "").upper())


def _plan_commands(plan: Mapping[str, Any]) -> list[str]:
    commands: list[str] = []
    for phase in plan.get("phases") or []:
        if not isinstance(phase, Mapping):
            continue
        for order in phase.get("work_orders") or []:
            if not isinstance(order, Mapping):
                continue
            command = str(order.get("verify_command") or "").strip()
            if command and command not in commands:
                commands.append(command)
    return commands


def _verify_summary(plan: Mapping[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    commands = _plan_commands(plan)
    results: list[dict[str, Any]] = []
    for event in events:
        name = str(event.get("event_type") or "")
        if "verify" not in name:
            continue
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        exit_code = data.get("exit_code", data.get("verify_exit"))
        if exit_code is None and isinstance(data.get("result"), Mapping):
            exit_code = (data.get("result") or {}).get("exit_code")
        entry = {
            "event_type": name,
            "timestamp": event.get("timestamp"),
            "command": data.get("command") or data.get("verify_command"),
            "exit_code": exit_code,
            "status": event.get("status") or data.get("status"),
        }
        results.append(entry)
    last = next((item for item in reversed(results) if item.get("exit_code") is not None), None)
    ok: bool | None = None
    if last is not None:
        ok = _int(last.get("exit_code"), 1) == 0
    return {
        "commands": commands,
        "command": commands[0] if commands else None,
        "ok": ok,
        "last_exit": _int(last.get("exit_code"), 1) if last is not None else None,
        "last_at": (last or {}).get("timestamp"),
        "results": results[-20:],
    }


def _spend(plan: Mapping[str, Any], budget: Mapping[str, Any]) -> dict[str, Any]:
    global_budget = budget.get("global") if isinstance(budget.get("global"), Mapping) else {}
    config_global = (budget.get("config") or {}).get("global") \
        if isinstance(budget.get("config"), Mapping) else {}
    config_global = config_global if isinstance(config_global, Mapping) else {}
    phase_budgets = budget.get("phases") if isinstance(budget.get("phases"), Mapping) else {}

    used_requests = _int(global_budget.get("used_requests"))
    used_cost = _float(global_budget.get("used_cost_usd"))
    reserved_requests = _int(global_budget.get("reserved_requests"))
    reserved_cost = _float(global_budget.get("reserved_cost_usd"))
    used_tokens = _int(global_budget.get("used_tokens"))
    max_requests = _int(config_global.get("max_requests"))
    max_cost = _float(config_global.get("max_cost_usd"))

    estimated_requests = sum(
        _int(((phase.get("budget") or {}).get("requests")) if isinstance(phase, Mapping) else 0)
        for phase in (plan.get("phases") or [])
        if isinstance(phase, Mapping)
    )
    estimated_cost = round(sum(
        _float(((phase.get("budget") or {}).get("cost_usd")) if isinstance(phase, Mapping) else 0)
        for phase in (plan.get("phases") or [])
        if isinstance(phase, Mapping)
    ), 8)
    estimated_tokens = 0
    for phase in plan.get("phases") or []:
        if not isinstance(phase, Mapping):
            continue
        phase_budget = phase.get("budget") if isinstance(phase.get("budget"), Mapping) else {}
        estimated_tokens += _int(phase_budget.get("tokens") or phase_budget.get("token_budget"))
    if not estimated_tokens and plan:
        # Same read-only heuristic as the v2 web page; actuals stay authoritative.
        estimated_tokens = max(1, len(json.dumps(plan, ensure_ascii=False)) // 4)

    phases: dict[str, dict[str, Any]] = {}
    for phase_id, phase in phase_budgets.items():
        if not isinstance(phase, Mapping):
            continue
        phases[str(phase_id)] = {
            "used_requests": _int(phase.get("used_requests")),
            "used_cost_usd": round(_float(phase.get("used_cost_usd")), 8),
            "used_tokens": _int(phase.get("used_tokens")),
        }

    return {
        "estimated_requests": estimated_requests,
        "estimated_cost_usd": estimated_cost,
        "estimated_tokens": estimated_tokens,
        "used_requests": used_requests,
        "used_cost_usd": round(used_cost, 8),
        "used_tokens": used_tokens,
        "reserved_requests": reserved_requests,
        "reserved_cost_usd": round(reserved_cost, 8),
        "remaining_requests": max(0, max_requests - used_requests - reserved_requests)
        if max_requests else max(0, estimated_requests - used_requests),
        "remaining_cost_usd": round(max(0.0, max_cost - used_cost - reserved_cost), 8)
        if max_cost else round(max(0.0, estimated_cost - used_cost), 8),
        "remaining_tokens": max(0, estimated_tokens - used_tokens),
        "max_requests": max_requests,
        "max_cost_usd": max_cost,
        "phases": phases,
    }


def _timeline(status: str, stage: str, *, created_at: Any, updated_at: Any,
              handoff_ready: bool) -> list[dict[str, Any]]:
    current = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else 0
    rows: list[dict[str, Any]] = []
    for index, name in enumerate(STAGE_ORDER):
        if name == "handoff" and handoff_ready and status in TERMINAL_STATUSES | {"HANDOFF_READY"}:
            state = "done"
        elif index < current:
            state = "done"
        elif index == current:
            state = "done" if status in {"DONE", "HANDOFF_READY", "PHASE_COMMITTED"} else "active"
        else:
            state = "pending"
        rows.append({
            "id": name,
            "label": STAGE_LABELS[name],
            "state": state,
            "at": created_at if index == 0 else (updated_at if state != "pending" else None),
        })
    return rows


def _usage(root: Path, run_dir: Path, plan: Mapping[str, Any],
           manifest: Mapping[str, Any]) -> dict[str, Any]:
    record = _read_json(run_dir / "usage_approval.json")
    signoff = _read_json(run_dir / "signoff.json")
    estimate = record.get("estimate") if isinstance(record.get("estimate"), Mapping) else {}
    status = "waiting"
    error = None
    if record:
        expires = _float(record.get("expires_at"))
        now = _iso_to_epoch(_utc_now())
        if record.get("plan_hash") and manifest.get("plan_hash") and \
                str(record.get("plan_hash")) != str(manifest.get("plan_hash")):
            status = "invalid"
            error = "approval belongs to a different plan"
        elif expires and now > expires:
            status = "expired"
            error = "approval expired"
        else:
            status = "approved"
    return {
        "status": status,
        "required": bool(plan),
        "approved_by": record.get("approved_by"),
        "approved_at": record.get("approved_at"),
        "expires_at": record.get("expires_at"),
        "plan_hash": record.get("plan_hash"),
        "estimate_hash": record.get("estimate_hash"),
        "estimate": redact(estimate),
        "reasons": redact(record.get("reasons") or []),
        "signoff": redact({
            "signed": bool(signoff),
            "plan_hash": signoff.get("plan_hash"),
            "approved_by": signoff.get("approved_by"),
            "created_at": signoff.get("created_at") or signoff.get("signed_at"),
            "auto_merge": signoff.get("auto_merge"),
        }),
        "error": error,
    }


def _handoff(root: Path, run_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    handoff_dir = run_dir / "handoff"
    patch = handoff_dir / "handoff.patch"
    report = handoff_dir / "report.md"
    merge = _read_json(run_dir / "merge.json")
    rollback = _read_json(run_dir / "rollback.json")
    size = 0
    if patch.exists():
        try:
            size = patch.stat().st_size
        except OSError:
            size = 0
    return {
        "ready": patch.exists(),
        "patch": str(patch) if patch.exists() else None,
        "report": str(report) if report.exists() else None,
        "patch_bytes": size,
        "report_text": redact_text(report.read_text(encoding="utf-8", errors="replace"),
                                   limit=MAX_EVENT_BYTES) if report.exists() else "",
        "merged": bool(merge),
        "merge": redact({
            "status": merge.get("status"),
            "pre_merge_sha": merge.get("pre_merge_sha"),
            "merge_sha": merge.get("merge_sha"),
            "paths": merge.get("paths") or [],
            "merged_at": merge.get("merged_at"),
            "verify": merge.get("verify") or [],
        }) if merge else {},
        "rolled_back": bool(rollback),
        "rollback": redact(rollback) if rollback else {},
        "status": manifest.get("status"),
    }


def _warnings(*, manifest: Mapping[str, Any], plan: Mapping[str, Any],
              spend: Mapping[str, Any], verify: Mapping[str, Any],
              handoff: Mapping[str, Any], usage: Mapping[str, Any],
              plan_hash: str | None) -> list[str]:
    warnings: list[str] = []
    status = str(manifest.get("status") or "")
    if not plan:
        warnings.append("run has no plan on disk")
    if plan and plan_hash and manifest.get("plan_hash") and \
            str(plan_hash) != str(manifest.get("plan_hash")):
        warnings.append("plan hash differs from the submitted manifest")
    if plan and not verify.get("commands"):
        warnings.append("plan has no verify_command")
    if status in {"HANDOFF_READY", "DONE"} and not handoff.get("ready"):
        warnings.append("handoff patch is missing")
    if usage.get("status") in {"invalid", "expired"}:
        warnings.append(f"usage approval {usage.get('status')}")
    if spend.get("used_cost_usd", 0) > spend.get("max_cost_usd", 0) > 0:
        warnings.append("spend exceeds the approved budget")
    if spend.get("used_requests", 0) > spend.get("max_requests", 0) > 0:
        warnings.append("request count exceeds the approved budget")
    workspace = manifest.get("workspace") if isinstance(manifest.get("workspace"), Mapping) else {}
    worktree = workspace.get("worktree_path") or workspace.get("path")
    if worktree and not Path(str(worktree)).exists():
        warnings.append("worktree is missing on disk")
    if manifest.get("last_error"):
        warnings.append(redact_text(manifest.get("last_error"), limit=300))
    if manifest.get("stop_reason") and manifest.get("stop_reason") not in (
            None, "", "user requested stop"):
        warnings.append(redact_text(manifest.get("stop_reason"), limit=300))
    return warnings


def _project_manifest(root: Path, run_dir: Path, manifest: Mapping[str, Any],
                      *, events: list[dict[str, Any]] | None = None,
                      include_plan: bool = False) -> dict[str, Any]:
    events = list(events if events is not None else _read_jsonl(run_dir / "events.ndjson"))
    plan = _read_json(run_dir / "plan.json")
    budget = _read_json(run_dir / "budget.json")
    usage_path = run_dir / "usage_approval.json"

    status = str(manifest.get("status") or "")
    stage, tone = STATUS_STAGE.get(status, ("planning", "info"))
    tier = _plan_tier(plan, manifest, events)
    planner = _plan_planner(plan, manifest, events, tier)
    spend = _spend(plan, budget)
    verify = _verify_summary(plan, events)
    handoff = _handoff(root, run_dir, manifest)
    # Usage is computed from the approval file; do not import the whole runner
    # into a read-only path.
    usage = _usage(root, run_dir, plan, manifest)
    if not usage_path.exists() and status in {"PLANNING", "AWAITING_SIGNOFF"}:
        usage["status"] = "awaiting"

    plan_hash: str | None = None
    if plan:
        try:
            from v2.plan import plan_hash as compute_plan_hash

            plan_hash = compute_plan_hash(plan)
        except Exception:
            summary = plan.get("_meta") if isinstance(plan.get("_meta"), Mapping) else {}
            plan_hash = summary.get("plan_hash")

    workspace = manifest.get("workspace") if isinstance(manifest.get("workspace"), Mapping) else {}
    checkpoints = manifest.get("checkpoints") if isinstance(manifest.get("checkpoints"), list) else []
    warnings = _warnings(
        manifest=manifest, plan=plan, spend=spend, verify=verify,
        handoff=handoff, usage=usage, plan_hash=plan_hash,
    )
    timeline = _timeline(
        status, stage,
        created_at=manifest.get("created_at"),
        updated_at=manifest.get("updated_at"),
        handoff_ready=bool(handoff.get("ready")),
    )
    projected = {
        "schema_version": SCHEMA_VERSION,
        "source": "v2",
        "run_id": manifest.get("run_id") or run_dir.name,
        "goal": redact_text(manifest.get("goal")),
        "status": status or "UNKNOWN",
        "stage": stage,
        "tone": tone,
        "tier": tier,
        "planner": planner,
        "created_at": manifest.get("created_at"),
        "updated_at": manifest.get("updated_at"),
        "base_sha": manifest.get("base_sha"),
        "plan_hash": plan_hash or manifest.get("plan_hash"),
        "submitted_plan_hash": manifest.get("plan_hash"),
        "budget_hash": manifest.get("budget_hash"),
        "current_phase": manifest.get("current_phase"),
        "worktree": workspace.get("worktree_path") or workspace.get("path"),
        "workspace": redact(workspace),
        "state_root": str(run_dir.parent.parent),
        "run_dir": str(run_dir),
        "checkpoints": redact(checkpoints[-20:]),
        "last_error": redact_text(manifest.get("last_error")) if manifest.get("last_error") else None,
        "stop_reason": redact_text(manifest.get("stop_reason")) if manifest.get("stop_reason") else None,
        "timeline": timeline,
        "spend": spend,
        "verify": verify,
        "handoff": handoff,
        "usage": usage,
        "warnings": warnings,
        "blackboard_rounds": sorted({
            _int((event.get("data") or {}).get("round"))
            for event in events
            if event.get("event_type") == "meeting.message"
        } - {0}),
        "event_count": len(events),
    }
    if include_plan:
        projected["plan"] = redact(plan)
    return projected


# ---------------------------------------------------------------------------
# v1 read-only adapter
# ---------------------------------------------------------------------------
def _v1_status(events: list[dict[str, Any]]) -> tuple[str, str, str]:
    last = events[-1] if events else {}
    name = str(last.get("event_type") or "")
    data = last.get("data") if isinstance(last.get("data"), Mapping) else {}
    result = last.get("result") if isinstance(last.get("result"), Mapping) else {}
    status = str(result.get("status") or data.get("status") or "").lower()
    if name in {"recipe.done", "job.done"}:
        if status in {"applied", "done", "ok", "pass", "success"}:
            return "DONE", "handoff", "ok"
        if status in {"stopped", "aborted"}:
            return "STOPPED", "handoff", "warn"
        return "FAILED", "handoff", "bad"
    if name in {"recipe.start", "job.start"}:
        return "RUNNING", "executing", "active"
    return "UNKNOWN", "planning", "info"


def _v1_runs(root: Path, ledger: Path) -> list[dict[str, Any]]:
    """Read the v1 ledger without writing a single byte to it."""
    events = _read_jsonl(ledger)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        run_id = str(event.get("run_id") or "").strip()
        if run_id:
            grouped.setdefault(run_id, []).append(event)
    runs: list[dict[str, Any]] = []
    for run_id, run_events in grouped.items():
        run_events.sort(key=lambda item: (_iso_to_epoch(item.get("timestamp")), 0))
        status, stage, tone = _v1_status(run_events)
        start = _first_event(run_events, "recipe.start", "job.start")
        start_data = start.get("data") if isinstance(start.get("data"), Mapping) else {}
        done = _last_event(run_events, "recipe.done", "job.done")
        done_data = done.get("data") if isinstance(done.get("data"), Mapping) else {}
        result = done.get("result") if isinstance(done.get("result"), Mapping) else {}
        tier = str(start_data.get("tier") or "").strip().upper() or None
        if tier not in {"E1", "E2", "E3", "E4"}:
            tier = None
        verify_command = start_data.get("verify_command")
        goal = start_data.get("objective") or start_data.get("title") or \
            start_data.get("goal") or start_data.get("task_id") or run_id
        snapshot = {
            "schema_version": SCHEMA_VERSION,
            "source": "v1",
            "run_id": run_id,
            "goal": redact_text(goal, limit=400),
            "status": status,
            "stage": stage,
            "tone": tone,
            "tier": tier,
            "planner": None,
            "created_at": (run_events[0] or {}).get("timestamp"),
            "updated_at": (run_events[-1] or {}).get("timestamp"),
            "base_sha": None,
            "plan_hash": None,
            "submitted_plan_hash": None,
            "budget_hash": None,
            "current_phase": None,
            "worktree": None,
            "workspace": {},
            "state_root": None,
            "run_dir": str(ledger),
            "checkpoints": [],
            "last_error": redact_text((result.get("error") or done_data.get("error") or "")) or None,
            "stop_reason": None,
            "timeline": _timeline(
                status, stage,
                created_at=(run_events[0] or {}).get("timestamp"),
                updated_at=(run_events[-1] or {}).get("timestamp"),
                handoff_ready=status == "DONE",
            ),
            "spend": {
                "estimated_requests": _int(start_data.get("requests")),
                "estimated_cost_usd": 0.0,
                "estimated_tokens": 0,
                "used_requests": _int(done_data.get("requests")),
                "used_cost_usd": 0.0,
                "used_tokens": _int(done_data.get("in_tokens")) + _int(done_data.get("out_tokens")),
                "reserved_requests": 0,
                "reserved_cost_usd": 0.0,
                "remaining_requests": 0,
                "remaining_cost_usd": 0.0,
                "remaining_tokens": 0,
                "max_requests": 0,
                "max_cost_usd": 0.0,
                "phases": {},
            },
            "verify": {
                "commands": [verify_command] if verify_command else [],
                "command": verify_command,
                "ok": None,
                "last_exit": None,
                "last_at": None,
                "results": [],
            },
            "handoff": {"ready": False, "patch": None, "report": None,
                        "patch_bytes": 0, "report_text": "", "merged": False,
                        "merge": {}, "rolled_back": False, "rollback": {},
                        "status": status},
            "usage": {"status": "unknown", "required": False, "reasons": [],
                      "estimate": {}, "signoff": {"signed": False}, "error": None},
            "warnings": ["legacy v1 ledger entry (read-only)"],
            "blackboard_rounds": sorted({
                _int((event.get("data") or {}).get("round"))
                for event in run_events
                if event.get("event_type") == "meeting.message"
            } - {0}),
            "event_count": len(run_events),
        }
        runs.append(redact(snapshot))
    return runs


# ---------------------------------------------------------------------------
# Public read API
# ---------------------------------------------------------------------------
def list_runs(root: str | Path, *, state_root: str | Path | None = None,
              include_v1: bool = True, limit: int | None = None,
              query: str | None = None) -> list[dict[str, Any]]:
    """Return every normalized run, newest first.

    v2 state is authoritative; the v1 ledger is an optional, read-only extra
    source.  A failure to read one run never hides the others.
    """
    root_path = Path(root).expanduser().resolve()
    runs_dir = state_root_for(root_path, state_root) / "runs"
    runs: list[dict[str, Any]] = []
    if runs_dir.exists():
        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            manifest = _read_json(run_dir / "run_manifest.json")
            if not manifest:
                continue
            try:
                runs.append(_project_manifest(root_path, run_dir, manifest))
            except Exception as exc:  # noqa: BLE001 - one bad run must not break the list
                runs.append({
                    "schema_version": SCHEMA_VERSION,
                    "source": "v2",
                    "run_id": run_dir.name,
                    "goal": "",
                    "status": "UNREADABLE",
                    "stage": "planning",
                    "tone": "bad",
                    "warnings": [f"cannot read run: {type(exc).__name__}: {exc}"[:400]],
                    "event_count": 0,
                })
    if include_v1:
        try:
            runs.extend(_v1_runs(root_path, v1_ledger_for(root_path)))
        except Exception:  # noqa: BLE001 - the v1 bridge is optional
            pass
    runs.sort(
        key=lambda item: (
            _iso_to_epoch(item.get("updated_at")),
            str(item.get("run_id") or ""),
        ),
        reverse=True,
    )
    if query:
        needle = str(query).strip().lower()
        runs = [
            item for item in runs
            if needle in str(item.get("run_id") or "").lower()
            or needle in str(item.get("goal") or "").lower()
        ]
    if limit is not None:
        runs = runs[: max(0, int(limit))]
    return runs[:MAX_RUNS]


def read_run(root: str | Path, run_id: str, *, state_root: str | Path | None = None,
             include_v1: bool = True) -> dict[str, Any]:
    """Load one run with its plan, events and blackboard projection."""
    root_path = Path(root).expanduser().resolve()
    run_id = str(run_id or "").strip()
    if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        raise ValueError("invalid run_id")
    runs_dir = state_root_for(root_path, state_root) / "runs"
    run_dir = runs_dir / run_id
    manifest = _read_json(run_dir / "run_manifest.json")
    if manifest:
        events = _read_jsonl(run_dir / "events.ndjson")
        detail = _project_manifest(root_path, run_dir, manifest, events=events,
                                   include_plan=True)
        detail["events"] = [
            redact(_project_event(event)) for event in events[-500:]
        ]
        detail["blackboard"] = blackboard(root_path, run_id, state_root=state_root)
        return detail
    if include_v1:
        for item in _v1_runs(root_path, v1_ledger_for(root_path)):
            if str(item.get("run_id")) == run_id:
                item["events"] = [
                    redact(_project_event(event))
                    for event in _read_jsonl(v1_ledger_for(root_path))
                    if str(event.get("run_id")) == run_id
                ][-500:]
                item["blackboard"] = blackboard(root_path, run_id,
                                                state_root=state_root)
                return item
    raise FileNotFoundError(f"run not found: {run_id}")


def run_detail(root: str | Path, run_id: str, *, state_root: str | Path | None = None,
               include_v1: bool = True) -> dict[str, Any]:
    return read_run(root, run_id, state_root=state_root, include_v1=include_v1)


def load_runs(root: str | Path, *, state_root: str | Path | None = None,
              include_v1: bool = True) -> list[dict[str, Any]]:
    return list_runs(root, state_root=state_root, include_v1=include_v1)


# Common aliases keep the reader intuitive for CLI/tests.
load_run = read_run
normalize_run = read_run
events = None  # assigned after read_events is defined


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
def _project_event(event: Mapping[str, Any], index: int = 0) -> dict[str, Any]:
    event_id = str(event.get("event_id") or "")
    if not event_id:
        raw = json.dumps(event, sort_keys=True, ensure_ascii=False).encode("utf-8")
        event_id = "legacy_" + hashlib.sha256(raw + str(index).encode()).hexdigest()[:16]
    data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
    status = event.get("status")
    if status is None:
        result = event.get("result") if isinstance(event.get("result"), Mapping) else {}
        status = result.get("status") or "info"
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "run_id": event.get("run_id"),
        "event_type": event.get("event_type") or event.get("type") or "legacy",
        "timestamp": event.get("timestamp"),
        "status": status,
        "phase_id": event.get("phase_id") or "",
        "action_id": event.get("action_id") or "",
        "data": redact(dict(data)),
    }


def read_events(root: str | Path, run_id: str, *, state_root: str | Path | None = None,
                cursor: int | str | None = 0, limit: int = DEFAULT_EVENT_LIMIT,
                event_type: str | None = None) -> dict[str, Any]:
    """Cursor-paged events for one run (never exposes another run)."""
    run_id = str(run_id or "").strip()
    if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        raise ValueError("invalid run_id")
    try:
        start = max(0, int(cursor or 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("cursor must be an integer") from exc
    limit = max(1, min(MAX_EVENT_LIMIT, int(limit or DEFAULT_EVENT_LIMIT)))
    root_path = Path(root).expanduser().resolve()
    run_dir = state_root_for(root_path, state_root) / "runs" / run_id
    source = run_dir / "events.ndjson"
    if not source.exists() and v1_ledger_for(root_path).exists():
        source = v1_ledger_for(root_path)
    events = [
        event for event in _read_jsonl(source)
        if not run_id or str(event.get("run_id") or "") == run_id
    ]
    if event_type:
        events = [event for event in events if str(event.get("event_type")) == str(event_type)]
    window = events[start:start + limit]
    next_cursor = str(start + limit) if start + limit < len(events) else None
    return {
        "run_id": run_id,
        "items": [_project_event(event, start + index) for index, event in enumerate(window)],
        "next_cursor": next_cursor,
        "total": len(events),
        "cursor": start,
        "limit": limit,
    }


def run_events(root: str | Path, run_id: str, **kwargs: Any) -> dict[str, Any]:
    return read_events(root, run_id, **kwargs)


# Alias after the implementation exists.
events = read_events


def list_v1_runs(root: str | Path) -> list[dict[str, Any]]:
    """Read-only projection of the frozen v1 ledger."""
    return _v1_runs(Path(root).expanduser().resolve(), v1_ledger_for(root))


# ---------------------------------------------------------------------------
# Blackboard / E4 meeting
# ---------------------------------------------------------------------------
def blackboard(root: str | Path, run_id: str, *, state_root: str | Path | None = None,
               max_events: int = 500) -> dict[str, Any]:
    payload = read_events(root, run_id, state_root=state_root, limit=MAX_EVENT_LIMIT)
    events: list[dict[str, Any]] = list(payload["items"])
    cursor = payload.get("next_cursor")
    while cursor and len(events) < max_events:
        page = read_events(root, run_id, state_root=state_root,
                           cursor=cursor, limit=MAX_EVENT_LIMIT)
        events.extend(page["items"])
        cursor = page.get("next_cursor")
    messages: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for event in events:
        name = str(event.get("event_type") or "")
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        if name == "meeting.message":
            messages.append(redact({
                "event_id": event.get("event_id"),
                "timestamp": event.get("timestamp"),
                "round": _int(data.get("round")),
                "sender": data.get("sender"),
                "recipients": data.get("recipients") or [],
                "kind": data.get("kind"),
                "body": redact_text(data.get("body"), limit=MAX_EVENT_BYTES),
                "reply_to": data.get("reply_to"),
                "evidence_refs": data.get("evidence_refs") or [],
                "task_id": data.get("task_id"),
            }))
        elif name == "meeting.decision":
            decisions.append(redact({
                "event_id": event.get("event_id"),
                "timestamp": event.get("timestamp"),
                "round": _int(data.get("round")),
                "verdict": data.get("verdict"),
                "reasons": data.get("reasons") or [],
                "vetoes": data.get("vetoes") or [],
                "dissent": data.get("dissent") or [],
                "task_id": data.get("task_id"),
            }))
    rounds: list[dict[str, Any]] = []
    for round_id in sorted({int(item.get("round") or 0) for item in messages}):
        rounds.append({
            "round": round_id,
            "messages": [item for item in messages if _int(item.get("round")) == round_id],
            "decision": next(
                (item for item in decisions if _int(item.get("round")) == round_id), None
            ),
        })
    return {
        "run_id": run_id,
        "messages": messages,
        "decisions": decisions,
        "rounds": rounds,
        "stats": {
            "messages": len(messages),
            "rounds": sorted({int(item.get("round") or 0) for item in messages} - {0}),
            "objections": sum(1 for item in messages if item.get("kind") == "objection"),
            "rebuttals": sum(1 for item in messages if item.get("kind") == "rebuttal"),
            "verdict": decisions[-1].get("verdict") if decisions else None,
            "vetoes": decisions[-1].get("vetoes") if decisions else [],
        },
        "edges": _meeting_edges(messages),
        "active": not (events[-1].get("event_type") == "run.handoff"
                       if events else False),
    }


def _meeting_edges(messages: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for message in messages:
        sender = str(message.get("sender") or "")
        for recipient in message.get("recipients") or []:
            pair = (sender, str(recipient))
            if sender and recipient and pair not in seen:
                seen.add(pair)
                edges.append({"from": sender, "to": str(recipient)})
    return edges


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------
def run_diff(root: str | Path, run_id: str, *, state_root: str | Path | None = None,
             max_bytes: int | None = None) -> dict[str, Any]:
    """Return the handoff patch, never larger than ``MAX_DIFF_BYTES``.

    The cap is enforced on the exact bytes written to the response, including
    any truncation marker.  A caller cannot raise the hard maximum.
    """
    cap = MAX_DIFF_BYTES if max_bytes is None else min(MAX_DIFF_BYTES, max(1, int(max_bytes)))
    root_path = Path(root).expanduser().resolve()
    run_id = str(run_id or "").strip()
    if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        raise ValueError("invalid run_id")
    patch = state_root_for(root_path, state_root) / "runs" / run_id / "handoff" / "handoff.patch"
    if not patch.exists():
        raise FileNotFoundError("handoff patch not found")
    raw = patch.read_bytes()
    truncated = len(raw) > cap
    body = raw[:cap]
    text = body.decode("utf-8", errors="replace")
    if truncated:
        marker = "\n... [diff truncated at size cap] ...\n"
        text = text[: max(0, cap - len(marker))] + marker
    report_path = patch.with_name("report.md")
    report = ""
    if report_path.exists():
        report = redact_text(report_path.read_text(encoding="utf-8", errors="replace"),
                             limit=min(cap, 20_000))
    return {
        "run_id": run_id,
        "diff": redact_text(text, limit=cap),
        "report": report,
        "bytes": len(raw),
        "returned_bytes": len(text),
        "truncated": truncated,
        "cap_bytes": cap,
    }


def diff(root: str | Path, run_id: str, **kwargs: Any) -> dict[str, Any]:
    return run_diff(root, run_id, **kwargs)


# ---------------------------------------------------------------------------
# Control capability (read-only projection for the UI)
# ---------------------------------------------------------------------------
def capabilities(manifest: Mapping[str, Any] | dict[str, Any]) -> dict[str, bool]:
    if not manifest:
        return {"pause": False, "resume": False, "stop": False, "accept": False,
                "usage_approve": False, "merge": False, "rollback": False}
    status = str(manifest.get("status") or "")
    handoff_ready = status in {"HANDOFF_READY", "DONE"}
    merged = bool(manifest.get("_merged"))
    return {
        "pause": status in {"PROVISIONING", "PHASE_EXECUTING", "PHASE_VERIFYING"},
        "resume": status == "PAUSED",
        "stop": status in {"PROVISIONING", "PHASE_EXECUTING", "PHASE_VERIFYING",
                           "PAUSED", "BLOCKED"},
        "accept": status == "HANDOFF_READY",
        "usage_approve": status == "AWAITING_SIGNOFF",
        "merge": handoff_ready and not merged,
        "rollback": merged,
    }


def summary(root: str | Path, *, state_root: str | Path | None = None,
            include_v1: bool = True) -> dict[str, Any]:
    runs = list_runs(root, state_root=state_root, include_v1=include_v1)
    counts: dict[str, int] = {}
    for run in runs:
        key = str(run.get("status") or "UNKNOWN")
        counts[key] = counts.get(key, 0) + 1
    return {
        "total": len(runs),
        "counts": counts,
        "v2": sum(1 for run in runs if run.get("source") == "v2"),
        "v1": sum(1 for run in runs if run.get("source") == "v1"),
        "warnings": sum(len(run.get("warnings") or []) for run in runs),
        "updated_at": _utc_now(),
    }
