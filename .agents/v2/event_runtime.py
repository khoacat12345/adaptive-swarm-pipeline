#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 5 event runtime: stable event identity and safe pagination.

Additive only: old ledger lines without event_id remain readable. New events
carry event_id/action_id and can be paged with a cursor.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class EventRecord:
    event_id: str
    run_id: str
    event_type: str
    timestamp: str
    status: str = "info"
    phase_id: str = ""
    action_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "2.1-adaptive",
            "event_id": self.event_id,
            "run_id": self.run_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "status": self.status,
            "phase_id": self.phase_id,
            "action_id": self.action_id,
            "data": dict(self.data),
        }


def make_event(
    *,
    run_id: str,
    event_type: str,
    status: str = "info",
    phase_id: str = "",
    action_id: str = "",
    data: dict[str, Any] | None = None,
) -> EventRecord:
    return EventRecord(
        event_id=f"evt_{uuid.uuid4().hex[:16]}",
        run_id=str(run_id),
        event_type=str(event_type),
        timestamp=_now(),
        status=str(status),
        phase_id=str(phase_id),
        action_id=str(action_id),
        data=dict(data or {}),
    )


def action_event(
    *,
    run_id: str,
    action: str,
    phase_id: str = "",
    data: dict[str, Any] | None = None,
) -> EventRecord:
    action_id = f"act_{uuid.uuid4().hex[:12]}"
    event = make_event(
        run_id=run_id, event_type="action", phase_id=phase_id,
        action_id=action_id, data={"action": action, **(data or {})},
    )
    return event


def observation_event(
    *,
    run_id: str,
    action_id: str,
    status: str,
    observation: str = "",
    phase_id: str = "",
) -> EventRecord:
    return make_event(
        run_id=run_id, event_type="observation", status=status,
        phase_id=phase_id, action_id=action_id,
        data={"observation": str(observation)[:4000]},
    )


def _legacy_id(event: dict[str, Any], index: int) -> str:
    raw = json.dumps(event, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "legacy_" + hashlib.sha256(raw + str(index).encode()).hexdigest()[:16]


def normalize_event(event: dict[str, Any], index: int = 0) -> EventRecord:
    """Add IDs to old ledger events without rewriting the source file."""
    return EventRecord(
        event_id=str(event.get("event_id") or _legacy_id(event, index)),
        run_id=str(event.get("run_id") or ""),
        event_type=str(event.get("event_type") or event.get("type") or "legacy"),
        timestamp=str(event.get("timestamp") or ""),
        status=str(event.get("status") or (event.get("result") or {}).get("status") or "info"),
        phase_id=str(event.get("phase_id") or ""),
        action_id=str(event.get("action_id") or ""),
        data=dict(event.get("data") or {}),
    )


@dataclass
class EventPage:
    items: list[EventRecord]
    next_cursor: str | None
    total_seen: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [item.as_dict() for item in self.items],
            "next_cursor": self.next_cursor,
            "total_seen": self.total_seen,
        }


class EventStore:
    """Append-only JSONL store with cursor pagination.

    Cursors are integer offsets encoded as strings. This keeps old files usable
    and avoids exposing file positions.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, event: EventRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event.as_dict(), ensure_ascii=False) + "\n")

    def read(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        timestamp_gte: str | None = None,
        timestamp_lt: str | None = None,
        strict_pagination: bool = False,
    ) -> EventPage:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be in 1..100")
        start = 0
        if cursor not in (None, ""):
            try:
                start = max(0, int(cursor))
            except ValueError as exc:
                raise ValueError(f"invalid cursor: {cursor!r}") from exc
        records: list[EventRecord] = []
        if self.path.exists():
            for index, line in enumerate(self.path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record = normalize_event(event, index)
                if timestamp_gte and record.timestamp < timestamp_gte:
                    continue
                if timestamp_lt and record.timestamp >= timestamp_lt:
                    continue
                records.append(record)
        page = records[start:start + limit]
        next_cursor = str(start + limit) if start + limit < len(records) else None
        if strict_pagination and next_cursor is None and start >= len(records):
            # Nothing more to read; an empty cursor page is valid only at the
            # true end, never for a partial/failed read.
            pass
        return EventPage(items=page, next_cursor=next_cursor, total_seen=len(records))

    def all(self) -> list[EventRecord]:
        result: list[EventRecord] = []
        cursor = None
        while True:
            page = self.read(cursor=cursor, limit=100)
            result.extend(page.items)
            if page.next_cursor is None:
                return result
            cursor = page.next_cursor
