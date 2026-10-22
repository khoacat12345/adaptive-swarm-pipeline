"""Read-only view model for the dashboard war room.

The dashboard must not guess which agent is on duty. This module reads the
append-only ledger plus job state and exposes the five swarm agents:

    coder + security + qa + perf + docs      (lead stays in the center)

No writes, no model calls, no secrets.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import job
import ledger as ledger_module
import schema
import workers

WAVE_LABELS = {
    "security": "Security",
    "qa": "QA",
    "perf": "Perf",
    "docs": "Docs",
}


def _last_event(events: list[dict], event_type: str) -> dict | None:
    for event in reversed(events):
        if event.get("event_type") == event_type:
            return event
    return None


def _last_wave(events: list[dict]) -> dict | None:
    for event in reversed(events):
        data = event.get("data") or {}
        if data.get("per_role"):
            return event
    return None


def _last_done(events: list[dict]) -> dict | None:
    for event in reversed(events):
        if event.get("event_type") != "recipe.done":
            continue
        data = event.get("data") or {}
        if data:
            return event
    return None


def _model_health(root: Path) -> dict:
    path = root / ".agents" / "tmp" / "health.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    now = time.time()
    result = {}
    for model, item in (data.get("entries") or {}).items():
        cooldown = float(item.get("cooldown_until") or 0) - now
        result[model] = {
            "status": item.get("last_status"),
            "error": item.get("last_error_type"),
            "successes": int(item.get("successes") or 0),
            "failures": int(item.get("failures") or 0),
            "cooldown_s": max(0, int(cooldown)),
        }
    return result


def build(root, ledger) -> dict:
    root_path = Path(root)
    events = schema.read_events(Path(ledger))
    state = job.read_state(root_path)
    plan_state = job.read_plan_state(root_path)
    plan = plan_state.get("plan") or {}
    decision = plan_state.get("decision") or {}
    team = state.get("team") or {}

    route_event = _last_event(events, "router.decided")
    route_data = (route_event or {}).get("data") or {}
    chain = [str(item) for item in (route_data.get("chain") or []) if item]
    coder = team.get("coder") or (chain[0] if chain else None)

    wave_event = _last_wave(events)
    wave_data = (wave_event or {}).get("data") or {}
    skipped = [str(item) for item in (wave_data.get("skipped") or [])]
    verdicts: dict[str, dict] = {}
    for item in wave_data.get("per_role") or []:
        role = str(item.get("role") or "")
        if not role:
            continue
        verdicts[role] = {
            "verdict": item.get("verdict"),
            "model": item.get("model") or workers.WAVE_ROLES.get(role),
            "reasons": list(item.get("reasons") or [])[:6],
            "reviewer": item.get("reviewer"),
        }

    reviewer_mode = str(team.get("reviewer") or "heuristic")
    wave_active = reviewer_mode.lower() in (
        "wave", "wave-reviewer", "role-reviewer")
    roles = [{
        "role": "coder",
        "label": "Coder",
        "model": coder,
        "fallback": team.get("fallback") or (chain[1] if len(chain) > 1 else None),
        "kind": "coder",
        "verdict": None,
        "reasons": [],
        "standby": False,
        "skipped": False,
    }]
    for role in ("security", "qa", "perf", "docs"):
        hit = verdicts.get(role) or {}
        roles.append({
            "role": role,
            "label": WAVE_LABELS.get(role, role.title()),
            "model": hit.get("model") or workers.WAVE_ROLES.get(role),
            "fallback": None,
            "kind": "reviewer",
            "verdict": hit.get("verdict"),
            "reasons": hit.get("reasons") or [],
            "standby": not wave_active,
            "skipped": role in skipped,
        })

    done_event = _last_done(events)
    done_data = (done_event or {}).get("data") or {}

    return {
        "team": team,
        "decision": decision,
        "roles": roles,
        "chain": chain,
        "reviewer_mode": reviewer_mode,
        "wave_active": wave_active,
        "work_type": route_data.get("work_type"),
        "route_label": route_data.get("label"),
        "route_mode": route_data.get("mode"),
        "route_why": route_data.get("why"),
        "degraded": bool(route_data.get("degraded")),
        "last_route_at": (route_event or {}).get("timestamp"),
        "model_health": _model_health(root_path),
        "last_wave": {
            "at": (wave_event or {}).get("timestamp"),
            "run_id": (wave_event or {}).get("run_id"),
            "verdict": wave_data.get("verdict"),
            "skipped": skipped,
            "roles": verdicts,
        } if wave_event else None,
        "last_done": {
            "at": (done_event or {}).get("timestamp"),
            "run_id": (done_event or {}).get("run_id"),
            "status": done_data.get("status"),
            "files": done_data.get("files") or [],
            "requests": done_data.get("requests"),
            "rolled_back": bool(done_data.get("rolled_back")),
            "budget_exceeded": done_data.get("budget_exceeded"),
            "secrets_blocked": int(done_data.get("secrets_blocked") or 0),
            "plan_ok": done_data.get("plan_ok"),
            "fallback_attempts": int(done_data.get("fallback_attempts") or 0),
            "reviewer_fallback": bool(done_data.get("reviewer_fallback")),
            "reviewer_skipped": done_data.get("reviewer_skipped") or [],
        } if done_event else None,
        "plan_job": plan.get("job_id"),
        "plan_file": (plan_state.get("plan") or {}).get("_source"),
        "phase": state.get("phase") or "idle",
    }


def _cli(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Print dashboard war-room view model.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--ledger", default=None)
    args = parser.parse_args(argv)
    ledger = Path(args.ledger) if args.ledger else ledger_module.resolve_ledger_path(args.root)
    print(json.dumps(build(args.root, ledger), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
