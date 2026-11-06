#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-05/P1-06/P1-07: Spec Kit bridge, converge and runtime stub."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from v2.converge import converge, evidence_from_ledger  # noqa: E402
from v2.runtime import (  # noqa: E402
    ClaudeCodeRuntimeStub,
    RuntimeRegistry,
)
from v2.tasks import build_master_plan  # noqa: E402
from test_v2_m3 import Checks, git, make_repo  # noqa: E402

ROOT = Path(tempfile.gettempdir()) / "opencode" / "speckit_converge_tests"


def make_tasks(run_id: str, base_sha: str) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "goal": "converge test",
        "base_sha": base_sha,
        "source": {
            "spec": "specs/demo/spec.md",
            "plan": "specs/demo/plan.md",
            "tasks": "specs/demo/tasks.md",
        },
        "budget": {"requests": 20, "cost_usd": 1.0, "wall_seconds": 600},
        "phases": [{
            "id": "P1", "title": "change src",
            "allowed_paths": ["src.py"],
            "forbidden_paths": [".git"],
            "budget": {"requests": 10, "cost_usd": 0.5, "wall_seconds": 300},
            "work_orders": [{
                "id": "S1", "objective": "update src",
                "acceptance_criteria": "verify exits 0",
                "verify_command": 'python -c "print(1)"',
                "target_files": ["src.py"], "level": 1, "max_rounds": 1,
            }],
        }],
    }


def main() -> int:
    shutil.rmtree(ROOT, ignore_errors=True)
    checks = Checks()
    root = make_repo("converge")
    try:
        (root / "specs" / "demo").mkdir(parents=True)
        for name in ("spec.md", "plan.md", "tasks.md"):
            (root / "specs" / "demo" / name).write_text(
                f"# {name}\n", encoding="utf-8")
        (root / "other.py").write_text("x = 1\n", encoding="utf-8")
        git(root, "add", ".")
        git(root, "commit", "-m", "spec fixture")
        base = git(root, "rev-parse", "HEAD").stdout.strip()
        plan, _ = build_master_plan(
            make_tasks("r1", base), root=root, run_id="r1", base_sha=base,
            worktree=root,
        )
        (root / "src.py").write_text("VALUE = 2\n", encoding="utf-8")
        good = converge(root, plan, worktree=root, test_evidence=[
            {"command": "verify", "exit_code": 0},
        ])
        checks.check("converge: clean change ok", good["ok"] is True, good)
        checks.check("converge: changed file recorded",
                     "src.py" in good["changed_files"], good)
        checks.check("converge: verify evidence recorded",
                     good["verify_evidence"][0]["status"] == "pass", good)

        bad_verify = converge(root, plan, worktree=root, test_evidence=[
            {"command": "verify", "exit_code": 1},
        ])
        checks.check("converge: failed verify blocks", bad_verify["ok"] is False,
                     bad_verify)

        (root / "specs" / "demo" / "spec.md").write_text(
            "# changed spec\n", encoding="utf-8")
        bad_spec = converge(root, plan, worktree=root, test_evidence=[])
        checks.check("converge: spec drift detected", bad_spec["ok"] is False,
                     bad_spec)

        (root / "other.py").write_text("x = 2\n", encoding="utf-8")
        bad_scope = converge(root, plan, worktree=root, test_evidence=[])
        checks.check("converge: path drift detected", bad_scope["ok"] is False,
                     bad_scope)

        evidence = evidence_from_ledger([
            {"event_type": "safe_writer.verify", "exit_code": 0},
            {"event_type": "noise"},
        ])
        checks.check("converge: ledger evidence filter", len(evidence) == 1, evidence)

        registry = RuntimeRegistry()
        stub = ClaudeCodeRuntimeStub()
        registry.register(stub)
        checks.check("runtime: second stub registered",
                     registry.get("claude-code-stub") is stub, registry.names())
        checks.check("runtime: stub is not live", stub.health()["ok"] is False)
        checks.check("runtime: stub execute fails closed",
                     _raises(lambda: stub.execute({}, worktree=None, budget=None)))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print(f"SPECKIT CONVERGE: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


if __name__ == "__main__":
    raise SystemExit(main())
