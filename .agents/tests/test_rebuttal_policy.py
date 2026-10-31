#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 3 acceptance - rebuttal, veto policy and hard budget.

    python .agents/tests/test_rebuttal_policy.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import recipe_c  # noqa: E402
import wave  # noqa: E402
import workers  # noqa: E402
import work_order as work_order_module  # noqa: E402
from core.budget import BudgetExceeded, CircuitBreaker, MeetingBudget  # noqa: E402
from core.meeting import MeetingContext, MeetingOrchestrator  # noqa: E402
from core.policy import VetoPolicy  # noqa: E402
from core.protocol import Vote  # noqa: E402

PY = f'"{sys.executable}"'
TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "meeting_policy_tests"


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
    def __init__(self, role: str, verdict: str = "approve", *, reject_first: bool = False,
                 evidence: bool = True) -> None:
        super().__init__()
        self.role = role
        self.verdict = verdict
        self.reject_first = reject_first
        self.evidence = evidence

    def review(self, work_order, *, diff, new_content, old_content, level, sink):
        self.calls += 1
        verdict = self.verdict
        if self.reject_first and self.calls == 1:
            verdict = "reject"
        refs = []
        if verdict == "reject" and self.evidence:
            refs = [{"file": "calc.py", "line_start": 1, "note": "unsafe"}]
        return {"role": self.role, "verdict": verdict,
                "reasons": [f"{self.role}: {verdict}"],
                "evidence_refs": refs, "task_id": work_order.task_id,
                "model": "fake"}

    def review_set(self, work_order, *, diffs, new_contents, old_contents, level, sink):
        self.calls += 1
        return self.review(work_order, diff="", new_content="", old_content="",
                           level=level, sink=sink)


def make_order(task_id="POL1"):
    return work_order_module.create(
        task_id=task_id, title="policy", objective="Sua ham add",
        boundaries="Chi sua calc.py", input_context="calc.py",
        acceptance_criteria="check.py exit 0", verify_command=f"{PY} check.py",
        target_files=["calc.py"], sandbox_files=["calc.py", "check.py"],
        level=2, max_rounds=1,
    )


def context(order, *, verify_exit=0, new_content="def add(a,b):\n    return a + b\n",
            old_content="def add(a,b):\n    return a - b\n"):
    return MeetingContext(
        work_order=order, brief="fix", diff="+ return a + b",
        new_content=new_content, old_content=old_content,
        level=2, verify_result={"exit_code": verify_exit, "mode": "single"},
    )


def votes(**verdicts) -> dict:
    return {role: Vote(role, verdict, [f"{role} reason"], round=1)
            for role, verdict in verdicts.items()}


def conflict(role: str, *, evidence=True) -> dict:
    refs = [{"file": "calc.py", "line_start": 1}] if evidence else []
    return {"role": role, "verdict": "reject", "body": "unsafe",
            "evidence_refs": refs, "severity": "block"}


def test_budget(checks: Checks) -> None:
    checks.check("budget: meeting wall clock is 600s", MeetingBudget().seconds == 600,
                 MeetingBudget().seconds)
    b = MeetingBudget()
    b.seed(coder_initial=8, round1=4)
    checks.check("budget: 8+4=12", b.spent == 12, b.snapshot())
    b.spend("coder", "rebuttal", 2)
    checks.check("budget: coder total 10", b.spent == 14, b.snapshot())
    b.spend("reviewer", "round2", 2)
    checks.check("budget: exact 16", b.spent == 16, b.snapshot())
    try:
        b.spend("reviewer", "round2", 1)
        blocked = False
    except BudgetExceeded:
        blocked = True
    checks.check("budget: 17 hard abort", blocked, b.snapshot())

    b = MeetingBudget()
    b.seed(coder_initial=9, round1=4)
    checks.check("budget: coder initial >8 blocks round2", not b.can_open_round2(),
                 b.snapshot())

    b = MeetingBudget(seconds=0)
    checks.check("budget: time cap", not b.check_time() and b.exceeded == "time",
                 b.snapshot())

    breaker = CircuitBreaker()
    checks.check("breaker: first observation", not breaker.observe("parse"), breaker.snapshot())
    checks.check("breaker: second same observation trips", breaker.observe("parse"),
                 breaker.snapshot())
    checks.check("breaker: signature retained", breaker.signature == "parse", breaker.snapshot())


