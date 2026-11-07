#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2-03: explicit v2 merge and rollback delivery workflow."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from v2 import isolation  # noqa: E402
from v2.delivery import (  # noqa: E402
    DeliveryError,
    merge,
    rollback,
    validate_handoff_patch,
)
from v2.runner import V2Runner  # noqa: E402
from test_v2_m3 import make_plan, make_repo  # noqa: E402


def patch_for(value: int, old: int = 1) -> str:
    return (
        "diff --git a/src.py b/src.py\n"
        "--- a/src.py\n"
        "+++ b/src.py\n"
        "@@ -1 +1 @@\n"
        f"-VALUE = {old}\n"
        f"+VALUE = {value}\n"
    )


def main() -> int:
    checks = 0
    root = make_repo("p2-delivery")
    runner = V2Runner(root)
    run_id = None
    try:
        prepared = runner.prepare("delivery test", phase_ids=["P1"])
        run_id = prepared["run_id"]
        plan = make_plan(run_id, prepared["workspace"]["base_sha"])
        run_dir = runner.load(run_id).run_dir
        (run_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        (run_dir / "handoff").mkdir()
        patch = patch_for(2)
        (run_dir / "handoff" / "handoff.patch").write_text(patch, encoding="utf-8")
        runner.load(run_id).update(status="HANDOFF_READY")

        checks += 1; assert validate_handoff_patch(patch, plan) == ["src.py"]
        bad = patch.replace("a/src.py", "a/../outside.py")
        try:
            validate_handoff_patch(bad, plan)
            raise AssertionError("unsafe patch path was accepted")
        except DeliveryError:
            checks += 1

        checks += 1; assert "VALUE = 1" in (root / "src.py").read_text(encoding="utf-8")
        result = merge(root, run_id, verify_timeout=60)
        checks += 1; assert result["ok"] is True
        checks += 1; assert result["merge_sha"]
        checks += 1; assert "VALUE = 2" in (root / "src.py").read_text(encoding="utf-8")
        checks += 1; assert (run_dir / "merge.json").exists()
        checks += 1; assert "v2 merge:" in (
            __import__("subprocess").run(
                ["git", "-C", str(root), "log", "-1", "--pretty=%s"],
                capture_output=True, text=True, check=True,
            ).stdout
        )

        # The merge verify gate must reject a bad patch and restore the tree.
        (run_dir / "handoff" / "handoff.patch").write_text(patch_for(3, old=2), encoding="utf-8")
        try:
            merge(root, run_id, verify_timeout=60)
            raise AssertionError("failing verify gate was accepted")
        except DeliveryError:
            checks += 1
        checks += 1; assert "VALUE = 2" in (root / "src.py").read_text(encoding="utf-8")

        rolled = rollback(root, run_id)
        checks += 1; assert rolled["ok"] is True and rolled["rollback_sha"]
        checks += 1; assert "VALUE = 1" in (root / "src.py").read_text(encoding="utf-8")
        checks += 1; assert (run_dir / "rollback.json").exists()

        print(f"V2 DELIVERY: {checks}/{checks} PASS")
        return 0
    finally:
        if run_id:
            try:
                isolation.remove_worktree(runner.workspace(run_id), keep_branch=False)
            except Exception:
                pass
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
