#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0-05: kill a real v2 phase process, recover, then finish and handoff."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from v2.execution import ExecutionEngine  # noqa: E402
from v2.swarm_cli import main as swarm_main  # noqa: E402
from test_v2_m3 import (  # noqa: E402
    Checks,
    StaticCoder,
    cleanup,
    seats,
    setup_run,
)

ROOT = Path(tempfile.gettempdir()) / "opencode" / "phase_crash_tests"

CHILD = r'''
import sys, time
from pathlib import Path
sys.path.insert(0, r"{agents}")
from v2.execution import ExecutionEngine, ExecutionSeat
from v2.runner import V2Runner

root = Path(r"{root}")
run_id = r"{run_id}"
wt = Path(r"{wt}")
marker = Path(r"{marker}")

class SlowCoder:
    def propose(self, work_order, **kwargs):
        marker.write_text("started", encoding="utf-8")
        time.sleep(120)
        return {"src.py": "VALUE = 2\n"}

class Reviewer:
    def __init__(self, role):
        self.role = role
    def review(self, role, *, diff, work_order, phase):
        return {"verdict": "approve", "severity": "low", "evidence": []}

runner = V2Runner(root, state_root=root / "state", worktree_root=wt)
coder = ExecutionSeat("coder", "mock/deepseek", "deepseek", SlowCoder())
reviewers = {
    role: ExecutionSeat(role, f"mock/{role}", f"family-{role}", Reviewer(role))
    for role in ("security", "qa", "perf", "docs")
}
ExecutionEngine(runner).execute(run_id, coder=coder, reviewers=reviewers)
'''


def main() -> int:
    shutil.rmtree(ROOT, ignore_errors=True)
    ROOT.mkdir(parents=True)
    checks = Checks()
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, _, plan = setup_run("phase-crash", coder)
    marker = ROOT / "child.started"
    child_file = ROOT / "child.py"
    child_file.write_text(
        CHILD.replace("{agents}", str(AGENTS_DIR))
        .replace("{root}", str(root))
        .replace("{run_id}", run_id)
        .replace("{wt}", str(wt_root))
        .replace("{marker}", str(marker)),
        encoding="utf-8",
    )
    child = subprocess.Popen(
        [sys.executable, str(child_file)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 30
        while not marker.exists() and time.time() < deadline:
            if child.poll() is not None:
                break
            time.sleep(0.2)
        checks.check("crash: child reached phase", marker.exists(),
                     child.poll())
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(child.pid)],
                           capture_output=True, text=True)
        else:
            child.kill()
        child.wait(timeout=30)
        checks.check("crash: process was killed", child.returncode != 0,
                     child.returncode)

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            resume_code = swarm_main([
                "resume", "--run-id", run_id,
                "--root", str(root),
                "--state-root", str(root / "state"),
                "--worktree-root", str(wt_root),
                "--json",
            ])
        resume_text = buffer.getvalue()
        try:
            recovered = json.loads(resume_text)
        except json.JSONDecodeError:
            recovered = {}
        checks.check("crash: swarm resume exits 0", resume_code == 0,
                     resume_text[-800:])
        checks.check("crash: swarm resume recovers to PROVISIONING",
                     recovered.get("status") == "PROVISIONING", recovered)
        checks.check("crash: checkpoint state readable",
                     runner.load(run_id).load().get("status") == "PROVISIONING")

        resumed_coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
        coder_seat = coder_seat.__class__(
            "coder", "mock/deepseek", "deepseek", resumed_coder)
        _, reviewers = seats()
        result = ExecutionEngine(runner).execute(
            run_id, coder=coder_seat, reviewers=reviewers,
        )
        checks.check("crash: resumed run reaches handoff",
                     result.status == "HANDOFF_READY", result.as_dict())
        checks.check("crash: one checkpoint", len(result.checkpoints) == 1,
                     result.checkpoints)
        checks.check("crash: main worktree unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
        worktree = runner.workspace(run_id).path
        checks.check("crash: worktree has verified value",
                     (worktree / "src.py").read_text(encoding="utf-8") == "VALUE = 2\n")
        allowed = {".agents", ".git", "check.py", "src.py", "tests"}
        extras = [path.name for path in worktree.iterdir()
                  if path.name not in allowed]
        checks.check("crash: no extra worktree files", not extras, extras)
        checks.check("crash: handoff produced",
                     bool(result.handoff and result.handoff.get("patch")),
                     result.handoff)
    finally:
        try:
            if child.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/PID", str(child.pid)],
                                   capture_output=True)
                else:
                    child.kill()
        except Exception:
            pass
        cleanup(runner, root, wt_root)
        shutil.rmtree(ROOT, ignore_errors=True)

    print(f"PHASE CRASH: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
