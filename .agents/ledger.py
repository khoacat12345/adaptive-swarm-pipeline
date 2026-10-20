#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Event Ledger v1 — nhat ky su kien append-only cho Adaptive Swarm Pipeline.

Dac diem:
  - JSON Lines: moi dong la 1 event doc lap -> de doc bang mat, de stream cho GUI sau nay.
  - Append-only: chi ghi them, khong sua, khong xoa.
  - Co lock file -> nhieu process ghi tuan tu, khong dan xen mot dong.
  - Co validate schema -> phat hien dong hong / thieu field.

Schema 1 dong event:
  {
    "schema_version": 1,
    "event_id": "a1b2c3d4e5f6",
    "timestamp": "2026-10-20T10:00:00.123Z",   # ISO-8601 UTC
    "run_id": "run_20261020T100000_ab12cd",
    "event_type": "system_bot.run",
    "duration_ms": 123,
    "usage": {"in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0},
    "result": {"status": "pass", "error": null},
    "data": { ... tuy y, khong chua secret ... }
  }

CLI:
  python ledger.py path
  python ledger.py tail -n 10
  python ledger.py validate
  python ledger.py stats
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent

SCHEMA_VERSION = 1
LEDGER_FILENAME = "ledger.jsonl"
LOCK_FILENAME = ".ledger.lock"

LOCK_TIMEOUT_SECONDS = 10.0
LOCK_STALE_SECONDS = 60.0

VALID_STATUS = {
    "info", "ok", "pass", "fail", "error", "blocked", "timeout", "skipped",
}

REQUIRED_EVENT_FIELDS = (
    "schema_version", "event_id", "timestamp", "run_id",
    "event_type", "duration_ms", "usage", "result",
)
REQUIRED_USAGE_FIELDS = ("in_tokens", "out_tokens", "cache_read_tokens")


# --------------------------------------------------------------------------
# Thoi gian / ID
# --------------------------------------------------------------------------

def utc_now_iso() -> str:
    """ISO-8601 UTC voi mili-giay, hau to Z (on dinh, sort duoc bang string)."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def new_run_id(prefix: str = "run") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:6]}"


# --------------------------------------------------------------------------
# Duong dan
# --------------------------------------------------------------------------

def resolve_agents_dir(root=None, agents_dir=None) -> Path:
    """Tra ve thu muc .agents/ tuong ung voi project root."""
    if agents_dir:
        return Path(agents_dir).resolve()
    if root:
        base = Path(root).resolve()
        if base.name == ".agents":
            return base
        return base / ".agents"
    return AGENTS_DIR


def resolve_ledger_path(root=None, agents_dir=None, path=None) -> Path:
    if path:
        return Path(path).resolve()
    return resolve_agents_dir(root=root, agents_dir=agents_dir) / LEDGER_FILENAME


# --------------------------------------------------------------------------
# Lock (chong ghi dan xen giua nhieu process)
# --------------------------------------------------------------------------

@contextmanager
def _file_lock(lock_path: Path, timeout: float = LOCK_TIMEOUT_SECONDS):
    deadline = time.time() + timeout
    acquired = False
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"{os.getpid()} {time.time():.3f}".encode("ascii"))
            finally:
                os.close(fd)
            acquired = True
            break
        except FileExistsError:
            # Lock cu bi treo -> don dep
            try:
                if time.time() - lock_path.stat().st_mtime > LOCK_STALE_SECONDS:
                    lock_path.unlink()
                    continue
            except OSError:
                pass
            if time.time() > deadline:
                break
            time.sleep(0.02)

    try:
        yield acquired
    finally:
        if acquired:
            try:
                lock_path.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------
# Tao event
# --------------------------------------------------------------------------

def make_event(
    event_type: str,
    *,
    run_id: str | None = None,
    duration_ms: int = 0,
    usage: dict | None = None,
    result: dict | None = None,
    data: dict | None = None,
) -> dict:
    usage = usage or {}
    result = result or {}
    status = result.get("status", "info")
    if status not in VALID_STATUS:
        status = "info"
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": uuid.uuid4().hex[:12],
        "timestamp": utc_now_iso(),
        "run_id": run_id or "run_unbound",
        "event_type": event_type,
        "duration_ms": int(duration_ms or 0),
        "usage": {
            "in_tokens": int(usage.get("in_tokens", 0) or 0),
            "out_tokens": int(usage.get("out_tokens", 0) or 0),
            "cache_read_tokens": int(usage.get("cache_read_tokens", 0) or 0),
        },
        "result": {
            "status": status,
            "error": result.get("error"),
        },
        "data": data or {},
    }


def append_event(
    event_type: str,
    *,
    run_id: str | None = None,
    duration_ms: int = 0,
    usage: dict | None = None,
    result: dict | None = None,
    data: dict | None = None,
    root=None,
    agents_dir=None,
    path=None,
) -> dict:
    """Ghi 1 event vao ledger. Tra ve event vua ghi."""
    event = make_event(
        event_type,
        run_id=run_id,
        duration_ms=duration_ms,
        usage=usage,
        result=result,
        data=data,
    )
    ledger_path = resolve_ledger_path(root=root, agents_dir=agents_dir, path=path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_path.parent / LOCK_FILENAME

    line = json.dumps(event, ensure_ascii=False, sort_keys=False)
    with _file_lock(lock_path):
        with open(ledger_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    return event


# --------------------------------------------------------------------------
# Doc / validate
# --------------------------------------------------------------------------

def read_events(
    root=None,
    agents_dir=None,
    path=None,
    *,
    collect_errors: bool = False,
):
    """
    Doc ledger. Mac dinh tra ve list[dict] cac event hop le.
    Neu collect_errors=True -> tra ve (events, errors).
    """
    ledger_path = resolve_ledger_path(root=root, agents_dir=agents_dir, path=path)
    events: list[dict] = []
    errors: list[dict] = []

    if not ledger_path.exists():
        return (events, errors) if collect_errors else events

    with open(ledger_path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append({"line": lineno, "reason": f"JSON khong hop le: {exc}"})
                continue
            if not isinstance(event, dict):
                errors.append({"line": lineno, "reason": "JSON khong phai object"})
                continue
            events.append(event)

    return (events, errors) if collect_errors else events


def validate_event(event: dict) -> list[str]:
    """Tra ve danh sach loi cua 1 event (rong = hop le)."""
    problems: list[str] = []
    if not isinstance(event, dict):
        return ["event khong phai dict"]

    for field in REQUIRED_EVENT_FIELDS:
        if field not in event:
            problems.append(f"thieu field '{field}'")

    if event.get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"schema_version khong khop (co {event.get('schema_version')!r}, can {SCHEMA_VERSION})"
        )

    ts = event.get("timestamp")
    if isinstance(ts, str):
        try:
            parse_iso(ts)
        except ValueError:
            problems.append(f"timestamp khong parse duoc: {ts!r}")
    else:
        problems.append("timestamp khong phai string")

    duration = event.get("duration_ms")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
        problems.append("duration_ms phai la int >= 0")

    run_id = event.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        problems.append("run_id phai la string khong rong")

    event_type = event.get("event_type")
    if not isinstance(event_type, str) or not event_type:
        problems.append("event_type phai la string khong rong")

    usage = event.get("usage")
    if not isinstance(usage, dict):
        problems.append("usage phai la dict")
    else:
        for field in REQUIRED_USAGE_FIELDS:
            value = usage.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(f"usage.{field} phai la int >= 0")

    result = event.get("result")
    if not isinstance(result, dict):
        problems.append("result phai la dict")
    else:
        status = result.get("status")
        if status not in VALID_STATUS:
            problems.append(f"result.status khong hop le: {status!r}")

    return problems


def validate_ledger(root=None, agents_dir=None, path=None) -> dict:
    """Validate toan bo ledger -> bao cao tong hop."""
    ledger_path = resolve_ledger_path(root=root, agents_dir=agents_dir, path=path)
    events, json_errors = read_events(root=root, agents_dir=agents_dir, path=path,
                                      collect_errors=True)
    problems: list[dict] = list(json_errors)
    for index, event in enumerate(events, start=1):
        for reason in validate_event(event):
            problems.append({"line": index, "event_id": event.get("event_id"), "reason": reason})
    return {
        "ledger": str(ledger_path),
        "exists": ledger_path.exists(),
        "total_events": len(events),
        "invalid_events": len(problems),
        "problems": problems,
    }


def stats(root=None, agents_dir=None, path=None) -> dict:
    """Thong ke nhanh: dem theo event_type, theo status, tong token, tong thoi gian."""
    events = read_events(root=root, agents_dir=agents_dir, path=path)
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    total_ms = 0
    in_tokens = out_tokens = cache_tokens = 0
    run_ids: set[str] = set()

    for event in events:
        by_type[event.get("event_type", "?")] = by_type.get(event.get("event_type", "?"), 0) + 1
        status = (event.get("result") or {}).get("status", "?")
        by_status[status] = by_status.get(status, 0) + 1
        total_ms += int(event.get("duration_ms", 0) or 0)
        usage = event.get("usage") or {}
        in_tokens += int(usage.get("in_tokens", 0) or 0)
        out_tokens += int(usage.get("out_tokens", 0) or 0)
        cache_tokens += int(usage.get("cache_read_tokens", 0) or 0)
        run_ids.add(event.get("run_id", "?"))

    return {
        "total_events": len(events),
        "runs": sorted(run_ids),
        "by_type": dict(sorted(by_type.items())),
        "by_status": dict(sorted(by_status.items())),
        "total_duration_ms": total_ms,
        "usage": {
            "in_tokens": in_tokens,
            "out_tokens": out_tokens,
            "cache_read_tokens": cache_tokens,
        },
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _cmd_path(args) -> int:
    print(resolve_ledger_path(root=args.root, path=args.ledger))
    return 0


def _cmd_tail(args) -> int:
    events, errors = read_events(root=args.root, path=args.ledger, collect_errors=True)
    tail = events[-args.n:]
    for event in tail:
        status = (event.get("result") or {}).get("status", "?")
        print(
            f"{event.get('timestamp')}  {status:<8} {event.get('event_type'):<26} "
            f"run={event.get('run_id')}  {event.get('duration_ms')}ms"
        )
    if errors:
        print(f"-- {len(errors)} dong loi --", file=sys.stderr)
        for err in errors:
            print(f"   line {err['line']}: {err['reason']}", file=sys.stderr)
    return 0


def _cmd_validate(args) -> int:
    report = validate_ledger(root=args.root, path=args.ledger)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["invalid_events"] == 0 else 1


def _cmd_stats(args) -> int:
    print(json.dumps(stats(root=args.root, path=args.ledger), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Event Ledger v1 (append-only JSONL)")
    parser.add_argument("--root", default=None, help="project root (ledger o <root>/.agents/)")
    parser.add_argument("--ledger", default=None, help="duong dan ledger tuy chinh")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("path", help="in duong dan ledger")
    p_tail = sub.add_parser("tail", help="in n dong cuoi")
    p_tail.add_argument("-n", type=int, default=10)
    sub.add_parser("validate", help="kiem tra toan ven schema")
    sub.add_parser("stats", help="thong ke ledger")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "path": _cmd_path,
        "tail": _cmd_tail,
        "validate": _cmd_validate,
        "stats": _cmd_stats,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
