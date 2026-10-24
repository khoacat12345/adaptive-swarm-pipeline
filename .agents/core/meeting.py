#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 2 - deterministic meeting orchestrator.

The orchestrator owns Round 1 only in this phase.  It deliberately has no
filesystem writer: reviewers return findings, the recipe/Lead remains the
single writer.

Design rules:
  * reviewers never see each other's findings in Round 1;
  * all reviewer calls are independent (optional thread pool);
  * every finding becomes a persisted vote + meeting.message;
  * an objection must carry evidence, otherwise it is recorded as a vote
    (never silently promoted to an objection);
  * no model call is allowed after a failed machine verify.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .blackboard import Blackboard
from .protocol import (
    AgentMessage,
    Decision,
    EvidenceRef,
    ProtocolError,
    Vote,
)


EventSink = Callable[[str, dict, str], None]


@dataclass
class MeetingContext:
    """Everything a reviewer is allowed to see in a round."""

    work_order: Any
    brief: str = ""
    diff: str = ""
    new_content: str = ""
    old_content: str = ""
    level: int = 2
    verify_result: dict = field(default_factory=dict)
    diffs: dict | None = None
    new_contents: dict | None = None
    old_contents: dict | None = None

    def call_reviewer(self, reviewer, *, sink=None) -> dict:
        if self.diffs is not None:
            return reviewer.review_set(
                self.work_order, diffs=self.diffs,
                new_contents=self.new_contents or {},
                old_contents=self.old_contents or {},
                level=self.level, sink=sink,
            )
        return reviewer.review(
            self.work_order, diff=self.diff,
            new_content=self.new_content, old_content=self.old_content,
            level=self.level, sink=sink,
        )


@dataclass
class Conflict:
    role: str
    verdict: str
    body: str
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    severity: str = "warn"

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "verdict": self.verdict,
            "body": self.body,
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "severity": self.severity,
        }


@dataclass
class RoundResult:
    findings: list[dict]
    conflicts: list[Conflict]
    messages: list[AgentMessage]
    votes: list[Vote]
    all_approved: bool

    @property
    def per_role(self) -> list[dict]:
        return self.findings


def _normalise_refs(raw) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for item in raw or []:
        try:
            ref = item if isinstance(item, EvidenceRef) else EvidenceRef.from_dict(item)
        except (ProtocolError, TypeError, ValueError):
            continue
        if not ref.validate():
            refs.append(ref)
    return refs


def _normalise_finding(role: str, data: dict) -> dict:
    data = dict(data or {})
    verdict = str(data.get("verdict") or "error").strip().lower()
    if verdict not in ("approve", "reject", "abstain", "error"):
        verdict = "error"
    reasons = data.get("reasons")
    if isinstance(reasons, str):
        reasons = [reasons]
    if not isinstance(reasons, list):
        reasons = []
    reasons = [str(item) for item in reasons if str(item).strip()]
    evidence = _normalise_refs(data.get("evidence_refs") or data.get("evidence") or [])
    data.update({
        "role": role,
        "verdict": verdict,
        "reasons": reasons,
        "evidence_refs": [ref.to_dict() for ref in evidence],
    })
    return data


