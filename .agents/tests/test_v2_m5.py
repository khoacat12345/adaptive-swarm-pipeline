#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M5 acceptance - provider selection, cost guard, multi-phase soak and faults.

    python .agents/tests/test_v2_m5.py

No model is called. Live-provider behavior is represented by config/catalog
fixtures; actual free models remain an operator-run pilot.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from v2.budget import BudgetConfig, BudgetController, Envelope, PhaseBudget  # noqa: E402
from v2.errors import BudgetBlocked, BudgetExceeded  # noqa: E402
from v2.execution import ExecutionEngine, ExecutionSeat  # noqa: E402
from v2.models import (  # noqa: E402
    select_free_seats,
    is_free,
    provider_status,
    validate_explicit,
)
from test_v2_m3 import (  # noqa: E402
    StaticCoder as Coder,
    Checks,
    cleanup,
    rmtree,
    setup_run,
)

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "v2_m5_tests"


def fake_config() -> dict:
    entries = {
        "mock/coder-free": {
            "id": "mock/coder-free", "provider": "mock", "family": "deepseek",
            "auth_status": "available", "is_free": True, "cost_tier": "free",
            "capabilities": {"code": 0.9, "reasoning": 0.8, "long_context": 0.8},
        },
        "mock/security-free": {
            "id": "mock/security-free", "provider": "mock", "family": "glm",
            "auth_status": "available", "is_free": True, "cost_tier": "free",
            "capabilities": {"code": 0.8, "reasoning": 0.8, "long_context": 0.7},
        },
        "mock/qa-free": {
            "id": "mock/qa-free", "provider": "mock", "family": "qwen",
            "auth_status": "available", "is_free": True, "cost_tier": "free",
            "capabilities": {"code": 0.8, "reasoning": 0.8, "long_context": 0.7},
        },
        "mock/perf-free": {
            "id": "mock/perf-free", "provider": "mock", "family": "openai",
            "auth_status": "available", "is_free": True, "cost_tier": "free",
            "capabilities": {"code": 0.8, "reasoning": 0.8, "long_context": 0.7},
        },
        "mock/docs-free": {
            "id": "mock/docs-free", "provider": "mock", "family": "anthropic",
            "auth_status": "available", "is_free": True, "cost_tier": "free",
            "capabilities": {"code": 0.8, "reasoning": 0.8, "long_context": 0.7},
        },
        "paid/coder": {
            "id": "paid/coder", "provider": "paid", "family": "deepseek",
            "auth_status": "available", "is_free": False, "cost_tier": "paid",
            "capabilities": {"code": 0.99, "reasoning": 0.9, "long_context": 0.9},
        },
    }
    return {
        "models": entries,
        "catalog": {"providers": {"mock": "available", "paid": "available"}},
    }


class StaticCoder:
    def __init__(self, contents):
        self.contents = contents
        self.calls = 0

    def propose(self, work_order, *, cwd, round_index, feedback):
        self.calls += 1
        index = min(round_index - 1, len(self.contents) - 1)
        return dict(self.contents[index])


def test_provider_selection(checks: Checks) -> None:
    config = fake_config()
    coder, reviewers = select_free_seats(config)
    checks.check("m5: free coder selected from config", is_free(coder, config))
    checks.check("m5: four free reviewers selected",
                 len(reviewers) == 4 and all(is_free(model, config)
                                             for model in reviewers.values()))
    checks.check("m5: provider status is exposed",
                 provider_status(config).get("mock") == "available")
    paid_blocked = False
    try:
        validate_explicit("paid/coder", reviewers, config, free_only=True)
    except Exception:
        paid_blocked = True
    checks.check("m5: paid model blocked under free-only", paid_blocked)
    paid_allowed = True
    try:
        validate_explicit("paid/coder", reviewers, config, free_only=False)
    except Exception:
        paid_allowed = False
    checks.check("m5: explicit paid model validates when allowed", paid_allowed)


