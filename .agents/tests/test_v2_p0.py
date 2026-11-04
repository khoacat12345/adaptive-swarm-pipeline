#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 acceptance - constitution binding, tasks.yaml bridge and runtime contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import yaml  # noqa: E402

from v2.constitution import constitution_hash, load_constitution  # noqa: E402
from v2.errors import PlanChangedError  # noqa: E402
from v2.plan import validate_plan  # noqa: E402
from v2.runner import V2Runner  # noqa: E402
from v2.runtime import RuntimeCapabilities, RuntimeRegistry  # noqa: E402
from v2.signoff import assert_signoff, load_signoff  # noqa: E402
from v2.tasks import (  # noqa: E402
    TasksSchemaError,
    bridge_spec_kit,
    build_master_plan,
    load_tasks,
)
from test_v2_m3 import Checks, cleanup, git, make_repo  # noqa: E402


def valid_tasks(run_id: str, base_sha: str) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "goal": "Spec Kit bridge test",
        "base_sha": base_sha,
        "source": {
            "spec": "specs/demo/spec.md",
            "plan": "specs/demo/plan.md",
            "tasks": "specs/demo/tasks.md",
        },
        "budget": {"requests": 20, "cost_usd": 2.0, "wall_seconds": 1200},
        "environment": {"env_allow": ["PATH", "PYTHONPATH"], "network_for_verify": False},
        "phases": [
            {
                "id": "P1",
                "title": "Readme",
                "depends_on": [],
                "allowed_paths": ["src.py"],
                "forbidden_paths": [".git", ".env"],
                "budget": {"requests": 10, "cost_usd": 0.5, "wall_seconds": 600},
                "work_orders": [
                    {
                        "id": "T001",
                        "objective": "Update src.py",
                        "boundaries": "Only src.py",
                        "input_context": "Base src.py",
                        "acceptance_criteria": "Command exits 0",
                        "verify_command": 'python -c "print(\'ok\')"',
                        "target_files": ["src.py"],
                        "level": 1,
                        "max_rounds": 1,
                    }
                ],
            }
        ],
    }


class FakeRuntime:
    name = "fake"

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(headless=True, tools=False)

    def health(self) -> dict:
        return {"ok": True}

    def execute(self, work_order, *, worktree, budget, feedback=None):
        raise NotImplementedError


def test_constitution(checks: Checks) -> None:
    root = make_repo("p0-constitution")
    try:
        (root / "docs").mkdir()
        (root / "docs" / "CONSTITUTION.md").write_text(
            "# Constitution\n\nMachine verify is mandatory.\n", encoding="utf-8"
        )
        info = load_constitution(root)
        checks.check("p0: constitution is found", info["present"] and info["complete"])
        checks.check("p0: constitution hash is stable",
                     info["hash"] == constitution_hash(root))
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)


def test_tasks_bridge(checks: Checks) -> None:
    root = make_repo("p0-bridge")
    wt_root = root.parent / "p0-bridge-wt"
    try:
        (root / "docs").mkdir()
        (root / "docs" / "CONSTITUTION.md").write_text(
            "# Constitution\n\nOnly verify can declare success.\n", encoding="utf-8"
        )
        (root / "specs" / "demo").mkdir(parents=True)
        for name in ("spec.md", "plan.md", "tasks.md"):
            (root / "specs" / "demo" / name).write_text(f"# {name}\n", encoding="utf-8")
        git(root, "add", ".")
        git(root, "commit", "-m", "p0 fixture")
        runner = V2Runner(root, state_root=root / "state", worktree_root=wt_root)
        prepared = runner.prepare("bridge", phase_ids=["P1"])
        run_id = prepared["run_id"]
        base = prepared["workspace"]["base_sha"]
        tasks = valid_tasks(run_id, base)
        tasks_path = root / "specs" / "demo" / "tasks.yaml"
        tasks_path.write_text(yaml.safe_dump(tasks, sort_keys=False), encoding="utf-8")

        bridged = bridge_spec_kit(
            root, tasks_path, run_id=run_id, base_sha=base,
            worktree=prepared["workspace"]["worktree_path"],
        )
        checks.check("p0: bridge produces a valid plan",
                     bridged["validated"]["plan_hash"])
        checks.check("p0: bridge binds source artifacts",
                     len(bridged["plan"]["source_artifacts"]) == 3)
        loaded = load_tasks(tasks_path)
        checks.check("p0: tasks.yaml loads", loaded["schema_version"] == "1.0")
        submitted = runner.submit_plan(run_id, bridged["plan"])
        checks.check("p0: bridge plan enters sign-off",
                     submitted["status"] == "AWAITING_SIGNOFF")
        signed = runner.signoff(run_id)
        record = load_signoff(runner.load(run_id))
        checks.check("p0: signoff records constitution hash",
                     record.get("constitution_hash") == constitution_hash(root))
        checks.check("p0: constitution path is recorded", bool(record.get("constitution_path")))
        checks.check("p0: signed plan still verifies",
                     assert_signoff(runner.load(run_id), bridged["plan"])["plan_hash"]
                     == record["plan_hash"])

        # A constitution edit invalidates the signature.
        (root / "docs" / "CONSTITUTION.md").write_text(
            "# Constitution\n\nChanged.\n", encoding="utf-8"
        )
        invalidated = False
        try:
            assert_signoff(runner.load(run_id), bridged["plan"])
        except PlanChangedError:
            invalidated = True
        checks.check("p0: constitution change invalidates signoff", invalidated)

        # Prose tasks.md is rejected.
        refused = False
        try:
            load_tasks(root / "specs" / "demo" / "tasks.md")
        except TasksSchemaError:
            refused = True
        checks.check("p0: prose tasks.md is refused", refused)

        # Missing verify_command is rejected before plan creation.
        bad = valid_tasks(run_id, base)
        del bad["phases"][0]["work_orders"][0]["verify_command"]
        failed = False
        try:
            build_master_plan(
                bad, root=root, run_id=run_id, base_sha=base,
                worktree=prepared["workspace"]["worktree_path"],
            )
        except TasksSchemaError:
            failed = True
        checks.check("p0: tasks without verify are rejected", failed)
    finally:
        cleanup(runner, root, wt_root)


def test_runtime_contract(checks: Checks) -> None:
    registry = RuntimeRegistry()
    fake = FakeRuntime()
    registry.register(fake)
    checks.check("p0: runtime registry resolves",
                 registry.get("fake") is fake)
    checks.check("p0: runtime capabilities expose headless",
                 registry.capabilities()["fake"]["headless"] is True)
    duplicate = False
    try:
        registry.register(fake)
    except Exception:
        duplicate = True
    checks.check("p0: duplicate runtime is rejected", duplicate)


def main() -> int:
    checks = Checks()
    print("[1/3] constitution ...")
    test_constitution(checks)
    print("[2/3] tasks.yaml bridge ...")
    test_tasks_bridge(checks)
    print("[3/3] AgentRuntime contract ...")
    test_runtime_contract(checks)
    print("")
    print(f"V2 P0: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if checks.passed == checks.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
