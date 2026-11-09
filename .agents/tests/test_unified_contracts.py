#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified engine contracts: UnifiedRequest/Result, preflight, plan, progress.

    python .agents/tests/test_unified_contracts.py

The contracts are the compatibility surface between the terminal CLI, the War
Room and the engine. They must validate impossible requests early, serialize to
plain JSON, and never expose a merge action to the orchestrator.
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from unified.contracts import (  # noqa: E402
    ContractError,
    PlanDecision,
    PreflightStatus,
    RunProgress,
    UnifiedRequest,
    UnifiedResult,
)


class Checks:
    def __init__(self) -> None:
        self.total = 0
        self.failures: list[str] = []

    def check(self, name: str, condition, detail: str = "") -> bool:
        self.total += 1
        ok = bool(condition)
        if not ok:
            self.failures.append(f"{name}: {detail}" if detail else name)
        return ok

    @property
    def passed(self) -> int:
        return self.total - len(self.failures)


def _json_roundtrip(payload) -> dict:
    return json.loads(json.dumps(payload, ensure_ascii=False))


def test_request(checks: Checks) -> None:
    request = UnifiedRequest(goal="fix src.py", tier="e2")
    checks.check("request: source is goal", request.source == "goal", request.source)
    checks.check("request: validate accepts a goal", request.validate() is request)
    checks.check("request: serializes",
                 request.as_dict()["goal"] == "fix src.py", request.as_dict())
    checks.check("request: json roundtrip",
                 _json_roundtrip(request.as_dict())["tier"] == "e2")
    checks.check("request: is frozen",
                 _frozen(lambda: setattr(request, "goal", "other")))

    for kwargs, label in (
        ({"goal": "x", "plan_path": "p.json"}, "goal + plan"),
        ({"goal": "x", "order_path": "order.json"}, "goal + order"),
        ({}, "no source"),
    ):
        failed = False
        try:
            UnifiedRequest(**kwargs).validate()
        except ContractError:
            failed = True
        checks.check(f"request: rejects {label}", failed)

    for tier in ("E9", "quick"):
        failed = False
        try:
            UnifiedRequest(goal="x", tier=tier).validate()
        except ContractError:
            failed = True
        checks.check(f"request: rejects tier {tier}", failed)

    failed = False
    try:
        UnifiedRequest(goal="x", max_cost=-1).validate()
    except ContractError:
        failed = True
    checks.check("request: rejects a negative budget cap", failed)

    failed = False
    try:
        UnifiedRequest(goal="x", timeout=0).validate()
    except ContractError:
        failed = True
    checks.check("request: rejects a zero timeout", failed)

    order = UnifiedRequest(order_path="order.json")
    checks.check("request: order shim is a source", order.source == "order")

    namespace = Namespace(
        goal=None, plan=None, order="order.json", root=".", state_root=None,
        worktree_root=None, run_id="r1", base_sha="HEAD", tier="E4",
        target=["a.py", "b.py"], verify="python check.py", e1_opt_in=False,
        allow_new_files=True, preset="strict", free=False, coder_model=None,
        coder_kind="agentic", reviewer=["qa=m"], paid_cost=0.0, timeout=10,
        verify_timeout=20, max_cost=0.5, max_requests=12,
        max_wall_seconds=60, approved_by="tester", approve_usage=True,
        yes=False, dry_run=True, plan_only=False, out=None, council=True,
        planner_model=["m1", "m2"], json=True,
    )
    from_ns = UnifiedRequest.from_namespace(namespace)
    checks.check("request: namespace maps sources",
                 from_ns.order_path == "order.json" and from_ns.source == "order")
    checks.check("request: namespace maps lists",
                 from_ns.targets == ("a.py", "b.py")
                 and from_ns.reviewers == ("qa=m",)
                 and from_ns.planner_models == ("m1", "m2"))
    checks.check("request: namespace maps flags",
                 from_ns.dry_run and from_ns.approve_usage and from_ns.json_output
                 and from_ns.council is True and from_ns.allow_new_files)
    checks.check("request: namespace is valid", from_ns.validate() is from_ns)


def _frozen(action) -> bool:
    try:
        action()
    except Exception:
        return True
    return False


def test_preflight(checks: Checks) -> None:
    status = PreflightStatus(
        ok=False,
        checks=({"name": "git", "status": "bad"},),
        blocking=({"name": "git", "status": "bad"},),
        note={"bad": 1, "warn": 2},
        errors=("git missing",),
    )
    payload = status.as_dict()
    checks.check("preflight: failure is recorded", payload["ok"] is False)
    checks.check("preflight: blockers are serializable",
                 _json_roundtrip(payload)["blocking"][0]["name"] == "git")
    checks.check("preflight: notes are preserved", payload["note"]["warn"] == 2)
    checks.check("preflight: errors are preserved",
                 payload["errors"] == ["git missing"])
    ok = PreflightStatus(ok=True)
    checks.check("preflight: default is empty and ok",
                 ok.as_dict()["checks"] == [] and ok.as_dict()["blocking"] == [])


