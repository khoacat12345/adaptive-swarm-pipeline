#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1 - meeting data contracts.

The meeting protocol is deliberately small and deterministic:

  * one message object for every proposal / objection / rebuttal / vote;
  * evidence is structured, never free-form only;
  * the blackboard is append-only for messages;
  * secrets are scanned before a message can be stored or written to ledger.

The module does not import OpenCode, subprocess, or safe_writer.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable

try:  # `.agents` is normally on sys.path for the pipeline scripts.
    from secret_scan import scan_text
except Exception:  # pragma: no cover - only when imported outside .agents
    scan_text = None


MESSAGE_ROUNDS = (1, 2)
MESSAGE_KINDS = (
    "proposal",
    "objection",
    "evidence",
    "question",
    "rebuttal",
    "vote",
    "decision",
    "system",
)
KNOWN_ROLES = (
    "coder",
    "security",
    "qa",
    "perf",
    "docs",
    "lead",
    "moderator",
    "systembot",
)
ALLOWED_RECIPIENTS = KNOWN_ROLES + ("all",)
VOTE_VERDICTS = ("approve", "reject", "abstain", "error")
DECISION_VERDICTS = ("approve", "reject", "escalate")
SEVERITIES = ("info", "warn", "block")
MAX_BODY_CHARS = 12_000
MAX_EVIDENCE_NOTE_CHARS = 500
MAX_EVIDENCE_REFS = 20

