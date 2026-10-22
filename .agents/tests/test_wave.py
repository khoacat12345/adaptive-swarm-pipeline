#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 6C - wave reviewer: 4 reviewer chay song song, trong tran request.

    python .agents/tests/test_wave.py
    python .agents/tests/test_wave.py --fast
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import recipe_c                        # noqa: E402
import workers                         # noqa: E402
import work_order as work_order_module  # noqa: E402

PY = f'"{sys.executable}"'
TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "wave_tests"


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


class CountingCoder(workers.Coder):
    """Fake coder that reports N model calls, to test burst/hard cap."""

    def __init__(self, calls: int, content: str) -> None:
        super().__init__()
        self.target_calls = calls
        self.content = content

    def patch(self, work_order, *, current_content, feedback, round_index):
        self.calls = self.target_calls
        return {"format": "whole", "content": self.content}


class Fake(workers.Reviewer):
    def __init__(self, role: str, verdict: str, *, delay: float = 0.0,
                 boom: bool = False) -> None:
        super().__init__()
        self.role = role
        self.verdict = verdict
        self.delay = delay
        self.boom = boom
        self.calls = 0

    def review(self, work_order, *, diff, new_content, old_content, level, sink):
        self.calls += 1
        time.sleep(self.delay)
        if self.boom:
            raise RuntimeError("boom")
        reasons = [] if self.verdict == "approve" else [f"{self.role} issue"]
        return {"verdict": self.verdict, "role": self.role, "reasons": reasons,
                "model": "fake", "task_id": work_order.task_id}

    def review_set(self, work_order, *, diffs, new_contents, old_contents, level, sink):
        return self.review(work_order, diff="\n".join(diffs.values()),
                           new_content="", old_content="", level=level, sink=sink)


def make_wave(verdicts: dict, *, delay: float = 0.0, boom: set | None = None):
    boom = boom or set()

    def factory(role, _model):
        return Fake(role, verdicts.get(role, "approve"), delay=delay, boom=role in boom)

    return workers.WaveReviewer({role: "fake" for role in verdicts}, factory=factory)


def make_order():
    return work_order_module.create(
        task_id="W1", title="wave", objective="Sua ham add trong calc.py",
        boundaries="Chi sua calc.py", input_context="calc.py",
        acceptance_criteria="check.py exit 0", verify_command=f"{PY} check.py",
        target_files=["calc.py"], sandbox_files=["calc.py", "check.py"],
        level=2, max_rounds=1,
    )


def test_aggregate(checks: Checks) -> None:
    roles = {"security": "approve", "qa": "approve", "perf": "approve", "docs": "approve"}
    wave = make_wave(roles)
    result = wave.review(make_order(), diff="+x", new_content="x", old_content="",
                         level=2, sink=None)
    checks.check("wave: tat ca approve -> approve", result["verdict"] == "approve", result)
    checks.check("wave: du 4 role", len(result["per_role"]) == 4, result["per_role"])
    checks.check("wave: calls = 4", wave.calls == 4, wave.calls)

    wave = make_wave({**roles, "qa": "reject"})
    result = wave.review(make_order(), diff="+x", new_content="x", old_content="",
                         level=2, sink=None)
    checks.check("wave: 1 reject -> reject", result["verdict"] == "reject", result)
    checks.check("wave: reason co ten role",
                 any(reason.startswith("[qa]") for reason in result["reasons"]),
                 result["reasons"])

    wave = make_wave(roles, boom={"perf"})
    result = wave.review(make_order(), diff="+x", new_content="x", old_content="",
                         level=2, sink=None)
    checks.check("wave: 1 loi -> reject", result["verdict"] == "reject", result)
    checks.check("wave: role loi duoc ghi",
                 any(item.get("verdict") == "error" for item in result["per_role"]),
                 result["per_role"])


