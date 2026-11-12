# -*- coding: utf-8 -*-
"""Per-run human-readable logbook generation.

The logbook is a read-only projection of the existing run state.  Raw state,
events and the handoff patch remain the source of truth.  The generated files
are placed inside the run directory so concurrent runs never share a mutable
log file:

    .agents/tmp/v2/runs/<run_id>/logbook.md
    .agents/tmp/v2/runs/<run_id>/logbook.json
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from unified import state as unified_state


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def _event_line(event: Mapping[str, Any]) -> str:
    name = str(event.get("event_type") or "event")
    timestamp = str(event.get("timestamp") or "")
    data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
    message = data.get("message") or data.get("body") or data.get("summary")
    if message:
        text = str(message).replace("\n", " ")
        return f"- `{timestamp}` **{name}** — {text[:500]}"
    return f"- `{timestamp}` **{name}**"


def build(root: str | Path, run_id: str, *, state_root: str | Path | None = None) -> dict[str, Any]:
    """Build a logbook payload without writing it."""
    detail = unified_state.read_run(root, run_id, state_root=state_root)
    plan = detail.get("plan") if isinstance(detail.get("plan"), Mapping) else {}
    events_payload = unified_state.read_events(
        root, run_id, state_root=state_root, limit=200,
    )
    events = events_payload.get("items") if isinstance(events_payload, Mapping) else []
    events = list(events or [])
    handoff = detail.get("handoff") if isinstance(detail.get("handoff"), Mapping) else {}
    verify = detail.get("verify") if isinstance(detail.get("verify"), Mapping) else {}
    spend = detail.get("spend") if isinstance(detail.get("spend"), Mapping) else {}
    phases = plan.get("phases") or []
    work_orders = [
        order
        for phase in phases if isinstance(phase, Mapping)
        for order in (phase.get("work_orders") or []) if isinstance(order, Mapping)
    ]
    files = sorted({
        str(target)
        for order in work_orders
        for target in (order.get("target_files") or [])
        if target
    })
    commands = sorted({
        str(order.get("verify_command"))
        for order in work_orders
        if order.get("verify_command")
    })
    diff_text = ""
    try:
        diff_payload = unified_state.run_diff(root, run_id, state_root=state_root)
        diff_text = str(diff_payload.get("diff") or "")
    except Exception:
        pass

    lines = [
        f"# Run Logbook — {run_id}",
        "",
        "> Generated from the read-only run state and event ledger.",
        "",
        "## Summary",
        "",
        f"- **Run ID:** `{run_id}`",
        f"- **Goal:** {_fmt(detail.get('goal'))}",
        f"- **Status:** `{_fmt(detail.get('status'))}`",
        f"- **Tier / Planner:** `{_fmt(detail.get('tier'))}` / `{_fmt(detail.get('planner'))}`",
        f"- **Source:** `{_fmt(detail.get('source'))}`",
        f"- **Created:** {_fmt(detail.get('created_at'))}",
        f"- **Updated:** {_fmt(detail.get('updated_at'))}",
        f"- **Worktree:** `{_fmt(detail.get('worktree'))}`",
        f"- **State root:** `{_fmt(detail.get('state_root'))}`",
        f"- **Logbook generated:** {_timestamp()}",
        "",
        "## Plan",
        "",
        f"- **Phases:** {len(phases)}",
        f"- **Work orders:** {len(work_orders)}",
        f"- **Target files:** {', '.join(f'`{item}`' for item in files) or '-'}",
        "",
    ]
    for phase in phases:
        if not isinstance(phase, Mapping):
            continue
        lines.append(f"### Phase `{_fmt(phase.get('id'))}`")
        lines.append("")
        for order in phase.get("work_orders") or []:
            if not isinstance(order, Mapping):
                continue
            lines.append(
                f"- `{_fmt(order.get('id'))}` {_fmt(order.get('objective') or order.get('title'))}"
            )
            lines.append(f"  - target: `{_fmt(order.get('target_files'))}`")
            lines.append(f"  - verify: `{_fmt(order.get('verify_command'))}`")
        lines.append("")
    lines.extend([
        "## Verification",
        "",
        f"- **Status:** {_fmt(verify.get('ok'))}",
        f"- **Last exit:** {_fmt(verify.get('last_exit'))}",
        f"- **Commands:** {', '.join(f'`{item}`' for item in commands) or '-'}",
        "",
    ])
    for result in verify.get("results") or []:
        if isinstance(result, Mapping):
            lines.append(
                f"- `{_fmt(result.get('event_type'))}`: exit "
                f"`{_fmt(result.get('exit_code'))}` — {_fmt(result.get('command'))}"
            )
    lines.extend([
        "",
        "## Spend",
        "",
        f"- Requests: `{_fmt(spend.get('used_requests'))}` / `{_fmt(spend.get('max_requests'))}`",
        f"- Tokens: `{_fmt(spend.get('used_tokens'))}` / `{_fmt(spend.get('estimated_tokens'))}`",
        f"- Cost USD: `{_fmt(spend.get('used_cost_usd'))}`",
        "",
        "## Delivery",
        "",
        f"- Handoff ready: `{_fmt(handoff.get('ready'))}`",
        f"- Merged: `{_fmt(handoff.get('merged'))}`",
        f"- Merge SHA: `{_fmt((handoff.get('merge') or {}).get('merge_sha'))}`",
        f"- Rolled back: `{_fmt(handoff.get('rolled_back'))}`",
        f"- Rollback SHA: `{_fmt((handoff.get('rollback') or {}).get('rollback_sha'))}`",
        "",
        "## Events",
        "",
    ])
    lines.extend(_event_line(event) for event in events)
    lines.extend(["", "## Diff", "", "```diff", diff_text[:20_000], "```", ""])
    markdown = "\n".join(lines)
    return {
        "run_id": run_id,
        "markdown": markdown,
        "event_count": len(events),
        "phase_count": len(phases),
        "work_order_count": len(work_orders),
        "files": files,
        "commands": commands,
        "status": detail.get("status"),
        "generated_at": _timestamp(),
    }


def write(root: str | Path, run_id: str, *, state_root: str | Path | None = None) -> dict[str, Any]:
    """Generate and persist the per-run logbook inside the run directory."""
    payload = build(root, run_id, state_root=state_root)
    detail = unified_state.read_run(root, run_id, state_root=state_root)
    run_dir = Path(str(detail.get("run_dir") or ""))
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run directory not found: {run_dir}")
    markdown_path = run_dir / "logbook.md"
    json_path = run_dir / "logbook.json"
    markdown_path.write_text(payload["markdown"], encoding="utf-8")
    json_payload = {key: value for key, value in payload.items() if key != "markdown"}
    json_payload["markdown_path"] = str(markdown_path)
    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        **json_payload,
        "markdown_path": str(markdown_path),
        "json_path": str(json_path),
    }