_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class ProtocolError(ValueError):
    """A meeting message or blackboard operation is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _valid_iso(value: str) -> bool:
    return bool(_ISO_RE.match(str(value or "")))


def _scan(value: str) -> tuple[str, list[dict]]:
    if not value:
        return value, []
    if scan_text is None:
        return value, []
    try:
        return scan_text(value)
    except Exception:  # scanner must never make the protocol unusable
        return value, []


def _known_roles() -> set[str]:
    return set(ALLOWED_RECIPIENTS)


@dataclass
class EvidenceRef:
    """A verifiable pointer to a file line or a machine verification event."""

    file: str
    line_start: int | None = None
    line_end: int | None = None
    verify_event_id: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        self.file = _clean_text(self.file)
        for key in ("line_start", "line_end"):
            value = getattr(self, key)
            if value is not None:
                try:
                    setattr(self, key, int(value))
                except (TypeError, ValueError):
                    setattr(self, key, -1)
        self.verify_event_id = (_clean_text(self.verify_event_id)
                                if self.verify_event_id is not None else None)
        self.note = _clean_text(self.note) if self.note is not None else None

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.file:
            problems.append("evidence.file khong duoc rong")
            return problems
        path = Path(self.file)
        win_path = PureWindowsPath(self.file)
        if path.is_absolute() or win_path.is_absolute():
            problems.append("evidence.file phai la duong dan relative")
        if ".." in path.parts or ".." in win_path.parts:
            problems.append("evidence.file khong duoc chua '..'")
        if len(self.file) > 500:
            problems.append("evidence.file qua dai")
        if self.line_start is not None:
            if self.line_start < 1:
                problems.append("evidence.line_start phai >= 1")
            if self.line_end is not None and self.line_end < self.line_start:
                problems.append("evidence.line_end phai >= line_start")
        if self.line_end is not None and self.line_start is None:
            problems.append("evidence.line_end can line_start")
        if self.verify_event_id is not None and len(self.verify_event_id) > 200:
            problems.append("evidence.verify_event_id qua dai")
        if self.note is not None and len(self.note) > MAX_EVIDENCE_NOTE_CHARS:
            problems.append("evidence.note qua dai")
        return problems

    def sanitized(self) -> tuple["EvidenceRef", list[dict]]:
        note, findings = _scan(self.note or "")
        clean = replace(self, note=note if self.note is not None else None)
        return clean, findings

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "verify_event_id": self.verify_event_id,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceRef":
        if not isinstance(data, dict):
            raise ProtocolError("evidence phai la object")
        return cls(
            file=data.get("file", ""),
            line_start=data.get("line_start"),
            line_end=data.get("line_end"),
            verify_event_id=data.get("verify_event_id"),
            note=data.get("note"),
        )


@dataclass
class AgentMessage:
    """One persisted message in a meeting.

    `reply_to` is checked against the blackboard message registry by
    Blackboard.add_message(); `validate(known=...)` is available for callers
    that need to check it before adding.
    """

    run_id: str
    task_id: str
    sender: str
    recipients: list[str]
    kind: str
    body: str
    round: int = 1
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    reply_to: str | None = None
    timestamp: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.id = _clean_text(self.id) or uuid.uuid4().hex
        self.run_id = _clean_text(self.run_id)
        self.task_id = _clean_text(self.task_id)
        self.sender = _clean_text(self.sender).lower()
        self.recipients = [str(item).strip().lower() for item in _as_list(self.recipients)
                           if str(item).strip()]
        self.kind = _clean_text(self.kind).lower()
        self.body = str(self.body or "")
        try:
            self.round = int(self.round)
        except (TypeError, ValueError):
            self.round = -1
        self.evidence_refs = [
            item if isinstance(item, EvidenceRef) else EvidenceRef.from_dict(item)
            for item in _as_list(self.evidence_refs)
        ]
        self.reply_to = _clean_text(self.reply_to) if self.reply_to is not None else None
        self.timestamp = _clean_text(self.timestamp) or utc_now()

    def validate(self, *, known: dict[str, "AgentMessage"] | None = None) -> list[str]:
        problems: list[str] = []
        if not self.id:
            problems.append("id khong duoc rong")
        if not self.run_id:
            problems.append("run_id khong duoc rong")
        if not self.task_id:
            problems.append("task_id khong duoc rong")
        if not self.sender:
            problems.append("sender khong duoc rong")
        elif self.sender not in KNOWN_ROLES:
            problems.append(f"sender khong hop le: {self.sender}")
        if not self.recipients:
            problems.append("recipients khong duoc rong")
        else:
            unknown = [role for role in self.recipients if role not in ALLOWED_RECIPIENTS]
            if unknown:
                problems.append("recipients khong hop le: " + ", ".join(unknown))
            if self.sender in self.recipients:
                problems.append("sender khong duoc tu gui cho chinh minh")
        if self.round not in MESSAGE_ROUNDS:
            problems.append("round chi duoc la 1 hoac 2")
        if self.kind not in MESSAGE_KINDS:
            problems.append(f"kind khong hop le: {self.kind}")
        if len(self.body) > MAX_BODY_CHARS:
            problems.append("body qua dai")
        if len(self.evidence_refs) > MAX_EVIDENCE_REFS:
            problems.append("qua nhieu evidence_refs")
        for index, ref in enumerate(self.evidence_refs):
            for problem in ref.validate():
                problems.append(f"evidence_refs[{index}]: {problem}")
        if self.kind == "objection" and not self.evidence_refs:
            problems.append("objection phai co it nhat 1 evidence_ref")
        if self.reply_to is not None:
            if not self.reply_to:
                problems.append("reply_to khong duoc rong")
            elif self.reply_to == self.id:
                problems.append("reply_to khong duoc tro toi chinh message")
            elif known is not None:
                parent = known.get(self.reply_to)
                if parent is None:
                    problems.append("reply_to khong tro toi message da biet")
                elif parent.run_id != self.run_id or parent.task_id != self.task_id:
                    problems.append("reply_to khac run_id/task_id")
        if not _valid_iso(self.timestamp):
            problems.append("timestamp phai la ISO-8601 UTC")
        return problems

    def sanitized(self) -> tuple["AgentMessage", list[dict]]:
        body, findings = _scan(self.body)
        refs: list[EvidenceRef] = []
        for ref in self.evidence_refs:
            clean, ref_findings = ref.sanitized()
            refs.append(clean)
            findings.extend(ref_findings)
        clean = replace(self, body=body, evidence_refs=refs)
        return clean, findings

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "round": self.round,
            "sender": self.sender,
            "recipients": list(self.recipients),
            "kind": self.kind,
            "body": self.body,
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "reply_to": self.reply_to,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentMessage":
        if not isinstance(data, dict):
            raise ProtocolError("message phai la object")
        return cls(
            id=data.get("id") or uuid.uuid4().hex,
            run_id=data.get("run_id", ""),
            task_id=data.get("task_id", ""),
            round=data.get("round", 1),
            sender=data.get("sender", ""),
            recipients=data.get("recipients") or [],
            kind=data.get("kind", ""),
            body=data.get("body", ""),
            evidence_refs=data.get("evidence_refs") or [],
            reply_to=data.get("reply_to"),
            timestamp=data.get("timestamp") or utc_now(),
        )


@dataclass
class Claim:
    """A factual assertion made by a participant."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    sender: str = ""
    body: str = ""
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    round: int = 1
    timestamp: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.sender = _clean_text(self.sender).lower()
        self.body = str(self.body or "")
        self.evidence_refs = [
            item if isinstance(item, EvidenceRef) else EvidenceRef.from_dict(item)
            for item in _as_list(self.evidence_refs)
        ]
        self.round = int(self.round) if str(self.round).lstrip("-").isdigit() else -1

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.sender:
            problems.append("claim.sender khong duoc rong")
        if not self.body:
            problems.append("claim.body khong duoc rong")
        if self.round not in MESSAGE_ROUNDS:
            problems.append("claim.round chi duoc la 1 hoac 2")
        for ref in self.evidence_refs:
            problems.extend(ref.validate())
        return problems

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sender": self.sender,
            "body": self.body,
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "round": self.round,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Claim":
        data = data if isinstance(data, dict) else {}
        return cls(
            id=data.get("id") or uuid.uuid4().hex,
            sender=data.get("sender", ""),
            body=data.get("body", ""),
            evidence_refs=data.get("evidence_refs") or [],
            round=data.get("round", 1),
            timestamp=data.get("timestamp") or utc_now(),
        )


