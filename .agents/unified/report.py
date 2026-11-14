#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F11: unified run completion report (backend only, no UI/assets here).

One stable JSON schema plus a Markdown rendering, built from data the run has
already written: manifest, plan, budget, events, handoff patch, merge/rollback
records, attempts and the decision inbox.

Every string passes through the shared redactor, so a report can be downloaded
or pasted into a review thread without leaking a token.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from unified import state as unified_state
from unified.state import redact, redact_text

SCHEMA_VERSION = 1
MAX_FILES = 200
MAX_OUTPUT_TAIL = 4_000

_DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_HUNK = re.compile(r"^@@")
_MERGE_FILE = "merge.json"
_ROLLBACK_FILE = "rollback.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_events(run_dir: Path, *, limit: int = 2000) -> list[dict[str, Any]]:
    path = run_dir / "events.ndjson"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
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
            rows.append(event)
    return rows[-limit:]


def diffstat(patch: str) -> dict[str, Any]:
    """Count files/lines touched by a unified diff (binary safe)."""
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    additions = 0
    deletions = 0
    binary = 0
    for line in patch.splitlines():
        header = _DIFF_HEADER.match(line)
        if header:
            if current is not None:
                files.append(current)
            current = {
                "path": header.group(2),
                "additions": 0,
                "deletions": 0,
                "binary": False,
            }
            continue
        if current is None:
            continue
        if line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            current["binary"] = True
            binary += 1
            continue
        if _HUNK.match(line):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current["additions"] += 1
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            current["deletions"] += 1
            deletions += 1
    if current is not None:
        files.append(current)
    return {
        "files": files[:MAX_FILES],
        "files_changed": len(files),
        "additions": additions,
        "deletions": deletions,
        "binary": binary,
    }