def test_cost_guard(checks: Checks) -> None:
    coder = Coder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "m5-cost", coder
    )
    paid_coder = ExecutionSeat(
        "coder", "paid/coder", "deepseek", coder, is_free=False, cost_usd=0.0
    )
    try:
        blocked = False
        try:
            ExecutionEngine(runner).execute(
                run_id, coder=paid_coder, reviewers=reviewers
            )
        except BudgetBlocked:
            blocked = True
        checks.check("m5: paid model with unknown cost is blocked", blocked)
        checks.check("m5: blocked paid coder was never called", coder.calls == 0)
        checks.check("m5: cost guard leaves main unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
    finally:
        cleanup(runner, root, wt_root)


def test_multi_phase_soak(checks: Checks) -> None:
    coder = Coder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, plan = setup_run(
        "m5-soak", coder
    )
    # Replace one phase with three sequential phases.
    phases = []
    for index in range(3):
        phase = copy.deepcopy(plan["phases"][0])
        phase["id"] = f"P{index + 1}"
        phase["title"] = f"Phase {index + 1}"
        phase["depends_on"] = [f"P{index}"] if index else []
        phase["work_orders"][0]["id"] = f"S{index + 1}"
        phase["budget"]["requests"] = 10
        phase["budget"]["cost_usd"] = 0.20
        phases.append(phase)
    multi = copy.deepcopy(plan)
    multi["phases"] = phases
    multi["budget"]["requests"] = 40
    runner.submit_plan(run_id, multi)
    runner.signoff(run_id, approved_by="tests")
    try:
        result = ExecutionEngine(runner, max_repair_attempts=1).execute(
            run_id, coder=coder_seat, reviewers=reviewers
        )
        checks.check("m5: multi-phase soak reaches handoff",
                     result.status == "HANDOFF_READY")
        checks.check("m5: checkpoint per phase", len(result.checkpoints) == 3)
        checks.check("m5: coder called once per phase", coder.calls == 3)
        checks.check("m5: main unchanged after soak",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
    finally:
        cleanup(runner, root, wt_root)


def test_wall_clock(checks: Checks) -> None:
    now = [1000.0]
    config = BudgetConfig(
        envelope=Envelope(10, 0.0, 10, emergency_reserve_pct=0),
        phases={"P1": PhaseBudget(10, 0.0, 10)},
    )
    state = TEMP_ROOT / "wall"
    rmtree(state)
    state.mkdir(parents=True)
    budget = BudgetController(state, config, clock=lambda: now[0])
    rid = budget.reserve("P1", role="coder", model="free", cost_usd=0.0)
    budget.settle(rid, requests=1, cost_usd=0.0)
    now[0] += 11
    stopped = False
    try:
        budget.reserve("P1", role="coder", model="free", cost_usd=0.0)
    except BudgetExceeded:
        stopped = True
    checks.check("m5: wall-clock cap stops before call", stopped)


def test_fault_injection(checks: Checks) -> None:
    coder = Coder([{"src.py": "VALUE = 3\n"}, {"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "m5-fault", coder
    )
    # Reviewer error on the first attempt is a repairable high finding.
    reviewers["qa"] = ExecutionSeat(
        "qa", "mock/qwen-qa", "qwen",
        type("R", (), {
            "review": lambda self, role, **kw: {
                "verdict": "reject", "severity": "high",
                "evidence": ["transient failure"],
            }
        })(),
    )
    try:
        try:
            ExecutionEngine(runner, max_repair_attempts=1).execute(
                run_id, coder=coder_seat, reviewers=reviewers
            )
        except Exception:
            pass
        checks.check("m5: fault path leaves main unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
    finally:
        cleanup(runner, root, wt_root)


def main() -> int:
    rmtree(TEMP_ROOT)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    checks = Checks()
    print("[1/5] provider selection ...")
    test_provider_selection(checks)
    print("[2/5] paid-cost guard ...")
    test_cost_guard(checks)
    print("[3/5] multi-phase soak ...")
    test_multi_phase_soak(checks)
    print("[4/5] wall-clock cap ...")
    test_wall_clock(checks)
    print("[5/5] fault injection ...")
    test_fault_injection(checks)
    print("")
    print(f"V2 M5: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    rmtree(TEMP_ROOT)
    return 0 if checks.passed == checks.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