def test_budget(checks: Checks) -> None:
    roles = {"security": "approve", "qa": "approve", "perf": "approve", "docs": "approve"}

    wave = make_wave(roles)
    wave.budget_left = lambda: 2
    result = wave.review(make_order(), diff="+x", new_content="x", old_content="",
                         level=2, sink=None)
    checks.check("wave: budget 2 -> heuristic fallback",
                 result["verdict"] == "approve" and result.get("fallback_reason"), result)
    checks.check("wave: budget 2 -> skip ca 4", len(result["skipped"]) == 4, result)
    checks.check("wave: calls = 0", wave.calls == 0, wave.calls)

    wave = make_wave(roles)
    wave.budget_left = lambda: 0
    result = wave.review(make_order(), diff="+x", new_content="x", old_content="",
                         level=2, sink=None)
    checks.check("wave: budget 0 -> heuristic fallback",
                 result["verdict"] == "approve" and result.get("fallback_reason"),
                 result)
    checks.check("wave: budget 0 -> skip 4", len(result["skipped"]) == 4, result)
    checks.check("wave: budget 0 -> khong goi", wave.calls == 0, wave.calls)

    wave = make_wave(roles)
    wave.budget_left = lambda: 99
    wave.review(make_order(), diff="+x", new_content="x", old_content="",
                level=2, sink=None)
    checks.check("wave: budget lon -> chay du 4", wave.calls == 4, wave.calls)


def test_parallel(checks: Checks) -> None:
    roles = {role: "approve" for role in ("security", "qa", "perf", "docs")}
    wave = make_wave(roles, delay=0.25)
    started = time.perf_counter()
    wave.review(make_order(), diff="+x", new_content="x", old_content="",
                level=2, sink=None)
    elapsed = time.perf_counter() - started
    checks.check("wave: chay song song (< 0.8s)", elapsed < 0.8, round(elapsed, 2))


def test_review_set(checks: Checks) -> None:
    roles = {role: "approve" for role in ("security", "qa", "perf", "docs")}
    wave = make_wave(roles)
    result = wave.review_set(make_order(), diffs={"a.py": "+a", "b.py": "+b"},
                             new_contents={"a.py": "a", "b.py": "b"},
                             old_contents={"a.py": "", "b.py": ""},
                             level=3, sink=None)
    checks.check("wave: review_set approve", result["verdict"] == "approve", result)
    checks.check("wave: review_set 4 role", len(result["per_role"]) == 4, result)


def test_recipe_integration(checks: Checks) -> None:
    root = TEMP_ROOT / "recipe"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "check.py").write_text(
        "from calc import add\nassert add(2, 3) == 5\nprint('OK')\n", encoding="utf-8")
    script = {"W1": [{"format": "whole", "content": "def add(a, b):\n    return a + b\n"}]}
    roles = {role: "approve" for role in ("security", "qa", "perf", "docs")}
    wave = make_wave(roles)
    report = recipe_c.run(make_order(), coder=workers.ScriptedCoder(script),
                          reviewer=wave, root=root, run_id="wave-int-1", log=False)
    checks.check("wave-recipe: applied", report["status"] == "applied", report["status"])
    checks.check("wave-recipe: reviewer_calls = 4", report["reviewer_calls"] == 4,
                 report["reviewer_calls"])
    checks.check("wave-recipe: review approve",
                 (report.get("review") or {}).get("verdict") == "approve",
                 report.get("review"))


def test_recipe_budget_fallback(checks: Checks) -> None:
    root = TEMP_ROOT / "budget-fallback"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "check.py").write_text(
        "from calc import add\nassert add(2, 3) == 5\nprint('OK')\n", encoding="utf-8")
    script = {"W1": [{"format": "whole", "content": "def add(a, b):\n    return a + b\n"}]}
    roles = {role: "approve" for role in ("security", "qa", "perf", "docs")}
    report = recipe_c.run(make_order(), coder=workers.ScriptedCoder(script),
                          reviewer=make_wave(roles), root=root,
                          run_id="wave-budget-1", log=False, max_requests=1)
    checks.check("wave-budget: verify van apply", report["status"] == "applied",
                 report["status"])
    checks.check("wave-budget: heuristic fallback",
                 bool((report.get("review") or {}).get("fallback_reason")), report.get("review"))
    checks.check("wave-budget: reviewer 0 request", report["reviewer_calls"] == 0,
                 report["reviewer_calls"])


