#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0-07: four execution tiers, three planner levels and capability wiring."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
TESTS_DIR = AGENTS_DIR / "tests"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import capability_registry  # noqa: E402
import job  # noqa: E402
import planner  # noqa: E402
import tier_policy  # noqa: E402
import workers  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "tier_tests"


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


def test_selection(checks: Checks) -> None:
    cases = [
        (dict(reviewer="none"), "E1", "P1"),
        (dict(reviewer="heuristic"), "E2", "P1"),
        (dict(reviewer="llm"), "E2", "P1"),
        (dict(reviewer="wave"), "E3", "P2"),
        (dict(mode="meeting", reviewer="wave"), "E4", "P3"),
        (dict(reviewer="wave", reviewer_kind="meeting-reviewer"), "E4", "P3"),
    ]
    for kwargs, expected_tier, expected_planner in cases:
        decision = tier_policy.select_tier(**kwargs)
        checks.check(f"tier: {kwargs} -> {expected_tier}",
                     decision.tier == expected_tier, decision.as_dict())
        checks.check(f"planner: {kwargs} -> {expected_planner}",
                     decision.planner == expected_planner, decision.as_dict())
    checks.check("planner: E1/E2 one planner",
                 tier_policy.required_planners("E1") == 1
                 and tier_policy.required_planners("E2") == 1)
    checks.check("planner: E3 two planners",
                 tier_policy.required_planners("E3") == 2)
    checks.check("planner: E4 three planners",
                 tier_policy.required_planners("E4") == 3)
    risk = tier_policy.select_tier(reviewer="heuristic", risk=True)
    checks.check("tier: risk upgrades E2 to E3", risk.tier == "E3", risk.as_dict())


def test_capabilities(checks: Checks) -> None:
    for tier in tier_policy.TIER_ORDER:
        names = [cap.name for cap in capability_registry.for_tier(tier)]
        checks.check(f"capability: {tier} has repo_map", "repo_map" in names, names)
        checks.check(f"capability: {tier} has aci", "aci" in names, names)
        checks.check(f"capability: {tier} has lessons", "lessons" in names, names)
        checks.check(f"capability: {tier} has events", "events" in names, names)
    matrix = capability_registry.matrix()
    checks.check("capability: matrix has all tiers",
                 matrix["tiers"] == list(tier_policy.TIER_ORDER), matrix)
    checks.check("capability: wiring is honest",
                 all(item["wiring"] in {"partial", "live", "not_wired"}
                     for item in matrix["capabilities"].values()), matrix)
    report = capability_registry.report()
    checks.check("capability: report includes wiring", "wiring" in report, report)


def test_planner_council(checks: Checks) -> None:
    calls: list[int] = []

    def fake(goal, root="."):
        calls.append(1)
        return {
            "job_id": "J1", "objective": goal,
            "subtasks": [
                {"id": f"S{i}", "target_files": ["a.py"]}
                for i in range(1, len(calls) + 1)
            ],
        }

    one = planner.generate_council("x", planners=1, generate_fn=fake)
    checks.check("planner: P1 calls once", len(calls) == 1, calls)
    checks.check("planner: P1 returns a plan", len(one["subtasks"]) == 1, one)
    calls.clear()
    three = planner.generate_council("x", planners=3, generate_fn=fake)
    checks.check("planner: P3 calls three times", len(calls) == 3, calls)
    checks.check("planner: P3 chooses smallest proposal",
                 len(three["subtasks"]) == 1, three)


def test_timeline(checks: Checks) -> None:
    events = [
        {"event_type": "tier.selected", "timestamp": "t", "run_id": "r",
         "result": {"status": "info"}},
        {"event_type": "recipe.coder", "timestamp": "t", "run_id": "r"},
        {"event_type": "recipe.draft_verify", "timestamp": "t", "run_id": "r"},
        {"event_type": "reviewer.findings", "timestamp": "t", "run_id": "r"},
        {"event_type": "recipe.apply", "timestamp": "t", "run_id": "r"},
        {"event_type": "job.done", "timestamp": "t", "run_id": "r"},
        {"event_type": "noise", "timestamp": "t", "run_id": "r"},
    ]
    timeline = tier_policy.compact_timeline(events)
    checks.check("timeline: compact stages",
                 [item["stage"] for item in timeline] ==
                 ["tier", "coder", "verify", "reviewer", "apply", "done"], timeline)
    checks.check("timeline: ignores noise",
                 all(item["event_type"] != "noise" for item in timeline), timeline)


def make_root(name: str) -> Path:
    root = TEMP_ROOT / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "a.py").write_text("def a():\n    return 0\n", encoding="utf-8")
    (root / "check.py").write_text(
        "from a import a\nassert a() == 1\n", encoding="utf-8")
    return root


def test_job_state(checks: Checks) -> None:
    root = make_root("job")
    plan = {
        "job_id": "J1", "objective": "tier test",
        "subtasks": [{
            "id": "S1", "title": "a", "objective": "a",
            "verify_command": f'"{sys.executable}" check.py',
            "target_files": ["a.py"], "sandbox_files": ["a.py", "check.py"],
            "level": 1, "max_rounds": 1, "depends_on": [],
            "scripted_patches": [{"format": "whole",
                                  "content": "def a():\n    return 1\n"}],
        }],
    }
    review = workers.HeuristicReviewer()
    report = job.run_job(
        plan, root=root, use_router=False, log=True,
        reviewer=review, reviewer_kind="heuristic",
        coder_factory=lambda order, chain: workers.ScriptedCoder({
            order.task_id: order_task_patch(order),
        }),
    )
    checks.check("job: report has tier", report.get("tier", {}).get("tier") == "E2",
                 report.get("tier"))
    checks.check("job: state has tier",
                 (job.read_state(root).get("tier") or {}).get("tier") == "E2",
                 job.read_state(root))
    checks.check("job: state has capabilities",
                 "repo_map" in (job.read_state(root).get("capabilities") or []),
                 job.read_state(root))


def order_task_patch(order):
    return [{"format": "whole", "content": "def a():\n    return 1\n"}]


def main() -> int:
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    TEMP_ROOT.mkdir(parents=True)
    checks = Checks()
    test_selection(checks)
    test_capabilities(checks)
    test_planner_council(checks)
    test_timeline(checks)
    test_job_state(checks)
    print(f"TIERS: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
