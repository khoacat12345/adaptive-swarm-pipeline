#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 5 - router theo loai viec + FallbackReviewer.

    python .agents/tests/test_phase5.py
    python .agents/tests/test_phase5.py --fast   # bo qua hoi quy Level 3 / Phase 4

DoD:
  1. classify_work nhan dung W0-W8 tu tin hieu (file, risk, context, modality...).
  2. ROUTES hop le: model opencode, main != escalate, vai kiem toan khac ho.
  3. FallbackReviewer: model dau fail -> model sau; tat ca fail -> raise.
  4. recipe_c --route ghi event router.decided + report["route"].
  5. khong hoi quy Level 3 + Phase 4.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import recipe_c                        # noqa: E402
import router                          # noqa: E402
import workers                         # noqa: E402
import work_order as work_order_module  # noqa: E402

PY = f'"{sys.executable}"'
TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "phase5_tests"


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


def make_order(**overrides) -> work_order_module.WorkOrder:
    data = {
        "task_id": "P5",
        "title": "phase 5",
        "objective": "Sua ham add trong calc.py de tra ve tong",
        "boundaries": "Chi sua calc.py",
        "input_context": "calc.py co ham add",
        "acceptance_criteria": "check.py exit 0 va add(2,3)==5",
        "verify_command": "python check.py",
        "target_files": ["calc.py"],
        "sandbox_files": ["calc.py", "check.py"],
        "level": 1,
        "max_rounds": 1,
    }
    data.update(overrides)
    return work_order_module.WorkOrder(**data)


# --------------------------------------------------------------------------
def test_classify(checks: Checks) -> None:
    cases = [
        ("W1", make_order()),
        ("W2", make_order(level=2)),
        ("W2", make_order(level=3, target_files=["a.py", "b.py"])),
        ("W3", make_order(objective="Sua auth token trong calc.py")),
        ("W5", make_order(target_files=["test_calc.py"])),
        ("W6", make_order(target_files=["app.tsx"])),
        ("W6", make_order(target_files=["calc.py"]), {"has_image": True}),
        ("W7", make_order(), {"context_tokens": 150_000}),
        ("W8", make_order(), {"failures": 2}),
        ("W8", make_order(), {"reviewer_rejected": True}),
        ("W0", make_order(target_files=[], sandbox_files=[])),
        ("W4", make_order(level=3, target_files=["a.py", "b.py"]), {"needs_plan": True}),
    ]
    for item in cases:
        expected, order = item[0], item[1]
        signals = item[2] if len(item) > 2 else {}
        got, detected = router.classify_work(order, **signals)
        checks.check(f"classify {expected}", got == expected,
                     f"duoc {got} signals={detected}")

    # uu tien: W8 thang W6; W6 thang W3 (UI co the cham auth)
    got, _ = router.classify_work(make_order(target_files=["app.tsx"]), failures=2)
    checks.check("classify: W8 thang W6", got == "W8", got)
    got, _ = router.classify_work(make_order(objective="auth", target_files=["app.tsx"]))
    checks.check("classify: W6 thang W3", got == "W6", got)


def test_routes(checks: Checks) -> None:
    checks.check("routes: validate_roles sach", router.validate_roles() == [],
                 router.validate_roles())
    for name, entry in router.ROUTES.items():
        chain = [m for m in (entry["main"], entry["escalate"]) if m]
        checks.check(f"routes {name}: co main", bool(entry["main"]))
        checks.check(f"routes {name}: model thuoc FAMILY",
                     all(m in router.FAMILY for m in chain), chain)
        checks.check(f"routes {name}: main != escalate",
                     not entry["escalate"] or entry["main"] != entry["escalate"])
    for name in ("W3", "W5", "W8"):
        entry = router.ROUTES[name]
        checks.check(f"routes {name}: kiem toan khac ho",
                     router.FAMILY[entry["main"]] != router.FAMILY[entry["escalate"]],
                     (router.FAMILY[entry["main"]], router.FAMILY[entry["escalate"]]))

    bad = dict(router.ROUTES)
    bad["W3"] = {"main": router.MODELS["glm-5.3"],
                 "escalate": router.MODELS["glm-flash-free"], "why": "bad"}
    problems = router.validate_roles(bad)
    checks.check("routes: bat cung ho vai kiem toan", any("khac ho" in p for p in problems),
                 problems)

    plan = router.route(make_order(level=2))
    checks.check("route: tra chain + work_type", plan["work_type"] == "W2"
                 and plan["chain"] == [plan["main"], plan["escalate"]], plan)