def test_burst(checks: Checks) -> None:
    roles = {role: "approve" for role in ("security", "qa", "perf", "docs")}
    fixed = "def add(a, b):\n    return a + b\n"

    root = TEMP_ROOT / "burst-ok"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "check.py").write_text(
        "from calc import add\nassert add(2, 3) == 5\nprint('OK')\n", encoding="utf-8")
    report = recipe_c.run(make_order(), coder=CountingCoder(10, fixed),
                          reviewer=make_wave(roles), root=root,
                          run_id="wave-burst-ok", log=False)
    checks.check("burst: 10 coder + 4 reviewer -> applied",
                 report["status"] == "applied", report["status"])
    checks.check("burst: du 4 reviewer", report["reviewer_calls"] == 4,
                 report["reviewer_calls"])
    checks.check("burst: 14 request + burst flag",
                 report["requests"] == 14 and report["budget"].get("burst_used"),
                 report["budget"])

    root = TEMP_ROOT / "burst-hard"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "check.py").write_text(
        "from calc import add\nassert add(2, 3) == 5\nprint('OK')\n", encoding="utf-8")
    report = recipe_c.run(make_order(), coder=CountingCoder(20, fixed),
                          reviewer=make_wave(roles), root=root,
                          run_id="wave-burst-hard", log=False)
    checks.check("burst: 20 coder -> hard abort",
                 report["status"] == "aborted"
                 and report["budget"].get("exceeded") == "requests", report["budget"])
    checks.check("burst: error hard limit 16",
                 "hard limit 16" in (report.get("error") or ""), report.get("error"))


def test_sink(checks: Checks) -> None:
    import json
    import permissions
    root = TEMP_ROOT / "sink"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    sink = permissions.FindingsSink(root, run_id="wave-sink", log=True)
    roles = {role: "approve" for role in ("security", "qa", "perf", "docs")}
    wave = make_wave(roles)
    wave.review(make_order(), diff="+x", new_content="x", old_content="",
                level=2, sink=sink)
    events = [json.loads(line) for line in
              (root / ".agents" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
              if line.strip()]
    event = next((item for item in events
                  if item.get("event_type") == "reviewer.findings"), {})
    checks.check("wave: ledger giu per_role",
                 len((event.get("data") or {}).get("per_role") or []) == 4, event)


def test_no_regression(checks: Checks, *, fast: bool = False) -> None:
    if fast:
        print("[skip] hoi quy Phase 6 (--fast)")
        return
    started = subprocess.run([sys.executable, str(AGENTS_DIR / "tests" / "test_phase6.py"),
                              "--fast"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=900)
    checks.check("khong hoi quy: Phase 6", started.returncode == 0,
                 (started.stdout or "")[-300:])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Wave reviewer tests.")
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args(argv)

    checks = Checks()
    print("[1/9] aggregate ...")
    test_aggregate(checks)
    print("[2/9] budget ...")
    test_budget(checks)
    print("[3/9] parallel ...")
    test_parallel(checks)
    print("[4/9] review_set ...")
    test_review_set(checks)
    print("[5/9] recipe integration ...")
    test_recipe_integration(checks)
    print("[6/9] recipe budget fallback ...")
    test_recipe_budget_fallback(checks)
    print("[7/9] burst 8->16 ...")
    test_burst(checks)
    print("[8/9] ledger findings ...")
    test_sink(checks)
    print("[9/9] khong hoi quy ...")
    test_no_regression(checks, fast=args.fast)

    print("")
    print(f"WAVE: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    sys.exit(main())
