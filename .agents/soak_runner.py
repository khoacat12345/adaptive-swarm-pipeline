#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Long-running soak harness (P1-01 / P1-02).

Default mode is deterministic/fake so an overnight loop can exercise the state
machine without burning model quota. ``--mode live`` uses free OpenCode seats;
it still obeys the same Usage gate and budget caps.

The runner writes heartbeats to docs/SOAK_LOG.md and can keep Windows awake.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

AGENTS = Path(__file__).resolve().parent
if str(AGENTS) not in sys.path:
    sys.path.insert(0, str(AGENTS))

from v2 import isolation  # noqa: E402
from v2.adapters import build_worker_seats  # noqa: E402
from v2.execution import ExecutionEngine, ExecutionSeat  # noqa: E402
from v2.plan import build_example  # noqa: E402
from v2.runner import V2Runner  # noqa: E402

LOG = AGENTS.parent / "docs" / "SOAK_LOG.md"


class FakeCoder:
    def propose(self, work_order, **kwargs):
        return {"src.py": "VALUE = 2\n"}

    def propose_set(self, work_order, **kwargs):
        return {"src.py": "VALUE = 2\n"}


class FakeReviewer:
    def __init__(self, role: str):
        self.role = role

    def review(self, role, *, diff, work_order, phase):
        return {"verdict": "approve", "severity": "low", "evidence": [],
                "required_change": ""}


def _keep_awake(enabled: bool) -> None:
    if not enabled or os.name != "nt":
        return
    try:
        # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    except Exception:
        pass


def _log(line: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line.rstrip() + "\n")


def _make_repo(base: Path) -> Path:
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=base, check=True)
    subprocess.run(["git", "config", "user.email", "soak@example.com"], cwd=base, check=True)
    subprocess.run(["git", "config", "user.name", "Soak"], cwd=base, check=True)
    (base / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
    (base / "check.py").write_text(
        "from pathlib import Path\n"
        "assert 'VALUE = 2' in Path('src.py').read_text(encoding='utf-8')\n"
        "print('OK')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=base, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=base, check=True)
    return base


def _plan(run_id: str, base_sha: str) -> dict:
    plan = build_example(run_id, base_sha, phase_ids=["P1"])
    phase = plan["phases"][0]
    phase["allowed_paths"] = ["src.py", "check.py"]
    phase["budget"] = {"requests": 12, "cost_usd": 0.2, "wall_seconds": 600}
    phase["work_orders"][0].update({
        "objective": "Change src.py VALUE from 1 to 2",
        "boundaries": "Only src.py",
        "acceptance_criteria": "check.py exits 0",
        "verify_command": "python check.py",
        "target_files": ["src.py"],
        "level": 1,
        "max_rounds": 1,
    })
    return plan


def _run_cycle(base: Path, mode: str, config: dict | None) -> dict:
    root = _make_repo(base / "repo")
    wt = base / "wt"
    runner = V2Runner(root, state_root=root / "state", worktree_root=wt)
    run_id = None
    try:
        prepared = runner.prepare("soak cycle", phase_ids=["P1"])
        run_id = prepared["run_id"]
        plan = _plan(run_id, prepared["workspace"]["base_sha"])
        runner.submit_plan(run_id, plan)
        runner.signoff(run_id, approved_by="soak")
        if mode == "live":
            coder, reviewers = build_worker_seats(
                coder_model="opencode/deepseek-v4.1-flash-free",
                reviewer_models={role: "opencode/glm-5.3-flash-free"
                                 for role in ("security", "qa", "perf", "docs")},
                sandbox_root=runner.load(run_id).run_dir / "coder_sandbox",
                coder_kind="agentic", timeout=180, config=config,
                paid_cost_usd=0.0, free_only=True,
            )
        else:
            coder = ExecutionSeat("coder", "fake/coder", "fake", FakeCoder())
            reviewers = {
                role: ExecutionSeat(role, f"fake/{role}", f"fake-{role}",
                                    FakeReviewer(role))
                for role in ("security", "qa", "perf", "docs")
            }
        result = ExecutionEngine(runner).execute(run_id, coder=coder, reviewers=reviewers)
        return {"ok": result.status == "HANDOFF_READY",
                "status": result.status, "run_id": run_id}
    finally:
        try:
            if run_id:
                isolation.remove_worktree(runner.workspace(run_id), keep_branch=False)
        except Exception:
            pass
        shutil.rmtree(base, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Adaptive Swarm soak runner")
    parser.add_argument("--hours", type=float, default=8.0)
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--max-cycles", type=int, default=0)
    parser.add_argument("--keep-awake", action="store_true")
    parser.add_argument("--work-root", default=None)
    args = parser.parse_args(argv)
    work_root = Path(args.work_root or (Path(tempfile.gettempdir()) / "opencode" / "soak"))
    work_root.mkdir(parents=True, exist_ok=True)
    config = None
    if args.mode == "live":
        try:
            from v2.models import load_config
            config = load_config(AGENTS.parent)
        except Exception:
            config = None
    _keep_awake(args.keep_awake)
    deadline = time.time() + max(0.1, args.hours) * 3600
    cycle = 0
    _log(f"\n## Soak started {dt.datetime.now().isoformat(timespec='seconds')}")
    _log(f"- mode: `{args.mode}`  interval: {args.interval}s  hours: {args.hours}")
    try:
        while time.time() < deadline and (not args.max_cycles or cycle < args.max_cycles):
            cycle += 1
            started = time.time()
            try:
                result = _run_cycle(work_root / f"cycle_{cycle}", args.mode, config)
                status = result.get("status")
                ok = result.get("ok")
            except Exception as exc:  # noqa: BLE001
                status = f"{type(exc).__name__}: {exc}"[:300]
                ok = False
                traceback.print_exc()
            elapsed = round(time.time() - started, 1)
            line = (f"- {dt.datetime.now().isoformat(timespec='seconds')} "
                    f"cycle={cycle} mode={args.mode} ok={ok} "
                    f"status={status} elapsed={elapsed}s")
            _log(line)
            print(line, flush=True)
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(max(1.0, args.interval), remaining))
    finally:
        _log(f"- Soak stopped {dt.datetime.now().isoformat(timespec='seconds')} "
             f"after {cycle} cycle(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