def test_plan_decision(checks: Checks) -> None:
    class FakeAdapter:
        source = "goal"
        tier = "E3"
        planner = "P2"
        plan_hash = "a" * 64
        summary = "E3/P2: one phase"
        council = {"level": "P2", "seats": ["architect", "pragmatist"]}
        source_hash = "b" * 64

    decision = PlanDecision.from_adapter(
        FakeAdapter(), caps={"max_cost_usd": 0.25}, plan_path="plan.json",
    )
    payload = decision.as_dict()
    checks.check("plan: adapter fields are copied",
                 payload["tier"] == "E3" and payload["planner"] == "P2"
                 and payload["plan_hash"] == "a" * 64)
    checks.check("plan: caps are recorded",
                 payload["caps"] == {"max_cost_usd": 0.25})
    checks.check("plan: council is recorded",
                 payload["council"]["level"] == "P2")
    checks.check("plan: json roundtrip",
                 _json_roundtrip(payload)["plan_path"] == "plan.json")
    checks.check("plan: default council is absent",
                 PlanDecision("plan", "E2", "P1", "c" * 64).as_dict()["council"]
                 is None)


def test_run_progress(checks: Checks) -> None:
    progress = RunProgress(
        run_id="run-1",
        stage="executing",
        status="PHASE_EXECUTING",
        tier="E4",
        planner="P3",
        phase="P1",
        worktree="/tmp/wt",
        usage={"required": False},
        execution={"reviewer_roles": ["security", "qa", "perf", "docs"]},
        handoff={"patch": "handoff.patch"},
        events=("run.prepared", "plan.submitted"),
    )
    payload = progress.as_dict()
    checks.check("progress: stage and status",
                 payload["stage"] == "executing"
                 and payload["status"] == "PHASE_EXECUTING")
    checks.check("progress: tier and planner",
                 payload["tier"] == "E4" and payload["planner"] == "P3")
    checks.check("progress: phase is exposed", payload["phase"] == "P1")
    checks.check("progress: events are a list",
                 payload["events"] == ["run.prepared", "plan.submitted"])
    checks.check("progress: json roundtrip",
                 _json_roundtrip(payload)["execution"]["reviewer_roles"][0]
                 == "security")


def test_result(checks: Checks) -> None:
    result = UnifiedResult(
        ok=True,
        status="HANDOFF_READY",
        exit_code=0,
        run_id="run-1",
        tier="E2",
        planner="P1",
        plan_hash="d" * 64,
        worktree="/tmp/wt",
        state_root="/tmp/state",
        plan_path="/tmp/state/plan.json",
        usage={"required": False, "reasons": []},
        execution={"reviewer_roles": ["security"]},
        handoff={"patch": "handoff.patch"},
        progress={"stage": "handoff"},
        artifacts={"proposal": "proposed_plan.json"},
    )
    payload = result.as_dict()
    checks.check("result: ok and status",
                 payload["ok"] is True and payload["status"] == "HANDOFF_READY")
    checks.check("result: run identifiers",
                 payload["run_id"] == "run-1" and payload["plan_hash"] == "d" * 64)
    checks.check("result: plan path uses the CLI name",
                 payload["plan"] == "/tmp/state/plan.json")
    checks.check("result: execution and handoff are exposed",
                 payload["execution"]["reviewer_roles"] == ["security"]
                 and payload["handoff"]["patch"] == "handoff.patch")
    checks.check("result: json roundtrip",
                 _json_roundtrip(payload)["artifacts"]["proposal"]
                 == "proposed_plan.json")
    checks.check("result: never exposes an auto-merge action",
                 "merge" not in payload and not hasattr(result, "merge"))
    failure = UnifiedResult(ok=False, status="FAILED", exit_code=2,
                            error="plan invalid")
    checks.check("result: failure keeps an exit code and error",
                 failure.as_dict()["error"] == "plan invalid"
                 and failure.as_dict()["status"] == "FAILED")
    checks.check("result: failure payload is still JSON",
                 _json_roundtrip(failure.as_dict())["ok"] is False)


def main() -> int:
    checks = Checks()
    print("[1/5] request ...")
    test_request(checks)
    print("[2/5] preflight ...")
    test_preflight(checks)
    print("[3/5] plan decision ...")
    test_plan_decision(checks)
    print("[4/5] run progress ...")
    test_run_progress(checks)
    print("[5/5] unified result ...")
    test_result(checks)
    print("")
    print(f"UNIFIED CONTRACTS: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if checks.passed == checks.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