class MeetingOrchestrator:
    """Round 1 engine; Round 2 is added in Phase 3."""

    def __init__(self, reviewers: dict[str, Any], *, event_sink: EventSink | None = None,
                 max_workers: int = 4, name: str = "meeting") -> None:
        self.reviewers = dict(reviewers)
        self.roles = list(self.reviewers)
        self.event_sink = event_sink
        self.max_workers = max(1, int(max_workers))
        self.name = name
        self.blackboard = Blackboard()
        self._opened = False
        self._run_id = "meeting"
        self._level = 2
        self.last_result: RoundResult | None = None

    # ------------------------------------------------------------------
    def _emit(self, event_type: str, data: dict, status: str = "info") -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(event_type, data, status)
        except Exception:
            # Observability must never break the decision path.
            pass

    def open(self, work_order, context: MeetingContext) -> None:
        verify = dict(context.verify_result or {})
        exit_code = verify.get("exit_code")
        if exit_code not in (0, "0", None):
            raise ProtocolError(f"verify chua pass (exit={exit_code}); khong mo meeting")
        self.blackboard = Blackboard(
            brief=context.brief or str(getattr(work_order, "objective", "") or ""),
            diff=context.diff if context.diffs is None else "\n".join(
                f"### {path}\n{diff}" for path, diff in context.diffs.items()),
            verify_result=verify,
        )
        self._opened = True
        self._level = int(getattr(work_order, "level", context.level) or 2)
        self._emit("meeting.opened", {
            "task_id": getattr(work_order, "task_id", ""),
            "roles": list(self.roles),
            "verify_exit": exit_code,
            "verify_mode": verify.get("mode"),
            "round": 1,
        })

    # ------------------------------------------------------------------
    def _review_one(self, role: str, reviewer, context: MeetingContext, sink) -> dict:
        try:
            raw = context.call_reviewer(reviewer, sink=sink)
            return _normalise_finding(role, raw if isinstance(raw, dict) else {})
        except Exception as exc:  # noqa: BLE001
            return _normalise_finding(role, {
                "verdict": "error",
                "reasons": [f"{type(exc).__name__}: {str(exc)[:300]}"],
            })

    def _message_for_finding(self, work_order, finding: dict) -> AgentMessage | None:
        role = finding["role"]
        verdict = finding["verdict"]
        reasons = finding.get("reasons") or []
        body = f"{verdict}: {reasons[0]}" if reasons else verdict
        refs = _normalise_refs(finding.get("evidence_refs") or [])
        kind = "objection" if verdict == "reject" and refs else "vote"
        try:
            return AgentMessage(
                run_id=str(getattr(work_order, "run_id", "") or self._run_id),
                task_id=str(getattr(work_order, "task_id", "") or ""),
                sender=role,
                recipients=["lead"],
                kind=kind,
                body=body,
                evidence_refs=refs if kind == "objection" else [],
                round=1,
            )
        except Exception:
            return None

    def run_round1(self, work_order, context: MeetingContext, *, sink=None) -> RoundResult:
        if not self._opened:
            raise ProtocolError("meeting chua mo")
        self._run_id = str(getattr(work_order, "run_id", "") or "meeting")
        findings: list[dict] = []
        by_role: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(self.reviewers))) as pool:
            futures = {
                pool.submit(self._review_one, role, reviewer, context, sink): role
                for role, reviewer in self.reviewers.items()
            }
            for future in as_completed(futures):
                role = futures[future]
                try:
                    by_role[role] = future.result()
                except Exception as exc:  # noqa: BLE001
                    by_role[role] = _normalise_finding(role, {
                        "verdict": "error",
                        "reasons": [f"{type(exc).__name__}: {str(exc)[:300]}"],
                    })
        for role in self.roles:
            finding = by_role.get(role) or _normalise_finding(role, {
                "verdict": "error", "reasons": ["reviewer khong tra ket qua"],
            })
            findings.append(finding)
            vote_verdict = finding["verdict"] if finding["verdict"] in (
                "approve", "reject", "abstain") else "reject"
            vote = Vote(role=role, verdict=vote_verdict,
                        reasons=finding.get("reasons") or [], round=1)
            try:
                self.blackboard.record_vote(vote)
            except ProtocolError:
                pass
            message = self._message_for_finding(work_order, finding)
            if message is not None:
                try:
                    self.blackboard.add_message(message)
                except ProtocolError:
                    # A malformed evidence pointer must not hide a reject.
                    fallback = AgentMessage(
                        run_id=message.run_id, task_id=message.task_id,
                        sender=role, recipients=["lead"], kind="vote",
                        body=message.body, round=1,
                    )
                    self.blackboard.add_message(fallback)
            self._emit("meeting.message", {
                "task_id": getattr(work_order, "task_id", ""),
                **(message.to_dict() if message is not None else {
                    "round": 1, "sender": role, "recipients": ["lead"],
                    "kind": "vote", "body": finding["verdict"],
                    "evidence_refs": [], "reply_to": None,
                }),
            })
            self._emit("meeting.vote", {
                "task_id": getattr(work_order, "task_id", ""),
                **vote.to_dict(),
            })

        conflicts = self.detect_conflicts(findings)
        all_approved = bool(findings) and all(
            item.get("verdict") == "approve" for item in findings)
        result = RoundResult(findings=findings, conflicts=conflicts,
                             messages=list(self.blackboard.messages),
                             votes=list(self.blackboard.vote_history),
                             all_approved=all_approved)
        self.last_result = result
        if conflicts:
            self._emit("meeting.conflict", {
                "task_id": getattr(work_order, "task_id", ""),
                "round": 1,
                "conflicts": [item.to_dict() for item in conflicts],
                "all_approved": False,
            }, "info")
        return result

    # ------------------------------------------------------------------
    def detect_conflicts(self, findings: Iterable[dict]) -> list[Conflict]:
        conflicts: list[Conflict] = []
        for finding in findings:
            if finding.get("verdict") == "approve":
                continue
            refs = _normalise_refs(finding.get("evidence_refs") or [])
            body = "; ".join(finding.get("reasons") or []) or str(finding.get("verdict"))
            severity = "block" if finding.get("role") == "security" else "warn"
            conflicts.append(Conflict(role=finding.get("role", "?"),
                                      verdict=finding.get("verdict", "error"),
                                      body=body, evidence_refs=refs,
                                      severity=severity))
        return conflicts

    # ------------------------------------------------------------------
    def aggregate(self, result: RoundResult | None = None) -> dict:
        result = result or self.last_result
        if result is None:
            return {"verdict": "reject", "reasons": ["meeting chua chay"],
                    "per_role": [], "reviewer": self.name,
                    "meeting": self.blackboard.snapshot()}
        reasons: list[str] = []
        for finding in result.findings:
            for reason in finding.get("reasons") or []:
                reasons.append(f"[{finding.get('role', '?')}] {reason}")
        verdict = "approve" if result.all_approved else "reject"
        return {
            "verdict": verdict,
            "level": self._level,
            "reasons": reasons[:12],
            "reviewer": self.name,
            "per_role": result.findings,
            "skipped": [],
            "round": 1,
            "conflicts": [item.to_dict() for item in result.conflicts],
            "meeting": self.blackboard.snapshot(),
        }

    def close(self, decision: Decision | dict, *, work_order=None) -> Decision:
        item = decision if isinstance(decision, Decision) else Decision.from_dict(decision)
        problems = item.validate()
        if problems:
            raise ProtocolError("; ".join(problems))
        self.blackboard.add_decision(item)
        body = item.verdict
        if item.reasons:
            body += ": " + "; ".join(item.reasons[:3])
        message = AgentMessage(
            run_id=str(getattr(work_order, "run_id", "") or self._run_id),
            task_id=str(getattr(work_order, "task_id", "") or ""),
            sender="lead", recipients=["all"], kind="decision",
            body=body, round=item.round,
        )
        self.blackboard.add_message(message)
        self._emit("meeting.decision", {
            "task_id": getattr(work_order, "task_id", ""),
            **item.to_dict(),
        }, "ok" if item.verdict == "approve" else "fail")
        return item

    def abort(self, reason: str, *, work_order=None, round_no: int = 1) -> None:
        self._emit("meeting.aborted", {
            "task_id": getattr(work_order, "task_id", ""),
            "round": round_no,
            "reason": str(reason or "aborted"),
        }, "fail")
