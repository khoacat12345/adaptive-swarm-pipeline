#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1 - shared blackboard for meeting mode.

The blackboard is the single structured memory shared by the moderator and
participants.  Messages are append-only.  Claims, objections, votes and
decisions are stored separately so policy code never has to parse free text.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import RLock
from typing import Iterator

from .protocol import (
    AgentMessage,
    Claim,
    Decision,
    Dissent,
    EvidenceRef,
    Objection,
    ProtocolError,
    Vote,
    _scan,
    _as_list,
)


def _coerce_claim(value) -> Claim:
    if isinstance(value, Claim):
        return value
    if isinstance(value, dict):
        return Claim.from_dict(value)
    raise ProtocolError("claim phai la Claim hoac dict")


def _coerce_objection(value) -> Objection:
    if isinstance(value, Objection):
        return value
    if isinstance(value, dict):
        return Objection.from_dict(value)
    raise ProtocolError("objection phai la Objection hoac dict")


def _coerce_vote(value) -> Vote:
    if isinstance(value, Vote):
        return value
    if isinstance(value, dict):
        return Vote.from_dict(value)
    raise ProtocolError("vote phai la Vote hoac dict")


def _coerce_decision(value) -> Decision:
    if isinstance(value, Decision):
        return value
    if isinstance(value, dict):
        return Decision.from_dict(value)
    raise ProtocolError("decision phai la Decision hoac dict")


def _coerce_dissent(value) -> Dissent:
    if isinstance(value, Dissent):
        return value
    if isinstance(value, dict):
        return Dissent.from_dict(value)
    raise ProtocolError("dissent phai la Dissent hoac dict")


