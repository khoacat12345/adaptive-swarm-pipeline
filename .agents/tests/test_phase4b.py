#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 4B: swarm resume and rollback acceptance checks.

The heavy process-kill path remains in test_phase_process_crash.py. This file
covers the operator-facing ``swarm resume`` / ``swarm rollback`` commands over
the same state machine and a real temporary Git repository.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from v2 import isolation  # noqa: E402
from v2.execution import ExecutionEngine  # noqa: E402
from v2.runner import V2Runner  # noqa: E402
from v2.swarm_cli import main as swarm_main  # noqa: E402
from test_v2_m3 import (  # noqa: E402
    StaticCoder,
    cleanup,
    make_plan,
    make_repo,
    seats,
    setup_run,
)


def run_swarm(args: list[str]) -> tuple[int, dict, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = swarm_main(args)
    text = buffer.getvalue()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {}
    return code, payload, text


def test_resume() -> int:
    checks = 0
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "phase4b-resume", StaticCoder([{"src.py": "VALUE = 2\n"}])
    )
    try:
        store = runner.load(run_id)
        store.update(status="PHASE_EXECUTING")
        code, payload, text = run_swarm([
            "resume", "--run-id", run_id,
            "--root", str(root),
            "--state-root", str(root / "state"),
            "--worktree-root", str(wt_root),
            "--json",
        ])
        checks += 1; assert code == 0, text[-1200:]
        checks += 1; assert payload.get("action") == "recover", payload
        checks += 1; assert payload.get("status") == "PROVISIONING", payload
        checks += 1; assert runner.load(run_id).load()["status"] == "PROVISIONING"
        checks += 1; assert (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n"

        result = ExecutionEngine(runner).execute(
            run_id, coder=coder_seat, reviewers=reviewers,
        )
        checks += 1; assert result.status == "HANDOFF_READY", result.as_dict()
        checks += 1; assert result.checkpoints
        checks += 1; assert (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        return checks
    finally:
        cleanup(runner, root, wt_root)


def test_rollback() -> int:
    checks = 0
    root = make_repo("phase4b-rollback")
    runner = V2Runner(root, state_root=root / "state")
    run_id = None
    try:
        prepared = runner.prepare("phase4b rollback", phase_ids=["P1"])
        run_id = prepared["run_id"]
        plan = make_plan(run_id, prepared["workspace"]["base_sha"])
        run_dir = runner.load(run_id).run_dir
        (run_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        handoff = run_dir / "handoff"
        handoff.mkdir()
        patch = (
            "diff --git a/src.py b/src.py\n"
            "--- a/src.py\n"
            "+++ b/src.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 1\n"
            "+VALUE = 2\n"
        )
        (handoff / "handoff.patch").write_text(patch, encoding="utf-8")
        (handoff / "report.md").write_text("# report\n", encoding="utf-8")
        runner.load(run_id).update(status="HANDOFF_READY")
        from v2.delivery import merge
        merged = merge(root, run_id, state_root=root / "state", verify_timeout=60)
        checks += 1; assert merged["ok"] is True, merged
        checks += 1; assert "VALUE = 2" in (root / "src.py").read_text(encoding="utf-8")

        code, payload, text = run_swarm([
            "rollback", "--run-id", run_id,
            "--root", str(root),
            "--state-root", str(root / "state"),
            "--json",
        ])
        checks += 1; assert code == 0, text[-1200:]
        checks += 1; assert payload.get("ok") is True, payload
        checks += 1; assert "VALUE = 1" in (root / "src.py").read_text(encoding="utf-8")
        checks += 1; assert (run_dir / "rollback.json").exists()
        return checks
    finally:
        if run_id:
            try:
                isolation.remove_worktree(runner.workspace(run_id), keep_branch=False)
            except Exception:
                pass
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    total = test_resume() + test_rollback()
    print(f"PHASE 4B: {total}/{total} PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
