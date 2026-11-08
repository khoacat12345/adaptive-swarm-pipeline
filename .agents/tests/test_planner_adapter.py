#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2 planner adapter: goal/v1 plan -> validated MasterPlan.

    python .agents/tests/test_planner_adapter.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2.plan import plan_hash, validate_plan  # noqa: E402
from v2.planner_adapter import (  # noqa: E402
    PlanningError,
    adapt_file,
    adapt_source,
    default_verify_command,
    detect_targets,
    from_goal,
    from_v1_plan,
    write_plan,
)

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "planner_adapter_tests"


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
    (root / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
    (root / "check.py").write_text(
        "from pathlib import Path\n"
        "text = Path('src.py').read_text(encoding='utf-8')\n"
        "assert 'VALUE = 2' in text\n"
        "print('OK')\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    (root / "security").mkdir()
    (root / "security" / "auth.py").write_text("ALLOW = False\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_src.py").write_text(
        "def test_value():\n    assert True\n", encoding="utf-8"
    )
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return root


def v1_plan(*, two_phases: bool = False, target: str = "src.py") -> dict:
    subtasks = [
        {
            "id": "S1",
            "title": "Set VALUE",
            "objective": "Set VALUE to 2",
            "boundaries": "Only src.py",
            "input_context": "VALUE starts at 1",
            "acceptance_criteria": "check.py exits 0",
            "verify_command": "python check.py",
            "target_files": [target],
            "level": 1,
            "max_rounds": 2,
            "depends_on": [],
        }
    ]
    if two_phases:
        subtasks.append(
            {
                "id": "S2",
                "title": "Touch extra",
                "objective": "Update extra.py",
                "boundaries": "Only extra.py",
                "input_context": "EXTRA starts at 1",
                "acceptance_criteria": "extra.py exists",
                "verify_command": "python -c \"print('ok')\"",
                "target_files": ["extra.py"],
                "level": 1,
                "max_rounds": 1,
                "depends_on": ["S1"],
            }
        )
    return {"job_id": "J1", "objective": "adapter test", "subtasks": subtasks}


def test_goal_plan(checks: Checks) -> None:
    root = make_repo("goal")
    try:
        result = from_goal(
            "Set VALUE to 2 in src.py",
            run_id="run-goal",
            base_sha="HEAD",
            root=root,
            verify_command="python check.py",
        )
        checks.check("goal: proposed tier is E2", result.tier == "E2", result.tier)
        checks.check("goal: planner is P1", result.planner == "P1", result.planner)
        checks.check("goal: one phase and one work order",
                     len(result.plan["phases"]) == 1
                     and len(result.plan["phases"][0]["work_orders"]) == 1,
                     result.plan)
        checks.check("goal: target detected from text",
                     result.plan["phases"][0]["work_orders"][0]["target_files"]
                     == ["src.py"])
        checks.check("goal: plan hash matches plan",
                     result.plan_hash == plan_hash(result.plan))
        checks.check("goal: plan validates",
                     bool(validate_plan(result.plan, worktree=root)))
        checks.check("goal: source is recorded",
                     result.source == "goal" and len(result.source_hash) == 64,
                     result.as_dict())
        checks.check("goal: summary mentions proposed tier",
                     "E2" in result.summary, result.summary)
    finally:
        rmtree(root)


def test_goal_without_target(checks: Checks) -> None:
    root = make_repo("goal-no-target")
    try:
        failed = False
        try:
            from_goal("make it better", run_id="r", base_sha="HEAD", root=root)
        except PlanningError as exc:
            failed = "target" in str(exc).lower()
        checks.check("goal: unnamed target is rejected", failed)
    finally:
        rmtree(root)


def test_goal_auto_verify(checks: Checks) -> None:
    root = make_repo("goal-verify")
    try:
        result = from_goal(
            "Fix src.py",
            run_id="r",
            base_sha="HEAD",
            root=root,
        )
        verify = result.plan["phases"][0]["work_orders"][0]["verify_command"]
        checks.check("goal: python target gets py_compile verify",
                     "py_compile" in verify and "src.py" in verify, verify)
        proc = subprocess.run(
            verify, cwd=root, shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        checks.check("goal: generated verify command actually runs",
                     proc.returncode == 0, proc.stdout + proc.stderr)
    finally:
        rmtree(root)


def test_v1_plan_multi_phase(checks: Checks) -> None:
    root = make_repo("multi")
    try:
        result = from_v1_plan(
            v1_plan(two_phases=True),
            run_id="run-multi",
            base_sha="HEAD",
            root=root,
        )
        plan = result.plan
        checks.check("v1: multi-phase proposal is E3", result.tier == "E3",
                     result.tier)
        checks.check("v1: planner follows tier policy", result.planner == "P2",
                     result.planner)
        checks.check("v1: two phases mapped", len(plan["phases"]) == 2,
                     [phase["id"] for phase in plan["phases"]])
        checks.check("v1: dependency is preserved",
                     plan["phases"][1]["depends_on"] == ["S1"],
                     plan["phases"][1]["depends_on"])
        checks.check("v1: every work order has verify",
                     all(work["verify_command"]
                         for phase in plan["phases"]
                         for work in phase["work_orders"]))
        checks.check("v1: plan validates",
                     bool(validate_plan(plan, worktree=root)))
    finally:
        rmtree(root)


def test_v1_plan_errors(checks: Checks) -> None:
    root = make_repo("errors")
    try:
        missing = v1_plan()
        missing["subtasks"][0]["target_files"] = ["missing.py"]
        failed_missing = False
        try:
            from_v1_plan(missing, run_id="r", base_sha="HEAD", root=root)
        except PlanningError as exc:
            failed_missing = "does not exist" in str(exc)
        checks.check("v1: missing target is rejected", failed_missing)

        test_target = v1_plan()
        test_target["subtasks"][0]["target_files"] = ["tests/test_src.py"]
        failed_test = False
        try:
            from_v1_plan(test_target, run_id="r", base_sha="HEAD", root=root)
        except PlanningError as exc:
            failed_test = "test file" in str(exc)
        checks.check("v1: test target is rejected", failed_test)

        empty = {"job_id": "J1", "subtasks": []}
        failed_empty = False
        try:
            from_v1_plan(empty, run_id="r", base_sha="HEAD", root=root)
        except PlanningError:
            failed_empty = True
        checks.check("v1: empty plan is rejected", failed_empty)
    finally:
        rmtree(root)


def test_e1_policy(checks: Checks) -> None:
    root = make_repo("e1")
    try:
        failed = False
        try:
            from_v1_plan(
                v1_plan(), run_id="r", base_sha="HEAD", root=root, tier="E1",
            )
        except PlanningError as exc:
            failed = "opt-in" in str(exc).lower()
        checks.check("E1: adapter requires explicit low-risk opt-in", failed)

        result = from_v1_plan(
            v1_plan(), run_id="r", base_sha="HEAD", root=root,
            tier="E1", low_risk_opt_in=True,
        )
        checks.check("E1: opt-in plan is accepted",
                     result.tier == "E1"
                     and result.plan.get("low_risk_opt_in") is True,
                     result.plan.get("low_risk_opt_in"))
        checks.check("E1: low-risk plan validates",
                     bool(validate_plan(result.plan, worktree=root)))

        sensitive = v1_plan(target="security/auth.py")
        blocked = False
        try:
            from_v1_plan(
                sensitive, run_id="r", base_sha="HEAD", root=root,
                tier="E1", low_risk_opt_in=True,
            )
        except PlanningError as exc:
            blocked = "sensitive" in str(exc).lower()
        checks.check("E1: sensitive target is rejected by the adapter", blocked)
    finally:
        rmtree(root)


def test_automatic_sensitive_tier(checks: Checks) -> None:
    root = make_repo("sensitive-tier")
    try:
        result = from_v1_plan(
            v1_plan(target="security/auth.py"),
            run_id="r",
            base_sha="HEAD",
            root=root,
        )
        checks.check("tier: sensitive single-file plan is upgraded to E3",
                     result.tier == "E3", result.tier)
    finally:
        rmtree(root)


def test_allow_new_files(checks: Checks) -> None:
    root = make_repo("new-files")
    try:
        source = v1_plan()
        source["subtasks"][0]["target_files"] = ["new_module.py"]
        result = from_v1_plan(
            source,
            run_id="r",
            base_sha="HEAD",
            root=root,
            allow_new_files=True,
        )
        phase = result.plan["phases"][0]
        checks.check("new files: phase opts in",
                     phase.get("allow_new_files") is True)
        checks.check("new files: plan validates",
                     bool(validate_plan(result.plan, worktree=root)))
    finally:
        rmtree(root)


def test_v2_passthrough(checks: Checks) -> None:
    root = make_repo("passthrough")
    try:
        first = from_goal(
            "Set VALUE to 2 in src.py",
            run_id="r",
            base_sha="HEAD",
            root=root,
            verify_command="python check.py",
        )
        second = adapt_source(
            first.plan, run_id="r", base_sha="HEAD", root=root,
        )
        checks.check("v2 plan: source is recognized",
                     second.source == "v2_plan", second.source)
        checks.check("v2 plan: hash is stable",
                     second.plan_hash == first.plan_hash,
                     (second.plan_hash, first.plan_hash))
        checks.check("v2 plan: tier is preserved",
                     second.tier == first.tier, second.tier)
    finally:
        rmtree(root)


def test_file_and_cli(checks: Checks) -> None:
    root = make_repo("cli")
    try:
        v1_path = root / "v1_plan.json"
        v1_path.write_text(json.dumps(v1_plan()), encoding="utf-8")
        result = adapt_file(
            v1_path, run_id="r", base_sha="HEAD", root=root,
        )
        out = write_plan(root / "v2_plan.json", result)
        loaded = json.loads(out.read_text(encoding="utf-8"))
        checks.check("file: adapted plan is written",
                     loaded["execution_tier"] == result.tier
                     and len(loaded["phases"]) == 1)
        checks.check("file: written plan validates",
                     bool(validate_plan(loaded, worktree=root)))

        cli_out = root / "cli_plan.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(AGENTS_DIR / "v2" / "planner_adapter.py"),
                "--goal", "Set VALUE to 2 in src.py",
                "--root", str(root),
                "--verify", "python check.py",
                "--base-sha", "HEAD",
                "--out", str(cli_out),
                "--json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        checks.check("cli: planner adapter exits 0", proc.returncode == 0,
                     proc.stdout + proc.stderr)
        checks.check("cli: plan file is valid JSON",
                     cli_out.is_file()
                     and json.loads(cli_out.read_text(encoding="utf-8"))[
                         "schema_version"
                     ] == "2.1",
                     proc.stdout + proc.stderr)
    finally:
        rmtree(root)


def test_helpers(checks: Checks) -> None:
    root = make_repo("helpers")
    try:
        checks.check("helpers: detect_targets finds existing file",
                     detect_targets("please update src.py today", root)
                     == ["src.py"])
        checks.check("helpers: detect_targets ignores missing file",
                     detect_targets("please update nope.py", root) == [])
        verify = default_verify_command(["README.md"])
        checks.check("helpers: markdown verify is runnable",
                     "README.md" in verify, verify)
        proc = subprocess.run(
            verify, cwd=root, shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        checks.check("helpers: markdown verify exits 0", proc.returncode == 0,
                     proc.stdout + proc.stderr)
    finally:
        rmtree(root)


def main() -> int:
    rmtree(TEMP_ROOT)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    checks = Checks()
    print("[1/11] goal -> plan ...")
    test_goal_plan(checks)
    print("[2/11] goal without target ...")
    test_goal_without_target(checks)
    print("[3/11] goal auto verify ...")
    test_goal_auto_verify(checks)
    print("[4/11] v1 multi-phase ...")
    test_v1_plan_multi_phase(checks)
    print("[5/11] v1 errors ...")
    test_v1_plan_errors(checks)
    print("[6/11] E1 policy ...")
    test_e1_policy(checks)
    print("[7/11] sensitive tier ...")
    test_automatic_sensitive_tier(checks)
    print("[8/11] new files ...")
    test_allow_new_files(checks)
    print("[9/11] v2 passthrough ...")
    test_v2_passthrough(checks)
    print("[10/11] file + CLI ...")
    test_file_and_cli(checks)
    print("[11/11] helpers ...")
    test_helpers(checks)
    print("")
    print(f"PLANNER ADAPTER: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    rmtree(TEMP_ROOT)
    return 0 if checks.passed == checks.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