@dataclass
class Blackboard:
    """Structured shared state for one meeting.

    `messages` is the transcript.  All other collections are indexes for
    policy/UI; they do not replace the transcript.
    """

    brief: str = ""
    diff: str = ""
    verify_result: dict = field(default_factory=dict)
    claims: list[Claim] = field(default_factory=list)
    objections: list[Objection] = field(default_factory=list)
    votes: dict[str, Vote] = field(default_factory=dict)
    decisions: list[Decision] = field(default_factory=list)
    dissent: list[Dissent] = field(default_factory=list)
    messages: list[AgentMessage] = field(default_factory=list)
    vote_history: list[Vote] = field(default_factory=list)
    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.brief = str(self.brief or "")
        self.diff = str(self.diff or "")
        self.verify_result = dict(self.verify_result or {})
        self.claims = [_coerce_claim(item) for item in _as_list(self.claims)]
        self.objections = [_coerce_objection(item) for item in _as_list(self.objections)]
        self.votes = {str(role): _coerce_vote(vote)
                      for role, vote in (self.votes or {}).items()}
        self.decisions = [_coerce_decision(item) for item in _as_list(self.decisions)]
        self.dissent = [_coerce_dissent(item) for item in _as_list(self.dissent)]
        self.messages = [
            item if isinstance(item, AgentMessage) else AgentMessage.from_dict(item)
            for item in _as_list(self.messages)
        ]
        self.vote_history = [_coerce_vote(item) for item in _as_list(self.vote_history)]

    @contextmanager
    def locked(self) -> Iterator["Blackboard"]:
        with self._lock:
            yield self

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    def message_by_id(self, message_id: str) -> AgentMessage | None:
        with self._lock:
            for message in self.messages:
                if message.id == message_id:
                    return message
        return None

    def _message_index(self) -> dict[str, AgentMessage]:
        return {message.id: message for message in self.messages}

    def add_message(self, message: AgentMessage | dict) -> list[dict]:
        """Validate, sanitize, link-check and append one message.

        Returns secret-scanner findings.  The stored message is the sanitized
        copy, never the raw body.
        """
        if not isinstance(message, AgentMessage):
            message = AgentMessage.from_dict(message)
        with self._lock:
            if any(item.id == message.id for item in self.messages):
                raise ProtocolError(f"message id da ton tai: {message.id}")
            clean, findings = message.sanitized()
            problems = clean.validate(known=self._message_index())
            if problems:
                raise ProtocolError("; ".join(problems))
            self.messages.append(clean)
            return findings

    # ------------------------------------------------------------------
    # Claims / objections
    # ------------------------------------------------------------------
    def add_claim(self, claim: Claim | dict) -> list[dict]:
        item = _coerce_claim(claim)
        problems = item.validate()
        if problems:
            raise ProtocolError("; ".join(problems))
        body, findings = _scan(item.body)
        if body != item.body:
            item = Claim(
                id=item.id, sender=item.sender, body=body,
                evidence_refs=item.evidence_refs, round=item.round,
                timestamp=item.timestamp,
            )
        with self._lock:
            self.claims.append(item)
        return findings

    def add_objection(self, objection: Objection | dict) -> list[dict]:
        item = _coerce_objection(objection)
        problems = item.validate()
        if problems:
            raise ProtocolError("; ".join(problems))
        body, findings = _scan(item.body)
        if body != item.body:
            item = Objection(
                id=item.id, sender=item.sender, body=body,
                evidence_refs=item.evidence_refs, round=item.round,
                timestamp=item.timestamp, severity=item.severity,
                target=item.target,
            )
        with self._lock:
            self.objections.append(item)
        return findings

    # ------------------------------------------------------------------
    # Votes / decisions / dissent
    # ------------------------------------------------------------------
    def record_vote(self, vote: Vote | dict, *, replace_previous: bool = True) -> None:
        item = _coerce_vote(vote)
        problems = item.validate()
        if problems:
            raise ProtocolError("; ".join(problems))
        with self._lock:
            if not replace_previous and item.role in self.votes:
                raise ProtocolError(f"role da vote: {item.role}")
            self.votes[item.role] = item
            self.vote_history.append(item)

    def add_decision(self, decision: Decision | dict) -> None:
        item = _coerce_decision(decision)
        problems = item.validate()
        if problems:
            raise ProtocolError("; ".join(problems))
        with self._lock:
            self.decisions.append(item)

    def add_dissent(self, dissent: Dissent | dict) -> None:
        item = _coerce_dissent(dissent)
        problems = item.validate()
        if problems:
            raise ProtocolError("; ".join(problems))
        with self._lock:
            self.dissent.append(item)

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------
    def objections_for(self, role: str) -> list[Objection]:
        role = str(role or "").lower()
        with self._lock:
            return [item for item in self.objections
                    if item.sender == role or item.target == role]

    def vote_snapshot(self) -> dict[str, str]:
        with self._lock:
            return {role: vote.verdict for role, vote in self.votes.items()}

    def latest_decision(self) -> Decision | None:
        with self._lock:
            return self.decisions[-1] if self.decisions else None

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "brief": self.brief,
                "diff": self.diff,
                "verify_result": dict(self.verify_result),
                "claims": [item.to_dict() for item in self.claims],
                "objections": [item.to_dict() for item in self.objections],
                "votes": {role: vote.to_dict() for role, vote in self.votes.items()},
                "vote_history": [item.to_dict() for item in self.vote_history],
                "decisions": [item.to_dict() for item in self.decisions],
                "dissent": [item.to_dict() for item in self.dissent],
                "messages": [item.to_dict() for item in self.messages],
            }

    def to_dict(self) -> dict:
        return self.snapshot()

    def to_json(self) -> str:
        return json.dumps(self.snapshot(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict) -> "Blackboard":
        data = data if isinstance(data, dict) else {}
        return cls(
            brief=data.get("brief", ""),
            diff=data.get("diff", ""),
            verify_result=data.get("verify_result") or {},
            claims=data.get("claims") or [],
            objections=data.get("objections") or [],
            votes=data.get("votes") or {},
            decisions=data.get("decisions") or [],
            dissent=data.get("dissent") or [],
            messages=data.get("messages") or [],
            vote_history=data.get("vote_history") or [],
        )

    @classmethod
    def from_json(cls, text: str) -> "Blackboard":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"blackboard JSON khong hop le: {exc}") from exc
        return cls.from_dict(data)

    # ------------------------------------------------------------------
    # Compatibility/UI helpers
    # ------------------------------------------------------------------
    def as_ledger_data(self) -> dict:
        """Small summary suitable for a ledger event (not full transcript)."""
        with self._lock:
            return {
                "brief": self.brief[:500],
                "verify_exit": (self.verify_result or {}).get("exit_code"),
                "messages": len(self.messages),
                "claims": len(self.claims),
                "objections": len(self.objections),
                "votes": self.vote_snapshot(),
                "decision": (self.latest_decision().to_dict()
                             if self.latest_decision() else None),
                "dissent": [item.to_dict() for item in self.dissent],
            }
