#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 2 acceptance - meeting orchestrator and independent Round 1.

    python .agents/tests/test_round1.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import recipe_c  # noqa: E402
import wave  # noqa: E402
import workers  # noqa: E402
import work_order as work_order_module  # noqa: E402
from core.meeting import MeetingContext, MeetingOrchestrator  # noqa: E402
from core.protocol import ProtocolError  # noqa: E402

PY = f'"{sys.executable}"'
TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "meeting_tests"


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


class FakeRole(workers.Reviewer):
    def __init__(self, role: str, verdict: str, *, evidence: bool = True) -> None:
        super().__init__()
        self.role = role
        self.verdict = verdict
        self.evidence = evidence
        self.seen_kwargs: list[dict] = []

    def review(self, work_order, *, diff, new_content, old_content, level, sink):
        self.seen_kwargs.append({"diff": diff, "level": level})
        return self._finding(work_order)

    def review_set(self, work_order, *, diffs, new_contents, old_contents, level, sink):
        self.calls += 1
        self.seen_kwargs.append({"diffs": list(diffs), "level": level})
        return self._finding(work_order)

    def _finding(self, work_order):
        refs = []
        if self.verdict == "reject" and self.evidence:
            refs = [{"file": "calc.py", "line_start": 1, "note": "bad input"}]
        return {
            "role": self.role,
            "verdict": self.verdict,
            "reasons": [f"{self.role} says {self.verdict}"],
            "evidence_refs": refs,
            "task_id": work_order.task_id,
            "model": "fake",
        }


def make_order(task_id="MEET1"):
    return work_order_module.create(
        task_id=task_id, title="meeting", objective="Sua ham add trong calc.py",
        boundaries="Chi sua calc.py", input_context="calc.py",
        acceptance_criteria="check.py exit 0", verify_command=f"{PY} check.py",
        target_files=["calc.py"], sandbox_files=["calc.py", "check.py"],
        level=2, max_rounds=1,
    )


def panel(verdicts: dict, *, evidence: bool = True) -> dict:
    return {role: FakeRole(role, verdict, evidence=evidence)
            for role, verdict in verdicts.items()}


def context(order, *, exit_code=0):
    return MeetingContext(
        work_order=order, brief="fix add", diff="+ return a + b",
        new_content="def add(a,b):\n    return a + b\n",
        old_content="def add(a,b):\n    return a - b\n",
        level=2, verify_result={"exit_code": exit_code, "mode": "single"},
    )


def test_all_approve(checks: Checks) -> None:
    order = make_order()
    events: list[tuple] = []
    orch = MeetingOrchestrator(panel({r: "approve" for r in wave.ROLE_ORDER}),
                               event_sink=lambda kind, data, status="info":
                               events.append((kind, data, status)), max_workers=4)
    orch.open(order, context(order))
    result = orch.run_round1(order, context(order))
    checks.check("round1: 4/4 approve", result.all_approved, result.findings)
    checks.check("round1: no conflict", not result.conflicts, result.conflicts)
    checks.check("round1: 5 persisted messages (coder + 4 reviewers)",
                 len(result.messages) == 5, len(result.messages))
    checks.check("round1: coder proposal is in transcript",
                 any(m.sender == "coder" and m.kind == "proposal" for m in result.messages),
                 result.messages)
    checks.check("round1: 4 votes", len(result.votes) == 4, len(result.votes))
    checks.check("round1: opened event", any(e[0] == "meeting.opened" for e in events), events)
    checks.check("round1: message events", sum(e[0] == "meeting.message" for e in events) == 5, events)
    checks.check("round1: no conflict event", not any(e[0] == "meeting.conflict" for e in events), events)
    checks.check("round1: aggregate approve", orch.aggregate(result)["verdict"] == "approve",
                 orch.aggregate(result))
    checks.check("round1: decision close",
                 orch.close({"verdict": "approve", "round": 1}, work_order=order).verdict == "approve",
                 "close failed")


def test_reject_with_evidence(checks: Checks) -> None:
    order = make_order("MEET2")
    events: list[tuple] = []
    orch = MeetingOrchestrator(panel({"security": "reject", "qa": "approve",
                                      "perf": "approve", "docs": "approve"}),
                               event_sink=lambda kind, data, status="info":
                               events.append((kind, data, status)))
    orch.open(order, context(order))
    result = orch.run_round1(order, context(order))
    checks.check("round1: reject detected", not result.all_approved and len(result.conflicts) == 1,
                 result.findings)
    checks.check("round1: conflict role", result.conflicts[0].role == "security",
                 result.conflicts)
    checks.check("round1: reject has evidence",
                 bool(result.conflicts[0].evidence_refs), result.conflicts[0])
    checks.check("round1: conflict event",
                 any(e[0] == "meeting.conflict" for e in events), events)
    checks.check("round1: objection message",
                 any(m.sender == "security" and m.kind == "objection"
                     for m in result.messages), result.messages)


