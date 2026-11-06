#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0-06 acceptance: conditional Usage approval policy."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

import approval_policy as policy  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "approval_policy_tests"
FREE_CATALOG = {
    "models": {
        "free/coder": {
            "id": "free/coder", "family": "free", "is_free": True,
            "cost_tier": "free", "pricing": None,
        },
        "free/reviewer": {
            "id": "free/reviewer", "family": "free2", "is_free": True,
            "cost_tier": "free", "pricing": None,
        },
        "paid/cheap": {
            "id": "paid/cheap", "family": "paid", "is_free": False,
            "cost_tier": "cheap", "pricing": None,
        },
    }
}


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


def make_plan(*, subtasks: int = 1, max_rounds: int = 1) -> dict:
    return {
        "job_id": "J1",
        "objective": "test",
        "subtasks": [
            {
                "id": f"S{index}",
                "title": "x",
                "objective": "x",
                "verify_command": "python check.py",
                "target_files": ["a.py"],
                "sandbox_files": ["a.py"],
                "level": 1,
                "max_rounds": max_rounds,
                "depends_on": [],
            }
            for index in range(1, subtasks + 1)
        ],
    }


def make_root() -> Path:
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    TEMP_ROOT.mkdir(parents=True)
    return TEMP_ROOT


def test_quick_path(checks: Checks) -> None:
    decision = policy.evaluate(
        make_plan(), mode="panel", reviewer="heuristic",
        models=["free/coder"], catalog=FREE_CATALOG,
    )
    checks.check("quick: free panel does not require approval",
                 decision.required is False, decision.as_dict())
    checks.check("quick: request estimate exists", decision.estimate.requests >= 1,
                 decision.estimate.as_dict())
    checks.check("quick: free estimate is zero",
                 decision.estimate.cost_usd == 0.0, decision.estimate.as_dict())
    checks.check("quick: no reasons", decision.reasons == (), decision.reasons)

    no_models = policy.evaluate(
        make_plan(), mode="panel", reviewer="heuristic", models=[],
    )
    checks.check("quick: missing model list does not force approval",
                 no_models.required is False, no_models.as_dict())

    two = policy.evaluate(
        make_plan(subtasks=2), mode="panel", reviewer="heuristic",
        models=["free/coder"], catalog=FREE_CATALOG,
    )
    checks.check("quick: two small subtasks remain below threshold",
                 two.required is False, two.as_dict())


def test_expensive_paths(checks: Checks) -> None:
    wave = policy.evaluate(
        make_plan(), mode="panel", reviewer="wave",
        models=["free/coder", "free/reviewer"], catalog=FREE_CATALOG,
    )
    checks.check("wave: requires approval", wave.required is True, wave.as_dict())
    checks.check("wave: reason recorded", "reviewer=wave" in wave.reasons, wave.reasons)

    meeting = policy.evaluate(
        make_plan(), mode="meeting", reviewer="wave",
        models=["free/coder", "free/reviewer"], catalog=FREE_CATALOG,
    )
    checks.check("meeting: requires approval", meeting.required is True, meeting.as_dict())
    checks.check("meeting: reason recorded", "mode=meeting" in meeting.reasons,
                 meeting.reasons)

    paid = policy.evaluate(
        make_plan(), mode="panel", reviewer="heuristic",
        models=["paid/cheap"], catalog=FREE_CATALOG,
    )
    checks.check("paid: requires approval", paid.required is True, paid.as_dict())
    checks.check("paid: reason recorded", "paid_model" in paid.reasons, paid.reasons)

    multi = policy.evaluate(
        make_plan(), mode="panel", reviewer="heuristic",
        models=["free/coder"], catalog=FREE_CATALOG, multi_phase=True,
    )
    checks.check("multi-phase: requires approval", multi.required is True,
                 multi.as_dict())

    overnight = policy.evaluate(
        make_plan(), mode="panel", reviewer="heuristic",
        models=["free/coder"], catalog=FREE_CATALOG, overnight=True,
    )
    checks.check("overnight: requires approval", overnight.required is True,
                 overnight.as_dict())


def test_thresholds(checks: Checks) -> None:
    many = policy.evaluate(
        make_plan(subtasks=10), mode="panel", reviewer="heuristic",
        models=["free/coder"], catalog=FREE_CATALOG,
        thresholds=policy.Thresholds(request_threshold=3, cost_threshold_usd=1.0,
                                     ttl_seconds=60),
    )
    checks.check("threshold: request trigger", many.required is True, many.reasons)
    checks.check("threshold: request reason",
                 any("estimated_requests" in item for item in many.reasons),
                 many.reasons)

    cheap = policy.evaluate(
        make_plan(), mode="panel", reviewer="heuristic",
        models=["paid/cheap"], catalog=FREE_CATALOG,
        thresholds=policy.Thresholds(request_threshold=100,
                                     cost_threshold_usd=0.001, ttl_seconds=60),
    )
    checks.check("threshold: cost trigger", cheap.required is True, cheap.reasons)
    checks.check("threshold: cost reason",
                 any("estimated_cost" in item for item in cheap.reasons),
                 cheap.reasons)


def test_approval_record(checks: Checks) -> None:
    root = make_root()
    plan = make_plan()
    # Force a required decision for the record test.
    decision = policy.evaluate(
        plan, mode="panel", reviewer="wave",
        models=["free/coder"], catalog=FREE_CATALOG,
    )
    path = policy.approval_path(root)
    record = policy.create_approval(path, decision, approved_by="test", now=1000)
    checks.check("approval: record binds plan hash",
                 record["plan_hash"] == decision.plan_hash, record)
    checks.check("approval: record binds estimate hash",
                 record["estimate_hash"] == decision.estimate_hash, record)
    checks.check("approval: valid record accepted",
                 policy.assert_approval(path, decision, now=1001)["approved_by"] == "test")
    checks.check("approval: status approved",
                 policy.approval_status(path, decision, now=1001)["status"] == "approved")

    checks.check("approval: missing record is required",
                 policy.approval_status(path.with_name("missing.json"), decision,
                                        now=1001)["status"] == "awaiting")
    try:
        policy.assert_approval(path.with_name("missing.json"), decision, now=1001)
        raised = False
    except policy.ApprovalRequired:
        raised = True
    checks.check("approval: missing raises ApprovalRequired", raised)

    changed = make_plan(subtasks=2)
    changed_decision = policy.evaluate(
        changed, mode="panel", reviewer="wave",
        models=["free/coder"], catalog=FREE_CATALOG,
    )
    try:
        policy.assert_approval(path, changed_decision, now=1001)
        raised = False
    except policy.ApprovalInvalid:
        raised = True
    checks.check("approval: changed plan invalidates", raised)

    expired = policy.create_approval(path, decision, ttl_seconds=10, now=1000)
    checks.check("approval: expiry timestamp", expired["expires_at"] == 1010)
    try:
        policy.assert_approval(path, decision, now=2000)
        raised = False
    except policy.ApprovalExpired:
        raised = True
    checks.check("approval: expired raises", raised)

    # A different mode changes the estimate hash.
    other_mode = policy.evaluate(
        plan, mode="meeting", reviewer="wave",
        models=["free/coder"], catalog=FREE_CATALOG,
    )
    try:
        policy.assert_approval(path, other_mode, now=1001)
        raised = False
    except policy.ApprovalInvalid:
        raised = True
    checks.check("approval: mode change invalidates", raised)


def main() -> int:
    checks = Checks()
    test_quick_path(checks)
    test_expensive_paths(checks)
    test_thresholds(checks)
    test_approval_record(checks)
    print(f"APPROVAL POLICY: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
