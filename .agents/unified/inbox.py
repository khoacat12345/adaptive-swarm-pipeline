#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F03: decision inbox — worker asks, human replies, the run unblocks.

The inbox is an append-only NDJSON log in the run directory.  Every message has
a deterministic id (``MSG-0001``), so a CLI reply can address it after a restart
without any in-memory session.

Message types: ``question``, ``answer``, ``approval``, ``rejection``,
``escalation``, ``blocker``.

Gates: ``usage_approval``, ``review_approval``, ``merge_approval``,
``destructive_action``, ``scope_change``.  A gate can never be bypassed by a
tier override: ``GateRequired`` is raised instead, and the caller decides how to
surface it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from v2.errors import V2Error

SCHEMA_VERSION = 1
INBOX_FILE = "inbox.ndjson"
MAX_QUESTION = 4_000
MAX_ANSWER = 8_000

MESSAGE_TYPES = ("question", "answer", "approval", "rejection", "escalation", "blocker")
GATE_TYPES = (
    "usage_approval",
    "review_approval",
    "merge_approval",
    "destructive_action",
    "scope_change",
)
OPEN_STATUSES = ("pending", "escalated")
CLOSED_STATUSES = ("answered", "cancelled", "expired")

_ID = re.compile(r"^MSG-(\d{4,})$")


class InboxError(V2Error):
    code = "INBOX_ERROR"


class MessageNotFound(InboxError):
    code = "INBOX_MESSAGE_NOT_FOUND"


class GateRequired(InboxError):
    code = "INBOX_GATE_REQUIRED"


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass
class DecisionMessage:
    id: str
    run_id: str
    kind: str = "question"
    question: str = ""
    status: str = "pending"
    blocking: bool = True
    gate: str | None = None
    task_id: str | None = None
    phase_id: str | None = None
    author: str = "worker"
    options: list[str] = field(default_factory=list)
    answer: str | None = None
    answered_by: str | None = None
    created_at: str = ""
    answered_at: str | None = None
    expires_at: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = str(self.id or "").strip()
        self.run_id = str(self.run_id or "")
        self.kind = str(self.kind or "question")
        if self.kind not in MESSAGE_TYPES:
            self.kind = "question"
        self.status = str(self.status or "pending")
        self.gate = str(self.gate) if self.gate else None
        if self.gate and self.gate not in GATE_TYPES:
            raise InboxError(f"unknown gate: {self.gate!r}")
        self.question = str(self.question or "")[:MAX_QUESTION]
        if self.answer is not None:
            self.answer = str(self.answer)[:MAX_ANSWER]
        self.options = [str(item) for item in (self.options or [])]
        self.created_at = self.created_at or utc_now_iso()
        if not isinstance(self.evidence, list):
            self.evidence = []
        if not isinstance(self.metadata, dict):
            self.metadata = dict(self.metadata or {})

    @property
    def open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def blocks_run(self) -> bool:
        return bool(self.blocking) and self.open

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "run_id": self.run_id,
            "kind": self.kind,
            "question": self.question,
            "status": self.status,
            "blocking": bool(self.blocking),
            "gate": self.gate,
            "task_id": self.task_id,
            "phase_id": self.phase_id,
            "author": self.author,
            "options": list(self.options),
            "answer": self.answer,
            "answered_by": self.answered_by,
            "created_at": self.created_at,
            "answered_at": self.answered_at,
            "expires_at": self.expires_at,
            "evidence": [dict(item) for item in self.evidence[-10:]],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionMessage":
        if not isinstance(data, Mapping):
            raise InboxError("message must be an object")
        return cls(
            id=str(data.get("id") or ""),
            run_id=str(data.get("run_id") or ""),
            kind=str(data.get("kind") or "question"),
            question=str(data.get("question") or ""),
            status=str(data.get("status") or "pending"),
            blocking=bool(data.get("blocking", True)),
            gate=data.get("gate"),
            task_id=data.get("task_id"),
            phase_id=data.get("phase_id"),
            author=str(data.get("author") or "worker"),
            options=list(data.get("options") or []),
            answer=data.get("answer"),
            answered_by=data.get("answered_by"),
            created_at=str(data.get("created_at") or ""),
            answered_at=data.get("answered_at"),
            expires_at=data.get("expires_at"),
            evidence=list(data.get("evidence") or []),
            metadata=dict(data.get("metadata") or {}),
        )