def test_policy(checks: Checks) -> None:
    policy = VetoPolicy()
    all_approve = votes(security="approve", qa="approve", perf="approve", docs="approve")
    d = policy.evaluate(all_approve, [])
    checks.check("policy: 4/4 approve", d.verdict == "approve", d.to_dict())

    d = policy.evaluate(votes(security="error", qa="approve",
                              perf="approve", docs="approve"), [])
    checks.check("policy: 1 error + 3 approve -> reject, never approve",
                 d.verdict == "reject" and "unavailable" in " ".join(d.reasons),
                 d.to_dict())

    d = policy.evaluate(votes(security="reject", qa="approve", perf="approve", docs="approve"),
                        [conflict("security")])
    checks.check("policy: security evidence veto", d.verdict == "reject" and d.vetoes == ["security"],
                 d.to_dict())

    d = policy.evaluate(votes(security="reject", qa="approve", perf="approve", docs="approve"),
                        [conflict("security", evidence=False)])
    checks.check("policy: security reject without evidence escalates",
                 d.verdict == "escalate" and not d.vetoes, d.to_dict())

    d = policy.evaluate(votes(security="approve", qa="reject", perf="approve", docs="approve"),
                        [conflict("qa")], verify_result={"exit_code": 0})
    checks.check("policy: qa evidence veto", d.verdict == "reject" and d.vetoes == ["qa"],
                 d.to_dict())

    d = policy.evaluate(votes(security="approve", qa="reject", perf="approve", docs="approve"),
                        [], verify_result={"exit_code": 1})
    checks.check("policy: qa verify fail veto", d.verdict == "reject" and d.vetoes == ["qa"],
                 d.to_dict())

    d = policy.evaluate(votes(security="approve", qa="reject", perf="approve", docs="approve"),
                        [conflict("qa")], verify_result={"exit_code": 0},
                        qa_test_tampering=True)
    checks.check("policy: qa test tamper veto", d.verdict == "reject", d.to_dict())

    d = policy.evaluate(votes(security="approve", qa="approve", perf="reject", docs="approve"),
                        [conflict("perf")])
    checks.check("policy: perf only escalates", d.verdict == "escalate" and d.dissent == ["perf"],
                 d.to_dict())

    d = policy.evaluate(votes(security="approve", qa="approve", perf="approve", docs="reject"),
                        [conflict("docs")])
    checks.check("policy: docs only escalates", d.verdict == "escalate" and d.dissent == ["docs"],
                 d.to_dict())

    d = policy.evaluate(votes(security="reject", qa="reject", perf="approve", docs="approve"),
                        [conflict("security"), conflict("qa")])
    checks.check("policy: mixed veto reject", d.verdict == "reject" and set(d.vetoes) == {"security", "qa"},
                 d.to_dict())


def test_round2(checks: Checks) -> None:
    order = make_order("POL2")
    panel = {
        "security": FakeRole("security", reject_first=True),
        "qa": FakeRole("qa"), "perf": FakeRole("perf"), "docs": FakeRole("docs"),
    }
    orch = MeetingOrchestrator(panel)
    orch.open(order, context(order))
    r1 = orch.run_round1(order, context(order))
    checks.check("round2: round1 conflict", not r1.all_approved and r1.conflicts, r1.findings)
    participants = orch.participants_for_rebuttal(r1.conflicts, new_patch=True)
    checks.check("round2: new patch requires security+qa",
                 participants == ["security", "qa"], participants)
    checks.check("round2: max two participants",
                 len(participants) <= 2, participants)
    mandatory = orch.participants_for_rebuttal([{"role": "perf"}], new_patch=True)
    checks.check("round2: new patch forces security+qa",
                 mandatory == ["security", "qa"], mandatory)

    new_ctx = context(order, new_content="def add(a,b):\n    return a + b\n")
    orch.record_rebuttal(order, sender="coder", recipients=participants,
                         body="removed unsafe code")
    r2 = orch.run_round2(order, new_ctx, participants, new_patch=True)
    checks.check("round2: recheck passes", r2.all_approved, r2.findings)
    checks.check("round2: round marker", all(v.round == 2 for v in r2.votes), r2.votes)
    checks.check("round2: no round3 api", not hasattr(orch, "run_round3"), "run_round3 exists")
    checks.check("round2: votes updated", orch.blackboard.votes["security"].round == 2,
                 orch.blackboard.vote_snapshot())


def _prepare_project(name: str) -> Path:
    root = TEMP_ROOT / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "check.py").write_text(
        "from calc import add\nassert add(2, 3) == 5\nprint('OK')\n", encoding="utf-8")
    return root


def test_recipe_rebuttal(checks: Checks) -> None:
    root = _prepare_project("recipe-rebuttal")
    initial = "def add(a, b):\n    eval('1+1')\n    return a - b\n"  # fails check, use checked below
    # Initial patch must verify; use a passing-but-insecure body.
    initial = "def add(a, b):\n    eval('1+1')\n    return a + b\n"
    final = "def add(a, b):\n    return a + b\n"
    panel = {
        "security": FakeRole("security", reject_first=True),
        "qa": FakeRole("qa"), "perf": FakeRole("perf"), "docs": FakeRole("docs"),
    }
    meeting = workers.MeetingReviewer(panel)
    report = recipe_c.run(
        make_order("POL3"),
        coder=workers.ScriptedCoder({"POL3": [
            {"format": "whole", "content": initial},
            {"format": "whole", "content": final},
        ]}),
        reviewer=meeting, root=root, run_id="meeting-rebuttal", log=False,
    )
    checks.check("recipe rebuttal: applied", report["status"] == "applied", report["status"])
    checks.check("recipe rebuttal: final content clean", "eval(" not in (root / "calc.py").read_text(encoding="utf-8"),
                 (root / "calc.py").read_text(encoding="utf-8"))
    checks.check("recipe rebuttal: metadata", bool(report.get("meeting_rebuttal")),
                 report.get("meeting_rebuttal"))
    checks.check("recipe rebuttal: decision approve",
                 (report.get("review") or {}).get("decision", {}).get("verdict") == "approve",
                 report.get("review"))


def main() -> int:
    checks = Checks()
    for name, test in (
        ("budget", test_budget),
        ("policy", test_policy),
        ("round2", test_round2),
        ("recipe rebuttal", test_recipe_rebuttal),
    ):
        print(f"[{name}] ...")
        test(checks)
    print("")
    print(f"POLICY: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