def test_fallback_reviewer(checks: Checks) -> None:
    def boom(prompt):
        raise workers.WorkerError("429", kind="rate_limit")

    def approve(prompt):
        return '{"verdict":"approve","reasons":[]}'

    reviewer = workers.FallbackReviewer(
        ["bad", "good"],
        factory=lambda model: workers.OpenCodeReviewer(
            model, runner=boom if model == "bad" else approve,
            raise_on_error=True))
    result = reviewer.review(make_order(), diff="+x", new_content="x",
                             old_content="", level=2, sink=None)
    checks.check("fallback-reviewer: dung model sau", result["verdict"] == "approve")
    checks.check("fallback-reviewer: dem 2 lan thu", reviewer.calls == 2, reviewer.calls)
    checks.check("fallback-reviewer: ghi 1 fail",
                 len([a for a in reviewer.attempts if a["status"] == "fail"]) == 1,
                 reviewer.attempts)

    only_bad = workers.FallbackReviewer(
        ["bad"], factory=lambda model: workers.OpenCodeReviewer(model, runner=boom, raise_on_error=True))
    raised = ""
    try:
        only_bad.review(make_order(), diff="+x", new_content="x", old_content="",
                        level=2, sink=None)
    except workers.WorkerError as exc:
        raised = getattr(exc, "kind", "")
    checks.check("fallback-reviewer: het model -> exhausted", raised == "exhausted", raised)

    multi = workers.FallbackReviewer(
        ["good"], factory=lambda model: workers.OpenCodeReviewer(model, runner=approve, raise_on_error=True))
    result = multi.review_set(make_order(level=3, target_files=["a.py", "b.py"]),
                              diffs={"a.py": "+a", "b.py": "+b"},
                              new_contents={"a.py": "a", "b.py": "b"},
                              old_contents={"a.py": "", "b.py": ""},
                              level=3, sink=None)
    checks.check("fallback-reviewer: review_set", result["verdict"] == "approve")


def test_route_in_recipe(checks: Checks) -> None:
    root = TEMP_ROOT / "route"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "check.py").write_text(
        "from calc import add\nassert add(2, 3) == 5\nprint('OK')\n", encoding="utf-8")
    order = make_order(level=2)
    plan = router.route(order)
    script = {"P5": [{"format": "whole",
                      "content": "def add(a, b):\n    return a + b\n"}]}
    report = recipe_c.run(order, coder=workers.ScriptedCoder(script),
                          reviewer=workers.HeuristicReviewer(), root=root,
                          run_id="p5-route-1", use_memory=False, route_plan=plan)
    checks.check("recipe: applied", report["status"] == "applied", report["status"])
    checks.check("recipe: report co route", (report.get("route") or {}).get("work_type") == "W2",
                 report.get("route"))
    text = (root / ".agents" / "ledger.jsonl").read_text(encoding="utf-8")
    checks.check("recipe: ledger co router.decided", "router.decided" in text)


def test_no_regression(checks: Checks, *, fast: bool = False) -> None:
    if fast:
        print("[skip] hoi quy Level 3 / Phase 4 (--fast)")
        return
    suites = [
        ("Level 3", [sys.executable, str(AGENTS_DIR / "tests" / "test_level3.py"), "--fast"]),
        ("Phase 4", [sys.executable, str(AGENTS_DIR / "tests" / "test_phase4.py"), "--fast"]),
    ]
    for name, command in suites:
        started = subprocess.run(command, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=900)
        tail = (started.stdout or "")[-300:]
        checks.check(f"khong hoi quy: {name}", started.returncode == 0,
                     f"exit={started.returncode} tail={tail}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Phase 5 acceptance tests.")
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args(argv)

    checks = Checks()
    print("[1/5] classify_work ...")
    test_classify(checks)
    print("[2/5] routes / validate_roles ...")
    test_routes(checks)
    print("[3/5] FallbackReviewer ...")
    test_fallback_reviewer(checks)
    print("[4/5] recipe --route ...")
    test_route_in_recipe(checks)
    print("[5/5] khong hoi quy ...")
    test_no_regression(checks, fast=args.fast)

    print("")
    print(f"PHASE 5: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    sys.exit(main())