class DecisionInbox:
    """Append-only decision queue for one run."""

    def __init__(self, run_dir: str | Path, *, run_id: str = "") -> None:
        self.run_dir = Path(run_dir)
        self.run_id = str(run_id or self.run_dir.name)
        self.path = self.run_dir / INBOX_FILE

    # -- io ------------------------------------------------------------
    def load(self) -> list[DecisionMessage]:
        if not self.path.exists():
            return []
        rows: list[DecisionMessage] = []
        latest: dict[str, DecisionMessage] = {}
        order: list[str] = []
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = DecisionMessage.from_dict(json.loads(line))
            except (json.JSONDecodeError, InboxError, TypeError, ValueError):
                continue
            if not item.id:
                continue
            if item.id not in latest:
                order.append(item.id)
            latest[item.id] = item
        for message_id in order:
            rows.append(latest[message_id])
        return rows

    def _append(self, message: DecisionMessage) -> DecisionMessage:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(message.as_dict(), ensure_ascii=False, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return message

    # -- ids -----------------------------------------------------------
    def _next_id(self) -> str:
        highest = 0
        for message in self.load():
            match = _ID.match(message.id)
            if match:
                highest = max(highest, int(match.group(1)))
        return f"MSG-{highest + 1:04d}"

    # -- api -----------------------------------------------------------
    def ask(
        self,
        question: str,
        *,
        kind: str = "question",
        blocking: bool = True,
        gate: str | None = None,
        task_id: str | None = None,
        phase_id: str | None = None,
        author: str = "worker",
        options: Iterable[str] = (),
        expires_at: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> DecisionMessage:
        text = str(question or "").strip()
        if not text:
            raise InboxError("question must not be empty")
        if gate and gate not in GATE_TYPES:
            raise InboxError(f"unknown gate: {gate!r}")
        message = DecisionMessage(
            id=self._next_id(),
            run_id=self.run_id,
            kind=kind,
            question=text[:MAX_QUESTION],
            status="escalated" if kind == "escalation" else "pending",
            blocking=bool(blocking),
            gate=gate,
            task_id=task_id,
            phase_id=phase_id,
            author=str(author or "worker"),
            options=list(options),
            expires_at=expires_at,
            metadata=dict(metadata or {}),
        )
        return self._append(message)

    def get(self, message_id: str) -> DecisionMessage:
        wanted = str(message_id or "").strip()
        for message in self.load():
            if message.id == wanted:
                return message
        raise MessageNotFound(f"no such message: {message_id}")

    def reply(
        self,
        message_id: str,
        answer: str,
        *,
        answered_by: str = "operator",
        kind: str | None = None,
        evidence: Iterable[Mapping[str, Any]] = (),
    ) -> DecisionMessage:
        text = str(answer or "").strip()
        if not text:
            raise InboxError("answer must not be empty")
        message = self.get(message_id)
        if not message.open:
            raise InboxError(f"message {message.id} is already {message.status}")
        final_kind = kind or {
            "question": "answer",
            "escalation": "answer",
            "blocker": "answer",
        }.get(message.kind, "answer")
        if final_kind not in MESSAGE_TYPES:
            final_kind = "answer"
        message.status = "answered"
        message.answer = text[:MAX_ANSWER]
        message.answered_by = str(answered_by or "operator")
        message.answered_at = utc_now_iso()
        if message.kind != "question":
            message.kind = final_kind
        for item in evidence:
            if isinstance(item, Mapping):
                message.evidence.append(dict(item))
        return self._append(message)

    def cancel(self, message_id: str, *, reason: str = "cancelled") -> DecisionMessage:
        message = self.get(message_id)
        if not message.open:
            raise InboxError(f"message {message.id} is already {message.status}")
        message.status = "cancelled"
        message.answer = None
        message.answered_by = None
        message.answered_at = utc_now_iso()
        message.metadata = {**message.metadata, "cancel_reason": str(reason)}
        return self._append(message)

    # -- queries -------------------------------------------------------
    def pending(self) -> list[DecisionMessage]:
        return [message for message in self.load() if message.open]

    def answered(self) -> list[DecisionMessage]:
        return [message for message in self.load() if message.status == "answered"]

    def blocking(self) -> list[DecisionMessage]:
        return [message for message in self.pending() if message.blocking]

    def needs_attention(self) -> bool:
        return bool(self.blocking())

    def gate_pending(self, gate: str) -> bool:
        return any(message.gate == str(gate) and message.open for message in self.load())

    def assert_gate_cleared(self, gate: str) -> None:
        """Raise when a blocking gate is still open (no bypass, ever)."""
        for message in self.load():
            if message.gate == str(gate) and message.blocks_run:
                raise GateRequired(
                    f"gate {gate} is blocked by {message.id}: {message.question[:200]}"
                )

    def summary(self) -> dict[str, Any]:
        rows = self.load()
        pending = [message for message in rows if message.open]
        return {
            "run_id": self.run_id,
            "total": len(rows),
            "pending": len(pending),
            "blocking": sum(1 for message in pending if message.blocking),
            "needs_attention": any(message.blocking for message in pending),
            "gates": sorted({message.gate for message in rows if message.gate}),
            "open_gates": sorted({message.gate for message in pending if message.gate}),
            "items": [message.as_dict() for message in rows[-50:]],
            "pending_items": [message.as_dict() for message in pending[-20:]],
        }


# ---------------------------------------------------------------------------
# Helpers shared by CLI / API / state projection
# ---------------------------------------------------------------------------
def inbox_for_run(root: str | Path, run_id: str, *,
                  state_root: str | Path | None = None) -> DecisionInbox:
    from unified.state import state_root_for

    run_dir = state_root_for(root, state_root) / "runs" / str(run_id)
    return DecisionInbox(run_dir, run_id=str(run_id))


def inbox_summary(root: str | Path, run_id: str, *,
                  state_root: str | Path | None = None) -> dict[str, Any]:
    return inbox_for_run(root, run_id, state_root=state_root).summary()


def find_message(root: str | Path, message_id: str, *,
                 state_root: str | Path | None = None) -> tuple[DecisionInbox, DecisionMessage]:
    """Locate a message id across every run in the state root."""
    from unified.state import state_root_for

    runs_dir = state_root_for(root, state_root) / "runs"
    wanted = str(message_id or "").strip()
    if not runs_dir.exists():
        raise MessageNotFound(f"no such message: {message_id}")
    for run_dir in sorted(path for path in runs_dir.iterdir() if path.is_dir()):
        inbox = DecisionInbox(run_dir, run_id=run_dir.name)
        try:
            return inbox, inbox.get(wanted)
        except MessageNotFound:
            continue
    raise MessageNotFound(f"no such message: {message_id}")


def inbox_events(inbox: DecisionInbox) -> list[dict[str, Any]]:
    """Event payloads for the run event log."""
    return [
        {
            "event_type": f"decision.{message.kind}",
            "data": {
                "message_id": message.id,
                "question": message.question,
                "status": message.status,
                "blocking": message.blocking,
                "gate": message.gate,
                "answer": message.answer,
                "answered_by": message.answered_by,
                "task_id": message.task_id,
            },
        }
        for message in inbox.load()
    ]
