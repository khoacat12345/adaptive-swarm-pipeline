#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2 acceptance - planning council, plan validation and sign-off.

    python .agents/tests/test_v2_m2.py

Uses temporary Git repositories and deterministic fake planners. No model is
called and the real project is never modified.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2 import isolation  # noqa: E402
from v2.errors import PlanChangedError  # noqa: E402
from v2.plan import (  # noqa: E402
    PlanValidationError,
    build_example,
    plan_hash,
    validate_plan,
)
from v2.planning import (  # noqa: E402
    PLANNER_ROLES,
    PlanningContext,
    PlanningCouncil,
    PlanningError,
    fake_planner,
)
from v2 import web as v2_web  # noqa: E402
from v2.runner import V2Runner  # noqa: E402
from v2.signoff import assert_signoff, load_signoff, needs_reapproval  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "v2_m2_tests"


def rmtree(path: str | Path) -> None:
    def onerror(func, target, exc_info):
        try:
            Path(target).chmod(0o700)
            func(target)
        except OSError:
            pass

    try:
        shutil.rmtree(path, onerror=onerror)
    except OSError:
        pass


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


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=60,
    )


def make_repo(name: str) -> Path:
    root = TEMP_ROOT / name
    rmtree(root)
    root.mkdir(parents=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Tests")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    (root / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return root


def make_runner(name: str) -> tuple[V2Runner, Path, Path]:
    root = make_repo(name)
    wt_root = TEMP_ROOT / f"{name}-wt"
    rmtree(wt_root)
    runner = V2Runner(root, state_root=root / "state", worktree_root=wt_root)
    return runner, root, wt_root


def cleanup(runner: V2Runner, root: Path, wt_root: Path) -> None:
    try:
        # Find the run from state, then remove its worktree.
        runs = sorted((root / "state" / "runs").glob("*"))
        for run in runs:
            manifest = run / "run_manifest.json"
            if not manifest.exists():
                continue
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            ws = payload.get("workspace") or {}
            if ws:
                try:
                    isolation.remove_worktree(
                        isolation.Workspace.from_dict(ws), keep_branch=False
                    )
                except Exception:
                    pass
    finally:
        rmtree(wt_root)
        rmtree(root)


def test_plan_validation(checks: Checks) -> None:
    root = make_repo("plan-validation")
    try:
        base = git(root, "rev-parse", "HEAD").stdout.strip()
        plan = build_example("run-plan", base, phase_ids=["P1"])
        validated = validate_plan(plan, worktree=root)
        checks.check("plan: valid example accepted", validated["plan_hash"] == plan_hash(plan))
        checks.check("plan: hash is stable", validated["plan_hash"] == plan_hash(copy.deepcopy(plan)))
        changed = copy.deepcopy(plan)
        changed["goal"] = "different"
        checks.check("plan: hash changes with content", plan_hash(changed) != plan_hash(plan))

        missing_verify = copy.deepcopy(plan)
        del missing_verify["phases"][0]["work_orders"][0]["verify_command"]
        failed = False
        try:
            validate_plan(missing_verify, worktree=root)
        except PlanValidationError:
            failed = True
        checks.check("plan: missing verify is rejected", failed)

        cycle = copy.deepcopy(plan)
        cycle["phases"].append(copy.deepcopy(cycle["phases"][0]))
        cycle["phases"][0]["id"] = "P1"
        cycle["phases"][1]["id"] = "P2"
        cycle["phases"][0]["depends_on"] = ["P2"]
        cycle["phases"][1]["depends_on"] = ["P1"]
        failed = False
        try:
            validate_plan(cycle, worktree=root)
        except PlanValidationError:
            failed = True
        checks.check("plan: dependency cycle is rejected", failed)

        outside = copy.deepcopy(plan)
        outside["phases"][0]["work_orders"][0]["target_files"] = ["../outside.py"]
        failed = False
        try:
            validate_plan(outside, worktree=root)
        except PlanValidationError:
            failed = True
        checks.check("plan: path escape is rejected", failed)

        missing = copy.deepcopy(plan)
        missing["phases"][0]["work_orders"][0]["target_files"] = ["nope.py"]
        missing["phases"][0]["allowed_paths"] = ["nope.py"]
        failed = False
        try:
            validate_plan(missing, worktree=root)
        except PlanValidationError:
            failed = True
        checks.check("plan: missing target is rejected", failed)

        budget = copy.deepcopy(plan)
        budget["budget"]["requests"] = 10
        failed = False
        try:
            validate_plan(budget, worktree=root)
        except PlanValidationError:
            failed = True
        checks.check("plan: budget overflow is rejected", failed)
    finally:
        rmtree(root)


def test_council(checks: Checks) -> None:
    root = make_repo("council")
    try:
        base = git(root, "rev-parse", "HEAD").stdout.strip()
        context = PlanningContext(
            goal="council test",
            run_id="run-council",
            base_sha=base,
            worktree=root,
            budget={"requests": 120, "cost_usd": 3.5, "wall_seconds": 36000},
            max_rounds=2,
        )
        plan = build_example("run-council", base, phase_ids=["P1"])
        council = PlanningCouncil({role: fake_planner(lambda r, p: plan) for role in PLANNER_ROLES})
        result = council.run(context)
        checks.check("council: three proposals collected", len(result.proposals) == 3)
        checks.check("council: two rounds executed", result.rounds == 2)
        checks.check("council: output phase present",
                     result.plan.get("phases", [{}])[0].get("id") == "P1")
        checks.check("council: output hash matches", result.plan_hash == plan_hash(result.plan))

        lead_plan = copy.deepcopy(plan)
        lead_plan["goal"] = "lead merged"
        lead_calls: list[str] = []

        def lead(role: str, prompt: str) -> str:
            lead_calls.append(role)
            return json.dumps(lead_plan)

        with_lead = PlanningCouncil(
            {role: fake_planner(lambda r, p: plan) for role in PLANNER_ROLES},
            lead=lead,
        ).run(context)
        checks.check("council: lead was called", lead_calls == ["lead"])
        checks.check("council: lead plan used", with_lead.lead_used)
        checks.check("council: lead goal used", with_lead.plan.get("goal") == "lead merged")

        bad = copy.deepcopy(plan)
        bad["phases"][0]["work_orders"][0]["target_files"] = ["missing.py"]
        bad["phases"][0]["allowed_paths"] = ["missing.py"]
        failed = False
        try:
            PlanningCouncil(
                {role: fake_planner(lambda r, p: bad) for role in PLANNER_ROLES}
            ).run(context)
        except PlanningError:
            failed = True
        checks.check("council: invalid plans fail closed", failed)

        missing = False
        try:
            PlanningCouncil({"architect": fake_planner(lambda r, p: plan)})
        except PlanningError:
            missing = True
        checks.check("council: missing seats fail closed", missing)
    finally:
        rmtree(root)


def test_signoff(checks: Checks) -> None:
    runner, root, wt_root = make_runner("signoff")
    try:
        prepared = runner.prepare("signoff test", phase_ids=["P1"])
        run_id = prepared["run_id"]
        workspace = runner.workspace(run_id)
        base = workspace.base_sha
        plan = build_example(run_id, base, phase_ids=["P1", "P2"])
        submitted = runner.submit_plan(run_id, plan)
        checks.check("signoff: plan enters AWAITING_SIGNOFF",
                     submitted["status"] == "AWAITING_SIGNOFF")
        checks.check("signoff: before signoff is unsigned",
                     needs_reapproval(runner.load(run_id), plan) is True)
        signed = runner.signoff(run_id)
        checks.check("signoff: state becomes PROVISIONING",
                     signed["status"] == "PROVISIONING")
        record = load_signoff(runner.load(run_id))
        checks.check("signoff: record has plan hash",
                     record.get("plan_hash") == submitted["plan_hash"])
        checks.check("signoff: record has nonce", bool(record.get("nonce")))
        checks.check("signoff: assert signed passes",
                     assert_signoff(runner.load(run_id), plan)["plan_hash"] == submitted["plan_hash"])
        checks.check("signoff: budget was reconfigured",
                     (root / "state" / "runs" / run_id / "budget.json").exists())

        changed = copy.deepcopy(plan)
        changed["goal"] = "changed after signoff"
        changed_failed = False
        try:
            assert_signoff(runner.load(run_id), changed)
        except PlanChangedError:
            changed_failed = True
        checks.check("signoff: changed plan requires reapproval", changed_failed)
        checks.check("signoff: needs_reapproval is true", needs_reapproval(runner.load(run_id), changed))

        runner.submit_plan(run_id, changed)
        checks.check("signoff: resubmission stays awaiting",
                     runner.load(run_id).load()["status"] == "AWAITING_SIGNOFF")
        checks.check("signoff: old approval no longer matches",
                     needs_reapproval(runner.load(run_id), changed) is True)
    finally:
        cleanup(runner, root, wt_root)


def test_web(checks: Checks) -> None:
    root = make_repo("web")
    wt_root = TEMP_ROOT / "web-wt"
    rmtree(wt_root)
    try:
        # The dashboard uses the default .agents/tmp/v2 state root.
        runner = V2Runner(root, worktree_root=wt_root)
        prepared = runner.prepare("web signoff", phase_ids=["P1"])
        run_id = prepared["run_id"]
        token = prepared["signoff_token"]
        plan = build_example(
            run_id, prepared["workspace"]["base_sha"], phase_ids=["P1"]
        )
        runner.submit_plan(run_id, plan)
        detail = v2_web.run_detail(root, run_id)
        checks.check("web: plan is visible", detail["plan"].get("run_id") == run_id)
        checks.check("web: plan can be signed", detail["can_sign"] is True)
        checks.check("web: run is listed",
                     any(item["run_id"] == run_id for item in v2_web.list_runs(root)["runs"]))
        wrong = False
        try:
            v2_web.approve(root, run_id, "wrong-token")
        except PermissionError:
            wrong = True
        checks.check("web: wrong token is rejected", wrong)
        signed = v2_web.approve(root, run_id, token)
        checks.check("web: approval transitions to provisioning",
                     signed["status"] == "PROVISIONING")
        detail = v2_web.run_detail(root, run_id)
        checks.check("web: signed plan is marked", detail["signed"] is True)
        checks.check("web: signed plan cannot be signed twice", detail["can_sign"] is False)
        paused = v2_web.control(root, run_id, token, "pause")
        checks.check("web: pause control works", paused["status"] == "PAUSED")
        resumed = v2_web.control(root, run_id, token, "resume")
        checks.check("web: resume control works", resumed["status"] == "PROVISIONING")
        stopped = v2_web.control(root, run_id, token, "stop")
        checks.check("web: stop control works", stopped["status"] == "STOPPED")
        isolation.remove_worktree(runner.workspace(run_id), keep_branch=False)
    finally:
        rmtree(wt_root)
        rmtree(root)


def main() -> int:
    rmtree(TEMP_ROOT)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    checks = Checks()
    print("[1/4] plan schema and hash ...")
    test_plan_validation(checks)
    print("[2/4] planning council ...")
    test_council(checks)
    print("[3/4] sign-off and reapproval ...")
    test_signoff(checks)
    print("[4/4] web sign-off surface ...")
    test_web(checks)
    print("")
    print(f"V2 M2: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    rmtree(TEMP_ROOT)
    return 0 if checks.passed == checks.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
