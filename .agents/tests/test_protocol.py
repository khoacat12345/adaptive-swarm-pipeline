#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1 acceptance - meeting protocol and blackboard.

    python .agents/tests/test_protocol.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

import schema  # noqa: E402
from core.blackboard import Blackboard  # noqa: E402
from core.protocol import (  # noqa: E402
    AgentMessage,
    Claim,
    Decision,
    Dissent,
    EvidenceRef,
    Objection,
    ProtocolError,
    Vote,
)


class Checks:
    def __init__(self) -> None:
        self.total = 0
        self.failures: list[str] = []

    def check(self, name: str, condition, detail="") -> bool:
        self.total += 1
        ok = bool(condition)
        if not ok:
            self.failures.append(f"{name}: {detail}" if detail else name)
        return ok

    @property
    def passed(self) -> int:
        return self.total - len(self.failures)


def message(**overrides) -> AgentMessage:
    data = dict(
        run_id="run-1",
        task_id="task-1",
        sender="security",
        recipients=["coder"],
        kind="objection",
        body="Unsafely executes user input.",
        evidence_refs=[EvidenceRef("auth.py", 10, 10, "verify-1", "eval")],
    )
    data.update(overrides)
    return AgentMessage(**data)


def test_serialization(checks: Checks) -> None:
    original = message()
    data = original.to_dict()
    restored = AgentMessage.from_dict(data)
    checks.check("protocol: serialize roundtrip", restored.to_dict() == data, restored.to_dict())
    checks.check("protocol: evidence roundtrip",
                 restored.evidence_refs[0].to_dict() == original.evidence_refs[0].to_dict(),
                 restored.evidence_refs)
    checks.check("protocol: timestamp ISO UTC", original.timestamp.endswith("Z"),
                 original.timestamp)

    ref = EvidenceRef("src/a.py", 3, 5, "evt-1", "line evidence")
    checks.check("protocol: EvidenceRef roundtrip",
                 EvidenceRef.from_dict(ref.to_dict()).to_dict() == ref.to_dict(), ref)
    checks.check("protocol: absolute evidence path rejected",
                 bool(EvidenceRef("C:/tmp/a.py").validate()), "absolute path accepted")
    checks.check("protocol: traversal evidence path rejected",
                 bool(EvidenceRef("../a.py").validate()), "traversal accepted")
    checks.check("protocol: line order rejected",
                 bool(EvidenceRef("a.py", 5, 2).validate()), "bad lines accepted")


def test_validation(checks: Checks) -> None:
    checks.check("protocol: valid message", not message().validate(), message().validate())
    checks.check("protocol: round 3 rejected",
                 bool(message(round=3).validate()), "round 3 accepted")
    checks.check("protocol: unknown kind rejected",
                 bool(message(kind="gossip").validate()), "unknown kind accepted")
    checks.check("protocol: empty recipients rejected",
                 bool(message(recipients=[]).validate()), "empty recipients accepted")
    checks.check("protocol: unknown sender rejected",
                 bool(message(sender="unknown").validate()), "unknown sender accepted")
    checks.check("protocol: self recipient rejected",
                 bool(message(sender="coder", recipients=["coder"]).validate()),
                 "self recipient accepted")
    checks.check("protocol: objection without evidence rejected",
                 bool(message(evidence_refs=[]).validate()), "no evidence accepted")
    checks.check("protocol: bad timestamp rejected",
                 bool(message(timestamp="yesterday").validate()), "bad timestamp accepted")
    checks.check("protocol: too many evidence refs rejected",
                 bool(message(evidence_refs=[EvidenceRef(f"a{i}.py") for i in range(21)]).validate()),
                 "too many evidence refs accepted")


def test_reply_links(checks: Checks) -> None:
    parent = message(kind="proposal", recipients=["lead"], body="initial patch")
    child = message(kind="rebuttal", recipients=["coder"], body="rebuttal",
                    reply_to=parent.id)
    checks.check("protocol: reply-to known parent",
                 not child.validate(known={parent.id: parent}), child.validate(known={parent.id: parent}))
    checks.check("protocol: reply-to unknown parent rejected",
                 bool(child.validate(known={})), "unknown parent accepted")
    checks.check("protocol: reply-to self rejected",
                 bool(message(reply_to=message().id).validate(known={})), "self reply accepted")

    board = Blackboard()
    board.add_message(parent)
    checks.check("protocol: blackboard append reply",
                 not board.add_message(child), board.snapshot())
    checks.check("protocol: blackboard transcript count", len(board.messages) == 2,
                 len(board.messages))
    try:
        board.add_message(parent)
        duplicate = False
    except ProtocolError:
        duplicate = True
    checks.check("protocol: duplicate message id rejected", duplicate, "duplicate accepted")

    other_run = message(kind="rebuttal", recipients=["coder"], body="bad link",
                        reply_to=parent.id, run_id="run-2")
    try:
        board.add_message(other_run)
        wrong_run = False
    except ProtocolError:
        wrong_run = True
    checks.check("protocol: reply-to wrong run rejected", wrong_run, "wrong run accepted")


