#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 4 acceptance - replay meeting transcript and UI contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

import replay  # noqa: E402


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


def event(event_type: str, data: dict, *, run_id="meet-1", timestamp="2026-10-24T00:00:00Z"):
    return {"run_id": run_id, "timestamp": timestamp, "event_type": event_type,
            "data": data, "result": {}}


def events():
    key = "sk-ant-" + "A" * 24
    return [
        event("meeting.message", {
            "id": "m1", "task_id": "T1", "round": 1, "sender": "security",
            "recipients": ["coder"], "kind": "objection",
            "body": f"bad token={key}", "reply_to": None,
            "evidence_refs": [{"file": "auth.py", "line_start": 10,
                               "line_end": 12, "verify_event_id": "v1",
                               "note": "unsafe eval"}],
        }),
        event("meeting.message", {
            "id": "m2", "task_id": "T1", "round": 2, "sender": "coder",
            "recipients": ["security", "qa"], "kind": "rebuttal",
            "body": "removed eval", "reply_to": "m1", "evidence_refs": [],
        }),
        event("meeting.decision", {
            "task_id": "T1", "verdict": "reject", "round": 2,
            "reasons": ["security veto"], "vetoes": ["security"], "dissent": ["docs"],
        }),
        event("recipe.done", {"task_id": "T1", "status": "aborted", "requests": 8},
              timestamp="2026-10-24T00:00:05Z"),
    ]


def test_replay(checks: Checks) -> None:
    data = events()
    messages = replay.meeting_messages(data, "meet-1")
    checks.check("replay: two messages", len(messages) == 2, messages)
    first = messages[0]
    checks.check("replay: round", first.get("round") == 1, first)
    checks.check("replay: sender", first.get("sender") == "security", first)
    checks.check("replay: recipients", first.get("recipients") == ["coder"], first)
    checks.check("replay: kind", first.get("kind") == "objection", first)
    checks.check("replay: evidence card data",
                 first["evidence_refs"][0]["file"] == "auth.py"
                 and first["evidence_refs"][0]["line_end"] == 12, first["evidence_refs"])
    checks.check("replay: reply-to", messages[1].get("reply_to") == "m1", messages[1])
    checks.check("replay: secret redacted",
                 "sk-ant-" not in first.get("body", "") and "[REDACTED_SECRET]" in first.get("body", ""),
                 first.get("body"))
    checks.check("replay: public event redacted",
                 "sk-ant-" not in json.dumps(replay.public_event(data[0]), ensure_ascii=False),
                 replay.public_event(data[0]))

    edges = replay.meeting_edges(messages)
    checks.check("edges: security -> coder",
                 any(item["from"] == "security" and item["to"] == "coder" for item in edges),
                 edges)
    checks.check("edges: coder -> security + qa",
                 any(item["from"] == "coder" and item["to"] == "security" for item in edges)
                 and any(item["from"] == "coder" and item["to"] == "qa" for item in edges),
                 edges)
    checks.check("edges: no message => no edge", replay.meeting_edges([]) == [], replay.meeting_edges([]))

    stats = replay.meeting_stats(data, "meet-1")
    checks.check("stats: rounds", stats["rounds"] == [1, 2], stats)
    checks.check("stats: objections", stats["objections"] == 1, stats)
    checks.check("stats: rebuttals", stats["rebuttals"] == 1, stats)
    checks.check("stats: veto role", stats["vetoes"] == ["security"], stats)
    checks.check("stats: dissent", stats["dissent"] == ["docs"], stats)
    checks.check("stats: verdict", stats["verdict"] == "reject", stats)
    checks.check("stats: requests", stats["requests"] == 8, stats)
    payload = replay.meeting_payload(data, "meet-1")
    checks.check("payload: complete", len(payload["messages"]) == 2
                 and payload["stats"]["messages"] == 2, payload)


def test_ui_contract(checks: Checks) -> None:
    html = (AGENTS_DIR / "dashboard_ui.html").read_text(encoding="utf-8")
    for name in ("meetingMessages", "meetingBubble", "meetingRoundLabel",
                 "meetingEvidence", "meetingEdges", "meetingActiveNodes",
                 "renderMeetingGraph", "meetingStats", "EventSource", "/api/stream"):
        checks.check(f"ui: {name}", name in html, name)
    checks.check("ui: war room filter round 2", "setMeetingRound('2')" in html, "missing filter")


def main() -> int:
    checks = Checks()
    print("[replay] ...")
    test_replay(checks)
    print("[ui contract] ...")
    test_ui_contract(checks)
    print("")
    print(f"MEETING UI: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
