#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ledger Schema v1.0.0 (Task 4.1) - hop dong du lieu bat bien.

Moi event trong `.agents/ledger.jsonl` duoc nhin qua 9 truong chuan:

    run_id, timestamp, event_type, node, model, status, duration_ms,
    usage {in_tokens, out_tokens}, details

Lich su: ledger.py (Phase 0) ghi `result` + `data` va `schema_version: 1`.
KHONG rewrite file ledger (append-only). Thay vao do `normalize()` chuyen moi
event cu sang dang chuan, va `validate()` chi bao loi khi thuc su khong doc duoc.

Tuong thich nguoc:

  * truong la -> bo qua (khong raise)
  * truong moi duoc phep them (giu trong `extra`), parser cu van chay
  * thieu `node`/`model` -> suy ra tu `event_type` / `data.model`
  * `schema_version` kieu int (1) duoc coi la 1.0.0

Dung:
    from schema import normalize, validate, SCHEMA_SEMVER
"""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA_SEMVER = "1.0.0"
CANONICAL_FIELDS = ("run_id", "timestamp", "event_type", "node", "model",
                    "status", "duration_ms", "usage", "details")
REQUIRED_FIELDS = ("run_id", "timestamp", "event_type")

VALID_STATUS = ("info", "ok", "pass", "fail", "error", "blocked", "timeout", "skipped")

# Meeting events are additive to schema v1.0.0.  No ledger rewrite is needed.
MEETING_EVENTS = (
    "meeting.opened",
    "meeting.message",
    "meeting.conflict",
    "meeting.unavailable",
    "meeting.rebuttal",
    "meeting.vote",
    "meeting.decision",
    "meeting.aborted",
)
MEETING_MESSAGE_KINDS = (
    "proposal", "objection", "evidence", "question",
    "rebuttal", "vote", "decision", "system",
)
MEETING_ROLES = (
    "coder", "security", "qa", "perf", "docs",
    "lead", "moderator", "systembot",
)

# Vai tro hien thi tren timeline / dashboard.
NODE_BY_EVENT = {
    "recipe.start": "lead",
    "recipe.done": "lead",
    "router.decided": "lead",
    "plan.submitted": "lead",
    "plan.approved": "lead",
    "plan.rejected": "lead",
    "job.start": "lead",
    "job.done": "lead",
    "recipe.ladder": "coder",
    "recipe.coder": "coder",
    "recipe.fallback": "coder",
    "recipe.draft_verify": "systembot",
    "recipe.guard.restore": "lead",
    "reviewer.findings": "reviewer",
    "plan.validated": "lead",
    "memory.hints": "lead",
    "clarify.required": "lead",
    "budget.exceeded": "lead",
    "secret.blocked": "security",
    "permission.denied": "security",
    "safe_writer.backup": "lead",
    "safe_writer.write": "lead",
    "safe_writer.verify": "systembot",
    "safe_writer.rollback": "lead",
    "transaction.begin": "lead",
    "transaction.commit": "lead",
    "transaction.rollback": "lead",
    "transaction.recovered": "lead",
    "system_bot.run": "systembot",
    "system_bot.blocked": "systembot",
    "profile.scan": "lead",
    "scout.catalog.read": "lead",
    "scout.plan": "lead",
    "scout.verify": "lead",
    "meeting.opened": "meeting",
    "meeting.message": "meeting",
    "meeting.conflict": "meeting",
    "meeting.unavailable": "meeting",
    "meeting.rebuttal": "meeting",
    "meeting.vote": "meeting",
    "meeting.decision": "lead",
    "meeting.aborted": "lead",
}


def node_of(event_type: str) -> str:
    """Suy ra node tu event_type; event la / moi -> 'system'."""
    event_type = str(event_type or "")
    if event_type in NODE_BY_EVENT:
        return NODE_BY_EVENT[event_type]
    for prefix, node in (("recipe.", "lead"), ("safe_writer.", "lead"),
                         ("scout.", "lead"), ("phase0", "qa"), ("phase05", "qa"),
                         ("meeting.", "meeting")):
        if event_type.startswith(prefix):
            return node
    return "system"


def model_of(event: dict) -> str | None:
    """Model lien quan (neu co). Khong doan bua."""
    data = event.get("data") or {}
    for key in ("model", "model_name"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    models = data.get("models")
    if isinstance(models, list) and models:
        first = models[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    return None


def status_of(event: dict) -> str:
    result = event.get("result") or {}
    status = result.get("status") or (event.get("data") or {}).get("status") or "info"
    return status if status in VALID_STATUS else "info"


def usage_of(event: dict) -> dict:
    usage = event.get("usage") or {}
    return {
        "in_tokens": int(usage.get("in_tokens", usage.get("input", 0)) or 0),
        "out_tokens": int(usage.get("out_tokens", usage.get("output", 0)) or 0),
        "cache_read_tokens": int(usage.get("cache_read_tokens", 0) or 0),
    }


def details_of(event: dict) -> dict:
    """Gop `data` + phan con lai cua `result` (giu tuong thich nguoc)."""
    details = dict(event.get("data") or {})
    result = event.get("result") or {}
    error = result.get("error")
    if error:
        details.setdefault("error", error)
    return details


def is_meeting_event(event_type: str) -> bool:
    return str(event_type or "").startswith("meeting.")


def validate_meeting_event(event: dict) -> list[str]:
    """Validate meeting payloads additively; non-meeting events are untouched."""
    event_type = str(event.get("event_type") or "")
    if event_type not in MEETING_EVENTS:
        return []
    data = event.get("data") if isinstance(event.get("data"), dict) else event.get("details")
    data = data if isinstance(data, dict) else {}
    problems: list[str] = []
    if not str(data.get("task_id") or "").strip():
        problems.append("meeting event thieu task_id")
    if event_type == "meeting.message":
        if not str(data.get("sender") or "").strip():
            problems.append("meeting.message thieu sender")
        elif str(data.get("sender")).lower() not in MEETING_ROLES:
            problems.append("meeting.message sender khong hop le")
        recipients = data.get("recipients")
        if not isinstance(recipients, list) or not recipients:
            problems.append("meeting.message recipients phai la list khong rong")
        try:
            round_value = int(data.get("round", 0) or 0)
        except (TypeError, ValueError):
            round_value = -1
        if round_value not in (1, 2):
            problems.append("meeting.message round chi duoc 1 hoac 2")
        if str(data.get("kind") or "").lower() not in MEETING_MESSAGE_KINDS:
            problems.append("meeting.message kind khong hop le")
        if not isinstance(data.get("body"), str):
            problems.append("meeting.message body phai la string")
        refs = data.get("evidence_refs")
        if refs is not None and not isinstance(refs, list):
            problems.append("meeting.message evidence_refs phai la list")
        if str(data.get("kind") or "").lower() == "objection" and not refs:
            problems.append("meeting.message objection thieu evidence_refs")
    elif event_type == "meeting.vote":
        if str(data.get("role") or "").lower() not in MEETING_ROLES:
            problems.append("meeting.vote thieu role hop le")
        if str(data.get("verdict") or "").lower() not in (
                "approve", "reject", "abstain", "error"):
            problems.append("meeting.vote verdict khong hop le")
    elif event_type == "meeting.decision":
        if str(data.get("verdict") or "").lower() not in ("approve", "reject", "escalate"):
            problems.append("meeting.decision verdict khong hop le")
    elif event_type == "meeting.unavailable":
        if not isinstance(data.get("roles"), list) or not data.get("roles"):
            problems.append("meeting.unavailable thieu roles")
        if not str(data.get("reason") or "").strip():
            problems.append("meeting.unavailable thieu reason")
    elif event_type == "meeting.aborted":
        if not str(data.get("reason") or "").strip():
            problems.append("meeting.aborted thieu reason")
    return problems


def normalize(event: dict) -> dict:
    """Chuyen 1 event (cu hoac moi) sang dang chuan 9 truong. Khong raise."""
    if not isinstance(event, dict):
        event = {"raw": event}
    known = set(CANONICAL_FIELDS) | {"result", "data", "usage", "schema_version", "event_id"}
    extra = {key: value for key, value in event.items() if key not in known}
    return {
        "run_id": str(event.get("run_id") or "run_unbound"),
        "timestamp": str(event.get("timestamp") or ""),
        "event_type": str(event.get("event_type") or "unknown"),
        "node": str(event.get("node") or node_of(event.get("event_type"))),
        "model": event.get("model") or model_of(event),
        "status": status_of(event),
        "duration_ms": int(event.get("duration_ms", 0) or 0),
        "usage": usage_of(event),
        "details": details_of(event),
        "extra": extra,
        "schema": SCHEMA_SEMVER,
    }


def validate(event: dict) -> list[str]:
    """Loi that (khong doc duoc). Thieu truong phu -> khong coi la loi."""
    problems: list[str] = []
    if not isinstance(event, dict):
        return ["event phai la object JSON"]
    for field in REQUIRED_FIELDS:
        value = event.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"thieu '{field}'")
    usage = event.get("usage")
    if usage is not None and not isinstance(usage, dict):
        problems.append("'usage' phai la object")
    duration = event.get("duration_ms", 0)
    if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
        problems.append("'duration_ms' phai la int >= 0")
    problems.extend(validate_meeting_event(event))
    return problems


def is_compatible(event: dict) -> bool:
    """Event cu (schema_version=1) hay moi deu doc duoc."""
    return not validate(event)


def read_events(path) -> list[dict]:
    """Doc ledger, bo qua dong hong (ledger la append-only, co the bi cat giua)."""
    events: list[dict] = []
    target = Path(path)
    if not target.exists():
        return events
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def describe() -> dict:
    """Mo ta schema (dung cho dashboard / tai lieu / test)."""
    return {
        "schema": SCHEMA_SEMVER,
        "fields": list(CANONICAL_FIELDS),
        "required": list(REQUIRED_FIELDS),
        "status": list(VALID_STATUS),
        "node_map": dict(NODE_BY_EVENT),
        "meeting_events": list(MEETING_EVENTS),
        "meeting_message_kinds": list(MEETING_MESSAGE_KINDS),
        "meeting_roles": list(MEETING_ROLES),
        "compatible_with": ["schema_version=1 (result/data)"],
    }


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Ledger schema v1.0.0.")
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.describe or not args.ledger:
        print(json.dumps(describe(), ensure_ascii=False, indent=2))
        return 0

    events = read_events(args.ledger)
    bad = [(index, validate(event)) for index, event in enumerate(events, start=1)]
    bad = [(index, problems) for index, problems in bad if problems]
    payload = {"ledger": str(args.ledger), "events": len(events), "invalid": len(bad),
               "problems": bad[:20], "schema": SCHEMA_SEMVER}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"schema {SCHEMA_SEMVER}: {len(events)} event, {len(bad)} khong doc duoc")
        for index, problems in bad[:10]:
            print(f"  dong {index}: {', '.join(problems)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