def test_blackboard(checks: Checks) -> None:
    board = Blackboard(brief="Fix auth", diff="+ eval(x)", verify_result={"exit_code": 0})
    file_ref = EvidenceRef("auth.py", 12, 12, "verify-1", "eval")
    claim = Claim(sender="security", body="eval is unsafe", evidence_refs=[file_ref])
    objection = Objection(sender="security", body="remove eval", target="coder",
                          severity="block", evidence_refs=[file_ref])
    board.add_claim(claim)
    board.add_objection(objection)
    board.record_vote(Vote("security", "reject", ["unsafe"], round=1))
    board.record_vote(Vote("qa", "approve", ["tests pass"], round=1))
    board.add_decision(Decision("reject", ["security veto"], ["security"], ["docs"], round=2))
    board.add_dissent(Dissent("docs", "needs a migration note", round=2))

    snap = board.snapshot()
    checks.check("blackboard: claim/objection stored",
                 len(snap["claims"]) == 1 and len(snap["objections"]) == 1, snap)
    checks.check("blackboard: votes stored",
                 snap["votes"]["security"]["verdict"] == "reject", snap["votes"])
    checks.check("blackboard: decision/dissent stored",
                 snap["decisions"][0]["verdict"] == "reject" and snap["dissent"][0]["role"] == "docs",
                 snap)
    checks.check("blackboard: snapshot JSON-safe",
                 isinstance(json.dumps(snap, ensure_ascii=False), str), "not JSON")
    checks.check("blackboard: from_dict roundtrip",
                 Blackboard.from_dict(snap).snapshot() == snap, "roundtrip mismatch")
    checks.check("blackboard: objections_for role",
                 len(board.objections_for("coder")) == 1, board.objections_for("coder"))
    checks.check("blackboard: latest decision",
                 board.latest_decision() is not None, "missing decision")


def test_secret_scan(checks: Checks) -> None:
    key = "sk-ant-" + "A" * 24
    unsafe = message(kind="proposal", recipients=["lead"], body=f"token={key}",
                     evidence_refs=[])
    board = Blackboard()
    findings = board.add_message(unsafe)
    stored = board.messages[-1].body
    checks.check("secret: scanner finding recorded", bool(findings), findings)
    checks.check("secret: stored body redacted", key not in stored and "[REDACTED_SECRET]" in stored,
                 stored)
    checks.check("secret: raw body not mutated", key in unsafe.body, unsafe.body)


def test_schema(checks: Checks) -> None:
    data = {
        "task_id": "task-1",
        "round": 1,
        "sender": "security",
        "recipients": ["coder"],
        "kind": "objection",
        "body": "eval is unsafe",
        "evidence_refs": [{"file": "auth.py", "line_start": 10}],
        "reply_to": None,
    }
    ok = {
        "run_id": "run-1",
        "timestamp": "2026-10-24T00:00:00Z",
        "event_type": "meeting.message",
        "data": data,
    }
    checks.check("schema: meeting event valid", not schema.validate(ok), schema.validate(ok))
    error_vote = {
        "schema_version": 1, "event_id": "err-vote", "timestamp": "2026-10-31T00:00:00Z",
        "run_id": "r", "event_type": "meeting.vote",
        "result": {"status": "info"},
        "data": {"task_id": "task-1", "role": "perf", "verdict": "error",
                 "reasons": ["quota"], "round": 1},
    }
    checks.check("schema: error vote is valid", not schema.validate(error_vote),
                 schema.validate(error_vote))
    checks.check("schema: meeting node mapped",
                 schema.node_of("meeting.message") == "meeting", schema.node_of("meeting.message"))
    checks.check("schema: decision node mapped",
                 schema.node_of("meeting.decision") == "lead", schema.node_of("meeting.decision"))
    checks.check("schema: describe has meeting events",
                 "meeting.message" in schema.describe()["meeting_events"], schema.describe())
    bad_round = json.loads(json.dumps(ok))
    bad_round["data"]["round"] = 3
    checks.check("schema: meeting round 3 invalid", bool(schema.validate(bad_round)),
                 schema.validate(bad_round))
    bad_kind = json.loads(json.dumps(ok))
    bad_kind["data"]["kind"] = "gossip"
    checks.check("schema: meeting kind invalid", bool(schema.validate(bad_kind)),
                 schema.validate(bad_kind))
    no_evidence = json.loads(json.dumps(ok))
    no_evidence["data"]["evidence_refs"] = []
    checks.check("schema: objection evidence invalid", bool(schema.validate(no_evidence)),
                 schema.validate(no_evidence))
    old = {
        "run_id": "old",
        "timestamp": "2026-01-01T00:00:00Z",
        "event_type": "recipe.done",
        "result": {"status": "ok"},
        "data": {"task_id": "old"},
    }
    checks.check("schema: old event remains compatible",
                 not schema.validate(schema.normalize(old)), schema.validate(schema.normalize(old)))


def main() -> int:
    checks = Checks()
    for name, test in (
        ("serialization", test_serialization),
        ("validation", test_validation),
        ("reply links", test_reply_links),
        ("blackboard", test_blackboard),
        ("secret scan", test_secret_scan),
        ("schema", test_schema),
    ):
        print(f"[{name}] ...")
        test(checks)
    print("")
    print(f"PROTOCOL: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