def test_reject_without_evidence(checks: Checks) -> None:
    order = make_order("MEET3")
    orch = MeetingOrchestrator(panel({"security": "reject", "qa": "approve",
                                      "perf": "approve", "docs": "approve"},
                                     evidence=False))
    orch.open(order, context(order))
    result = orch.run_round1(order, context(order))
    checks.check("round1: reject no evidence still conflict",
                 not result.all_approved and len(result.conflicts) == 1, result.findings)
    checks.check("round1: no fake objection without evidence",
                 not any(m.kind == "objection" for m in result.messages), result.messages)
    checks.check("round1: no rebuttal in Phase 2",
                 not any(m.kind == "rebuttal" for m in result.messages), result.messages)


def test_independent_and_error(checks: Checks) -> None:
    order = make_order("MEET4")
    roles = panel({"security": "approve", "qa": "error",
                   "perf": "abstain", "docs": "approve"})
    orch = MeetingOrchestrator(roles, max_workers=4)
    orch.open(order, context(order))
    result = orch.run_round1(order, context(order))
    checks.check("round1: error/abstain are conflicts",
                 len(result.conflicts) == 2, result.findings)
    checks.check("round1: no auto approve", not result.all_approved, result.findings)
    checks.check("round1: reviewers did not receive peer messages",
                 all("messages" not in item.seen_kwargs for item in roles.values()),
                 [item.seen_kwargs for item in roles.values()])

    bad = MeetingOrchestrator(panel({r: "approve" for r in wave.ROLE_ORDER}))
    try:
        bad.open(order, context(order, exit_code=1))
        blocked = False
    except ProtocolError:
        blocked = True
    checks.check("round1: verify fail cannot open meeting", blocked, "opened")


def test_wave_helpers(checks: Checks) -> None:
    conflicts = [{"role": "docs"}, {"role": "perf"}, {"role": "qa"}, {"role": "security"}]
    checks.check("wave: select security first",
                 wave.select_participants(conflicts, limit=2) == ["security", "qa"],
                 wave.select_participants(conflicts, limit=2))
    checks.check("wave: limit zero", wave.select_participants(conflicts, limit=0) == [], "not empty")
    roles = wave.normalize_roles("security=m1,qa=m2")
    checks.check("wave: parse roles", roles == {"security": "m1", "qa": "m2"}, roles)


def _prepare_project(name: str):
    root = TEMP_ROOT / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "check.py").write_text(
        "from calc import add\nassert add(2, 3) == 5\nprint('OK')\n", encoding="utf-8")
    return root


def test_recipe_meeting_integration(checks: Checks) -> None:
    fixed = "def add(a, b):\n    return a + b\n"
    script = {"M1": [{"format": "whole", "content": fixed}],
              "MEET5": [{"format": "whole", "content": fixed}],
              "MEET6": [{"format": "whole", "content": fixed}]}

    root = _prepare_project("recipe-approve")
    events: list[tuple] = []
    meeting = workers.MeetingReviewer(panel({r: "approve" for r in wave.ROLE_ORDER}))
    meeting.bind_run("meeting-approve", root, log=False)
    meeting.event_sink = lambda kind, data, status="info": events.append((kind, data, status))
    report = recipe_c.run(make_order("MEET5"),
                          coder=workers.ScriptedCoder(script),
                          reviewer=meeting, root=root, run_id="meeting-approve",
                          log=False)
    checks.check("recipe meeting: apply on 4/4", report["status"] == "applied",
                 report["status"])
    review = report.get("review") or {}
    checks.check("recipe meeting: review approve", review.get("verdict") == "approve", review)
    checks.check("recipe meeting: meeting transcript", len(review.get("meeting", {}).get("messages", [])) >= 5,
                 review.get("meeting"))
    checks.check("recipe meeting: decision event", any(e[0] == "meeting.decision" for e in events),
                 events)

    root = _prepare_project("recipe-reject")
    meeting = workers.MeetingReviewer(panel({"security": "reject", "qa": "approve",
                                             "perf": "approve", "docs": "approve"}))
    meeting.bind_run("meeting-reject", root, log=False)
    report = recipe_c.run(make_order("MEET6"),
                          coder=workers.ScriptedCoder(script),
                          reviewer=meeting, root=root, run_id="meeting-reject",
                          log=False)
    checks.check("recipe meeting: reject aborts", report["status"] == "aborted",
                 report["status"])
    checks.check("recipe meeting: file unchanged",
                 (root / "calc.py").read_text(encoding="utf-8").endswith("return a - b\n"),
                 (root / "calc.py").read_text(encoding="utf-8"))
    checks.check("recipe meeting: reject review",
                 (report.get("review") or {}).get("verdict") == "reject", report.get("review"))


def main() -> int:
    checks = Checks()
    for name, test in (
        ("all approve", test_all_approve),
        ("reject evidence", test_reject_with_evidence),
        ("reject no evidence", test_reject_without_evidence),
        ("independent/error", test_independent_and_error),
        ("wave helpers", test_wave_helpers),
        ("recipe integration", test_recipe_meeting_integration),
    ):
        print(f"[{name}] ...")
        test(checks)
    print("")
    print(f"ROUND1: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