@dataclass
class Objection(Claim):
    """A claim that asks for a change; evidence is mandatory."""

    severity: str = "warn"
    target: str = "coder"

    def __post_init__(self) -> None:
        super().__post_init__()
        self.severity = _clean_text(self.severity).lower() or "warn"
        self.target = _clean_text(self.target).lower() or "coder"

    def validate(self) -> list[str]:
        problems = super().validate()
        if not self.evidence_refs:
            problems.append("objection phai co it nhat 1 evidence_ref")
        if self.severity not in SEVERITIES:
            problems.append(f"objection.severity khong hop le: {self.severity}")
        if self.target not in KNOWN_ROLES:
            problems.append(f"objection.target khong hop le: {self.target}")
        return problems

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update({"severity": self.severity, "target": self.target})
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Objection":
        data = data if isinstance(data, dict) else {}
        return cls(
            id=data.get("id") or uuid.uuid4().hex,
            sender=data.get("sender", ""),
            body=data.get("body", ""),
            evidence_refs=data.get("evidence_refs") or [],
            round=data.get("round", 1),
            timestamp=data.get("timestamp") or utc_now(),
            severity=data.get("severity", "warn"),
            target=data.get("target", "coder"),
        )


@dataclass
class Vote:
    role: str
    verdict: str
    reasons: list[str] = field(default_factory=list)
    round: int = 1
    timestamp: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.role = _clean_text(self.role).lower()
        self.verdict = _clean_text(self.verdict).lower()
        self.reasons = [str(item) for item in _as_list(self.reasons) if str(item).strip()]
        self.round = int(self.round) if str(self.round).lstrip("-").isdigit() else -1

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.role not in KNOWN_ROLES:
            problems.append(f"vote.role khong hop le: {self.role}")
        if self.verdict not in VOTE_VERDICTS:
            problems.append(f"vote.verdict khong hop le: {self.verdict}")
        if self.round not in MESSAGE_ROUNDS:
            problems.append("vote.round chi duoc la 1 hoac 2")
        return problems

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "round": self.round,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Vote":
        data = data if isinstance(data, dict) else {}
        return cls(
            role=data.get("role", ""),
            verdict=data.get("verdict", ""),
            reasons=data.get("reasons") or [],
            round=data.get("round", 1),
            timestamp=data.get("timestamp") or utc_now(),
        )


@dataclass
class Decision:
    verdict: str
    reasons: list[str] = field(default_factory=list)
    vetoes: list[str] = field(default_factory=list)
    dissent: list[str] = field(default_factory=list)
    round: int = 2
    timestamp: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.verdict = _clean_text(self.verdict).lower()
        self.reasons = [str(item) for item in _as_list(self.reasons) if str(item).strip()]
        self.vetoes = [str(item) for item in _as_list(self.vetoes) if str(item).strip()]
        self.dissent = [str(item) for item in _as_list(self.dissent) if str(item).strip()]
        self.round = int(self.round) if str(self.round).lstrip("-").isdigit() else -1

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.verdict not in DECISION_VERDICTS:
            problems.append(f"decision.verdict khong hop le: {self.verdict}")
        if self.round not in MESSAGE_ROUNDS:
            problems.append("decision.round chi duoc la 1 hoac 2")
        return problems

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "vetoes": list(self.vetoes),
            "dissent": list(self.dissent),
            "round": self.round,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Decision":
        data = data if isinstance(data, dict) else {}
        return cls(
            verdict=data.get("verdict", ""),
            reasons=data.get("reasons") or [],
            vetoes=data.get("vetoes") or [],
            dissent=data.get("dissent") or [],
            round=data.get("round", 2),
            timestamp=data.get("timestamp") or utc_now(),
        )


@dataclass
class Dissent:
    role: str
    reason: str
    round: int = 2
    timestamp: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.role = _clean_text(self.role).lower()
        self.reason = str(self.reason or "")
        self.round = int(self.round) if str(self.round).lstrip("-").isdigit() else -1

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.role not in KNOWN_ROLES:
            problems.append(f"dissent.role khong hop le: {self.role}")
        if not self.reason:
            problems.append("dissent.reason khong duoc rong")
        if self.round not in MESSAGE_ROUNDS:
            problems.append("dissent.round chi duoc la 1 hoac 2")
        return problems

    def to_dict(self) -> dict:
        return {"role": self.role, "reason": self.reason,
                "round": self.round, "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, data: dict) -> "Dissent":
        data = data if isinstance(data, dict) else {}
        return cls(
            role=data.get("role", ""),
            reason=data.get("reason", ""),
            round=data.get("round", 2),
            timestamp=data.get("timestamp") or utc_now(),
        )