def _verify_rows(detail: Mapping[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    verify = detail.get("verify") if isinstance(detail.get("verify"), Mapping) else {}
    for item in verify.get("results") or []:
        if not isinstance(item, Mapping):
            continue
        rows.append({
            "command": redact_text(item.get("command")),
            "exit_code": item.get("exit_code"),
            "ok": int(item.get("exit_code") or 0) == 0 if item.get("exit_code") is not None else None,
            "status": item.get("status"),
            "at": item.get("timestamp"),
            "output_tail": redact_text(item.get("output"), limit=MAX_OUTPUT_TAIL),
            "source": item.get("event_type"),
        })
    if not rows:
        # Fall back to the raw events so an old run still reports its exits.
        for event in events:
            name = str(event.get("event_type") or "")
            data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
            if "verify" not in name:
                continue
            exit_code = data.get("exit_code", data.get("verify_exit"))
            if exit_code is None:
                continue
            rows.append({
                "command": redact_text(data.get("command") or data.get("verify_command")),
                "exit_code": exit_code,
                "ok": int(exit_code or 0) == 0,
                "status": event.get("status"),
                "at": event.get("timestamp"),
                "output_tail": redact_text(
                    data.get("output_tail") or data.get("output"), limit=MAX_OUTPUT_TAIL,
                ),
                "source": name,
            })
    return rows[-20:]


def _review_rows(events: list[dict[str, Any]]) -> dict[str, Any]:
    votes: list[dict[str, Any]] = []
    roles: list[str] = []
    for event in events:
        name = str(event.get("event_type") or "")
        if name not in {"task.reviews", "run.reviews"}:
            continue
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        for item in data.get("reviews") or []:
            if not isinstance(item, Mapping):
                continue
            role = str(item.get("role") or "")
            if role and role not in roles:
                roles.append(role)
            votes.append(redact({
                "role": role,
                "verdict": item.get("verdict"),
                "severity": item.get("severity"),
                "model": item.get("model"),
                "evidence": item.get("evidence") or [],
                "task_id": data.get("task_id"),
                "phase_id": data.get("phase_id"),
                "at": event.get("timestamp"),
            }))
    return {"roles": roles, "votes": votes[-80:], "count": len(votes)}


def _merge_rows(run_dir: Path) -> dict[str, Any]:
    merge = _read_json(run_dir / _MERGE_FILE)
    rollback = _read_json(run_dir / _ROLLBACK_FILE)
    merge_sha = merge.get("merge_sha") or merge.get("sha")
    rollback_sha = (
        rollback.get("rollback_sha")
        or rollback.get("reverted_to")
        or rollback.get("merge_sha")
    )
    return {
        "merged": bool(merge),
        "merge_sha": merge_sha,
        "pre_merge_sha": merge.get("pre_merge_sha"),
        "merged_at": merge.get("merged_at"),
        "merge_message": redact_text(merge.get("message")),
        "rolled_back": bool(rollback),
        "rollback_sha": rollback_sha,
        "rolled_back_at": rollback.get("rolled_back_at") or rollback.get("at"),
        "rollback_reason": redact_text(rollback.get("reason")),
    }


def _attempts(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "attempts.ndjson"
    if not path.exists():
        return {"total": 0, "tasks": {}, "history": {}}
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    except OSError:
        return {"total": 0, "tasks": {}, "history": {}}
    latest: dict[str, dict[str, Any]] = {}
    history: dict[str, list[dict[str, Any]]] = {}
    for item in rows:
        task_id = str(item.get("task_id") or "unknown")
        latest[task_id] = item
        history.setdefault(task_id, []).append({
            "attempt": item.get("attempt"),
            "state": item.get("state"),
            "model": item.get("model"),
            "duration_ms": item.get("duration_ms"),
            "failure_kind": item.get("failure_kind"),
        })
    return {
        "total": len(rows),
        "tasks": {key: redact(value) for key, value in latest.items()},
        "history": history,
    }


def _inbox(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "inbox.ndjson"
    if not path.exists():
        return {"total": 0, "pending": 0, "items": []}
    items: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                items.append(item)
    except OSError:
        return {"total": 0, "pending": 0, "items": []}
    pending = [item for item in items if item.get("status") == "pending"]
    return {
        "total": len(items),
        "pending": len(pending),
        "items": [redact(item) for item in items[-20:]],
    }


def _duration_seconds(created_at: Any, updated_at: Any) -> float | None:
    start = unified_state._iso_to_epoch(created_at)
    end = unified_state._iso_to_epoch(updated_at)
    if not start or not end:
        return None
    return round(max(0.0, end - start), 3)


def _next_action(status: str, *, report_available: bool, merged: bool) -> str:
    if status == "AWAITING_SIGNOFF":
        return "Approve the plan exactly as submitted, then sign off usage."
    if status in {"PLANNING", "PROVISIONING"}:
        return "Wait for the planner/executor; no action needed."
    if status in {"PHASE_EXECUTING", "PHASE_VERIFYING", "PHASE_COMMITTED"}:
        return "Let the run finish, or stop it explicitly."
    if status == "PAUSED":
        return "Resume the run, or answer the pending decision in the inbox."
    if status in {"BLOCKED", "RATE_LIMITED"}:
        return "Review the blocker, then resume or stop the run."
    if status in {"FAILED", "QUARANTINED"}:
        return "Read the quarantine evidence, then fix and re-plan."
    if status in {"HANDOFF_READY", "DONE"}:
        if merged:
            return "Run verified after merge. Roll back explicitly if it regresses."
        return (
            "Review the handoff patch, then merge explicitly "
            "(no auto-merge ever happens)."
        )
    if status == "STOPPED":
        return "Run stopped by the operator; start a new run to continue."
    if not report_available:
        return "No handoff yet; wait for the run to reach HANDOFF_READY."
    return "Inspect the run detail in the War Room."


def build_report(
    root: str | Path,
    run_id: str,
    *,
    state_root: str | Path | None = None,
    include_v1: bool = False,
) -> dict[str, Any]:
    """Compile the completion report for one run.

    Raises ``FileNotFoundError`` when the run does not exist, which lets the API
    answer 404 without guessing.
    """
    detail = unified_state.read_run(
        root, run_id, state_root=state_root, include_v1=include_v1,
    )
    run_dir = Path(str(detail.get("run_dir") or ""))
    status = str(detail.get("status") or "UNKNOWN")
    handoff = detail.get("handoff") if isinstance(detail.get("handoff"), Mapping) else {}
    events = _read_events(run_dir)

    patch_text = ""
    patch_path = run_dir / "handoff" / "handoff.patch"
    if patch_path.exists():
        try:
            patch_text = patch_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            patch_text = ""
    changes = diffstat(patch_text)
    changes["patch_bytes"] = len(patch_text.encode("utf-8"))

    tier = str(detail.get("tier") or "E3")
    blackboard = detail.get("blackboard") if isinstance(detail.get("blackboard"), Mapping) else {}
    meeting = None
    if tier == "E4" and blackboard.get("messages"):
        meeting = redact({
            "rounds": blackboard.get("rounds") or [],
            "decisions": blackboard.get("decisions") or [],
            "stats": blackboard.get("stats") or {},
        })

    merge = _merge_rows(run_dir)
    verify_rows = _verify_rows(detail, events)
    verify_detail = detail.get("verify") if isinstance(detail.get("verify"), Mapping) else {}
    report = {
        "schema_version": SCHEMA_VERSION,
        "run_id": detail.get("run_id") or str(run_id),
        "source": detail.get("source") or "v2",
        "status": status,
        "goal": detail.get("goal"),
        "tier": tier,
        "planner": detail.get("planner"),
        "created_at": detail.get("created_at"),
        "updated_at": detail.get("updated_at"),
        "duration_seconds": _duration_seconds(
            detail.get("created_at"), detail.get("updated_at"),
        ),
        "base_sha": detail.get("base_sha"),
        "plan_hash": detail.get("plan_hash"),
        "worktree": detail.get("worktree"),
        "state_root": detail.get("state_root"),
        "spend": redact(detail.get("spend") or {}),
        "changes": changes,
        "verify": {
            "commands": verify_detail.get("commands") or [],
            "ok": verify_detail.get("ok"),
            "last_exit": verify_detail.get("last_exit"),
            "rows": verify_rows,
        },
        "reviews": _review_rows(events),
        "meeting": meeting,
        "graph": redact(detail.get("graph") or {}),
        "attempts": _attempts(run_dir),
        "inbox": _inbox(run_dir),
        "handoff": {
            "ready": bool(handoff.get("ready")),
            "patch": handoff.get("patch"),
            "report": handoff.get("report"),
            "patch_bytes": handoff.get("patch_bytes"),
            "merge_sha": merge["merge_sha"],
            "rolled_back": merge["rolled_back"],
            "rollback_sha": merge["rollback_sha"],
        },
        "merge": merge,
        "warnings": list(detail.get("warnings") or []),
    }
    report["next_action"] = _next_action(
        status,
        report_available=bool(handoff.get("ready")),
        merged=bool(merge["merged"]),
    )
    return redact(report)


def render_markdown(report: Mapping[str, Any]) -> str:
    """Stable Markdown rendering used by the download endpoint."""
    report = report if isinstance(report, Mapping) else {}
    lines: list[str] = []
    lines.append(f"# Run report · {report.get('run_id') or 'unknown'}")
    lines.append("")
    lines.append(f"- **Status:** `{report.get('status') or 'UNKNOWN'}`")
    if report.get("goal"):
        lines.append(f"- **Goal:** {report.get('goal')}")
    lines.append(f"- **Tier / planner:** {report.get('tier')} / {report.get('planner')}")
    lines.append(f"- **Created:** {report.get('created_at')}")
    lines.append(f"- **Updated:** {report.get('updated_at')}")
    duration = report.get("duration_seconds")
    if duration is not None:
        lines.append(f"- **Duration:** {duration:.1f}s")
    if report.get("base_sha"):
        lines.append(f"- **Base SHA:** `{report.get('base_sha')}`")
    if report.get("plan_hash"):
        lines.append(f"- **Plan hash:** `{report.get('plan_hash')}`")
    if report.get("worktree"):
        lines.append(f"- **Worktree:** `{report.get('worktree')}`")
    lines.append(f"- **Next action:** {report.get('next_action')}")
    lines.append("")

    spend = report.get("spend") if isinstance(report.get("spend"), Mapping) else {}
    lines.append("## Spend")
    lines.append("")
    lines.append(
        f"- Requests: {spend.get('used_requests', 0)} / "
        f"{spend.get('max_requests', 0)}"
    )
    lines.append(
        f"- Cost: ${spend.get('used_cost_usd', 0)} / "
        f"${spend.get('max_cost_usd', 0)}"
    )
    lines.append(
        f"- Tokens: {spend.get('used_tokens', 0)} / "
        f"{spend.get('estimated_tokens', 0)} estimated"
    )
    lines.append("")

    changes = report.get("changes") if isinstance(report.get("changes"), Mapping) else {}
    lines.append(
        "## Changes · "
        f"{changes.get('files_changed', 0)} file(s), "
        f"+{changes.get('additions', 0)} / -{changes.get('deletions', 0)}"
    )
    lines.append("")
    for item in changes.get("files") or []:
        if not isinstance(item, Mapping):
            continue
        if item.get("binary"):
            lines.append(f"- `{item.get('path')}` (binary)")
        else:
            lines.append(
                f"- `{item.get('path')}` +{item.get('additions', 0)} "
                f"-{item.get('deletions', 0)}"
            )
    if not changes.get("files"):
        lines.append("- no diff captured yet")
    lines.append("")

    verify = report.get("verify") if isinstance(report.get("verify"), Mapping) else {}
    lines.append("## Verify")
    lines.append("")
    for command in verify.get("commands") or []:
        lines.append(f"- `{command}`")
    if not verify.get("commands"):
        lines.append("- no verify_command declared")
    for row in verify.get("rows") or []:
        if not isinstance(row, Mapping):
            continue
        lines.append(
            f"- `{row.get('command') or 'verify'}` → exit {row.get('exit_code')} "
            f"({row.get('at') or 'unknown time'})"
        )
        tail = str(row.get("output_tail") or "").strip()
        if tail:
            lines.append("")
            lines.append("```text")
            lines.append(tail[:MAX_OUTPUT_TAIL])
            lines.append("```")
    lines.append("")

    reviews = report.get("reviews") if isinstance(report.get("reviews"), Mapping) else {}
    lines.append("## Review")
    lines.append("")
    roles = reviews.get("roles") or []
    lines.append("- Roles: " + (", ".join(roles) if roles else "none (E1 verify-only)"))
    for vote in reviews.get("votes") or []:
        if not isinstance(vote, Mapping):
            continue
        evidence = "; ".join(str(item) for item in vote.get("evidence") or [])
        lines.append(
            f"- {vote.get('role')}: {vote.get('verdict')} "
            f"({vote.get('model') or 'unknown model'})"
            + (f" — {evidence}" if evidence else "")
        )
    if not reviews.get("votes"):
        lines.append("- no review votes recorded")
    lines.append("")

    meeting = report.get("meeting")
    if meeting:
        lines.append("## E4 blackboard")
        lines.append("")
        for row in meeting.get("rounds") or []:
            if not isinstance(row, Mapping):
                continue
            decision = row.get("decision") or {}
            lines.append(
                f"- Round {row.get('round')}: "
                f"{len(row.get('messages') or [])} message(s), "
                f"verdict {decision.get('verdict') or 'n/a'}"
            )
        lines.append("")

    graph = report.get("graph") if isinstance(report.get("graph"), Mapping) else {}
    if graph.get("available"):
        lines.append("## Task graph")
        lines.append("")
        lines.append(
            f"- Tasks: {graph.get('tasks', 0)}, edges: {graph.get('edges', 0)}, "
            f"max width: {graph.get('max_width', 0)}"
        )
        for layer in graph.get("layers") or []:
            lines.append(f"- Wave: {', '.join(str(item) for item in layer)}")
        lines.append("")

    attempts = report.get("attempts") if isinstance(report.get("attempts"), Mapping) else {}
    if attempts.get("total"):
        lines.append("## Attempts")
        lines.append("")
        for task_id, rows in (attempts.get("history") or {}).items():
            summary = ", ".join(
                f"#{row.get('attempt')}:{row.get('state')}"
                for row in rows if isinstance(row, Mapping)
            )
            lines.append(f"- `{task_id}` — {summary}")
        lines.append("")

    inbox = report.get("inbox") if isinstance(report.get("inbox"), Mapping) else {}
    if inbox.get("total"):
        lines.append("## Decision inbox")
        lines.append("")
        lines.append(f"- Pending: {inbox.get('pending', 0)} / {inbox.get('total', 0)}")
        for item in inbox.get("items") or []:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                f"- `{item.get('id')}` [{item.get('status')}] {item.get('question')}"
            )
        lines.append("")

    merge = report.get("merge") if isinstance(report.get("merge"), Mapping) else {}
    lines.append("## Delivery")
    lines.append("")
    if merge.get("merge_sha"):
        lines.append(f"- Merged: `{merge.get('merge_sha')}`")
        lines.append(f"- Pre-merge: `{merge.get('pre_merge_sha')}`")
    else:
        lines.append("- Not merged (main branch untouched)")
    if merge.get("rolled_back"):
        lines.append(
            f"- Rolled back to `{merge.get('rollback_sha')}` "
            f"({merge.get('rollback_reason') or 'operator action'})"
        )
    lines.append("")

    warnings = report.get("warnings") or []
    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def report_markdown(root: str | Path, run_id: str, *,
                    state_root: str | Path | None = None,
                    include_v1: bool = False) -> str:
    return render_markdown(build_report(
        root, run_id, state_root=state_root, include_v1=include_v1,
    ))


def report_summary(root: str | Path, run_id: str, *,
                   state_root: str | Path | None = None) -> dict[str, Any]:
    """Cheap projection embedded in ``read_run`` (no Markdown rendering)."""
    return build_report(root, run_id, state_root=state_root, include_v1=False)
