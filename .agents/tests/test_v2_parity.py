#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0-07 parity: v2 E3 wave and E4 meeting paths."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from v2.execution import ExecutionEngine  # noqa: E402
from test_v2_m3 import (  # noqa: E402
    Checks,
    StaticCoder,
    StaticReviewer,
    cleanup,
    seats,
    setup_run,
)


class FlipReviewer:
    def __init__(self):
        self.calls = 0

    def review(self, role, *, diff, work_order, phase):
        self.calls += 1
        if self.calls == 1:
            return {"verdict": "reject", "severity": "high",
                    "evidence": ["first pass objection"]}
        return {"verdict": "approve", "severity": "low", "evidence": []}


def _signed_with_tier(name: str, tier: str):
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, plan = setup_run(name, coder)
    changed = copy.deepcopy(plan)
    changed["execution_tier"] = tier
    runner.submit_plan(run_id, changed)
    runner.signoff(run_id, approved_by="parity")
    return runner, root, wt_root, run_id, coder_seat, reviewers, changed


def test_e3_wave(checks: Checks) -> None:
    runner, root, wt_root, run_id, coder_seat, reviewers, plan = _signed_with_tier(
        "parity-e3", "E3")
    try:
        result = ExecutionEngine(runner).execute(
            run_id, coder=coder_seat, reviewers=reviewers,
        )
        checks.check("E3: reaches handoff", result.status == "HANDOFF_READY",
                     result.as_dict())
        checks.check("E3: four reviewers called",
                     all(seat.backend.calls == 1 for seat in reviewers.values()),
                     {role: seat.backend.calls for role, seat in reviewers.items()})
        events = runner.load(run_id).read_events()
        checks.check("E3: no meeting protocol",
                     not any(event.get("event_type") == "meeting.opened"
                             for event in events), [event.get("event_type") for event in events])
        checks.check("E3: worktree changed, main unchanged",
                     (runner.workspace(run_id).path / "src.py").read_text(encoding="utf-8")
                     == "VALUE = 2\n"
                     and (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
    finally:
        cleanup(runner, root, wt_root)


def test_e4_meeting(checks: Checks) -> None:
    runner, root, wt_root, run_id, coder_seat, reviewers, plan = _signed_with_tier(
        "parity-e4", "E4")
    try:
        result = ExecutionEngine(runner).execute(
            run_id, coder=coder_seat, reviewers=reviewers,
        )
        checks.check("E4: reaches handoff", result.status == "HANDOFF_READY",
                     result.as_dict())
        events = runner.load(run_id).read_events()
        types = [event.get("event_type") for event in events]
        checks.check("E4: meeting opened", "meeting.opened" in types, types)
        checks.check("E4: meeting transcript exists",
                     "meeting.message" in types, types)
        checks.check("E4: four reviewers called",
                     all(seat.backend.calls == 1 for seat in reviewers.values()),
                     {role: seat.backend.calls for role, seat in reviewers.items()})
    finally:
        cleanup(runner, root, wt_root)


def test_e4_round2(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, plan = setup_run(
        "parity-e4-round2", coder)
    try:
        changed = copy.deepcopy(plan)
        changed["execution_tier"] = "E4"
        runner.submit_plan(run_id, changed)
        runner.signoff(run_id, approved_by="parity")
        flip = FlipReviewer()
        from dataclasses import replace
        reviewers["security"] = replace(reviewers["security"], backend=flip)
        result = ExecutionEngine(runner).execute(
            run_id, coder=coder_seat, reviewers=reviewers,
        )
        checks.check("E4: round-2 conflict resolves", result.status == "HANDOFF_READY",
                     result.as_dict())
        checks.check("E4: security rechecked in round 2", flip.calls == 2,
                     flip.calls)
        events = runner.load(run_id).read_events()
        types = [event.get("event_type") for event in events]
        checks.check("E4: round-2 meeting events",
                     "meeting.conflict" in types and "meeting.message" in types, types)
    finally:
        cleanup(runner, root, wt_root)


def main() -> int:
    checks = Checks()
    print("[1/3] E3 wave ...")
    test_e3_wave(checks)
    print("[2/3] E4 meeting ...")
    test_e4_meeting(checks)
    print("[3/3] E4 round 2 ...")
    test_e4_round2(checks)
    print(f"V2 PARITY: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
