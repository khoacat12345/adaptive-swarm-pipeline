#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M3 acceptance - apply, review, veto, repair and test integrity.

    python .agents/tests/test_v2_m3.py

All code paths use temporary Git worktrees and fake coder/reviewer backends.
No model is called; the real project is never written.
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
from v2.errors import (  # noqa: E402
    BudgetExceeded,
    CrossFamilyError,
    ExecutionError,
    NoProgressError,
    SecurityVeto,
    TestIntegrityError,
)
from v2.execution import ExecutionEngine, ExecutionSeat  # noqa: E402
from v2.plan import plan_hash, validate_plan  # noqa: E402
from v2.runner import V2Runner  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "v2_m3_tests"


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
    (root / "check.py").write_text(
        "from pathlib import Path\n"
        "text = Path('src.py').read_text(encoding='utf-8')\n"
        "assert 'VALUE = 2' in text\n"
        "print('OK')\n",
        encoding="utf-8",
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_src.py").write_text(
        "def test_value():\n    assert True\n", encoding="utf-8"
    )
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return root


def make_plan(run_id: str, base_sha: str, *, target: str = "src.py",
              max_requests: int = 20) -> dict:
    return {
        "schema_version": "2.1",
        "run_id": run_id,
        "goal": "M3 execution test",
        "base_sha": base_sha,
        "budget": {
            "requests": 40,
            "cost_usd": 3.5,
            "wall_seconds": 1200,
            "emergency_reserve_pct": 0,
        },
        "environment": {
            "env_allow": ["PATH", "PYTHONPATH"],
            "network_for_verify": False,
            "seed_files": [],
        },
        "phases": [
            {
                "id": "P1",
                "title": "Execution",
                "depends_on": [],
                "allowed_paths": ["src.py", "check.py", "tests/test_src.py"],
                "forbidden_paths": [".git", ".env", ".agents/tmp"],
                "budget": {
                    "requests": max_requests,
                    "cost_usd": 0.70,
                    "wall_seconds": 600,
                },
                "work_orders": [
                    {
                        "id": "S1",
                        "objective": "Set VALUE to 2",
                        "boundaries": "Only the target file",
                        "input_context": "VALUE starts at 1",
                        "acceptance_criteria": "check.py exits 0",
                        "verify_command": "python check.py",
                        "target_files": [target],
                        "level": 1,
                        "max_rounds": 2,
                    }
                ],
                "rollback": {"mode": "last_checkpoint"},
                "cleanup": "disabled",
            }
        ],
    }


class StaticCoder:
    def __init__(self, contents: list[dict[str, str]]):
        self.contents = contents
        self.calls = 0

    def propose(self, work_order, *, cwd, round_index, feedback):
        self.calls += 1
        index = min(round_index - 1, len(self.contents) - 1)
        return dict(self.contents[index])


class StaticReviewer:
    def __init__(self, payload: dict | None = None):
        self.payload = payload or {
            "verdict": "approve",
            "severity": "low",
            "evidence": ["fake review"],
        }
        self.calls = 0

    def review(self, role, *, diff, work_order, phase):
        self.calls += 1
        return dict(self.payload)


def seats(reviewer_payloads: dict[str, dict] | None = None):
    coder = ExecutionSeat("coder", "mock/deepseek-coder", "deepseek", StaticCoder([]))
    payloads = reviewer_payloads or {}
    reviewers = {
        "security": ExecutionSeat(
            "security", "mock/glm-security", "glm",
            StaticReviewer(payloads.get("security")),
        ),
        "qa": ExecutionSeat(
            "qa", "mock/qwen-qa", "qwen", StaticReviewer(payloads.get("qa")),
        ),
        "perf": ExecutionSeat(
            "perf", "mock/gpt-perf", "openai", StaticReviewer(payloads.get("perf")),
        ),
        "docs": ExecutionSeat(
            "docs", "mock/claude-docs", "anthropic", StaticReviewer(payloads.get("docs")),
        ),
    }
    return coder, reviewers


def setup_run(name: str, coder: StaticCoder, *, target: str = "src.py",
              max_requests: int = 20):
    root = make_repo(name)
    wt_root = TEMP_ROOT / f"{name}-wt"
    rmtree(wt_root)
    runner = V2Runner(root, state_root=root / "state", worktree_root=wt_root)
    prepared = runner.prepare("M3 execution test", phase_ids=["P1"])
    run_id = prepared["run_id"]
    base = prepared["workspace"]["base_sha"]
    plan = make_plan(run_id, base, target=target, max_requests=max_requests)
    validate_plan(plan, worktree=prepared["workspace"]["worktree_path"])
    runner.submit_plan(run_id, plan)
    runner.signoff(run_id, approved_by="tests")
    coder_seat, reviewers = seats()
    coder_seat = ExecutionSeat("coder", "mock/deepseek-coder", "deepseek", coder)
    return runner, root, wt_root, run_id, coder_seat, reviewers, plan


def cleanup(runner: V2Runner, root: Path, wt_root: Path) -> None:
    try:
        for run_dir in (root / "state" / "runs").glob("*"):
            manifest = run_dir / "run_manifest.json"
            if not manifest.exists():
                continue
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if payload.get("workspace"):
                try:
                    isolation.remove_worktree(
                        isolation.Workspace.from_dict(payload["workspace"]),
                        keep_branch=False,
                    )
                except Exception:
                    pass
    finally:
        rmtree(wt_root)
        rmtree(root)


def test_happy_path(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run("happy", coder)
    try:
        result = ExecutionEngine(runner).execute(
            run_id, coder=coder_seat, reviewers=reviewers
        )
        checks.check("m3: happy path reaches handoff", result.status == "HANDOFF_READY")
        checks.check("m3: happy path has checkpoint", len(result.checkpoints) == 1)
        checks.check("m3: main worktree unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
        workspace = runner.workspace(run_id)
        checks.check("m3: worktree has verified value",
                     (workspace.path / "src.py").read_text(encoding="utf-8") == "VALUE = 2\n")
        checks.check("m3: coder called once", coder.calls == 1)
    finally:
        cleanup(runner, root, wt_root)


def test_repair(checks: Checks) -> None:
    coder = StaticCoder([
        {"src.py": "VALUE = 3\n"},
        {"src.py": "VALUE = 2\n"},
    ])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run("repair", coder)
    try:
        result = ExecutionEngine(runner, max_repair_attempts=3).execute(
            run_id, coder=coder_seat, reviewers=reviewers
        )
        checks.check("m3: repair succeeds on second attempt",
                     result.status == "HANDOFF_READY" and result.repairs == 1)
        checks.check("m3: repair left verified value",
                     (runner.workspace(run_id).path / "src.py").read_text(encoding="utf-8")
                     == "VALUE = 2\n")
    finally:
        cleanup(runner, root, wt_root)


def test_cross_family(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run("cross", coder)
    reviewers["qa"] = ExecutionSeat(
        "qa", "mock/deepseek-qa", "deepseek", StaticReviewer()
    )
    try:
        failed = False
        try:
            ExecutionEngine(runner).execute(
                run_id, coder=coder_seat, reviewers=reviewers
            )
        except CrossFamilyError:
            failed = True
        checks.check("m3: same-family reviewer is rejected", failed)
        checks.check("m3: main unchanged after family rejection",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
    finally:
        cleanup(runner, root, wt_root)


def test_security_veto(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "security",
        coder,
    )
    reviewers["security"] = ExecutionSeat(
        "security", "mock/glm-security", "glm",
        StaticReviewer({
            "verdict": "reject",
            "severity": "blocker",
            "evidence": ["injection"],
        }),
    )
    try:
        vetoed = False
        try:
            ExecutionEngine(runner).execute(
                run_id, coder=coder_seat, reviewers=reviewers
            )
        except SecurityVeto:
            vetoed = True
        checks.check("m3: security veto stops execution", vetoed)
        checks.check("m3: vetoed worktree is rolled back",
                     (runner.workspace(run_id).path / "src.py").read_text(encoding="utf-8")
                     == "VALUE = 1\n")
        checks.check("m3: vetoed main is unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
        checks.check("m3: veto leaves run quarantined",
                     runner.load(run_id).load()["status"] == "QUARANTINED")
    finally:
        cleanup(runner, root, wt_root)


def test_test_integrity(checks: Checks) -> None:
    coder = StaticCoder([{"tests/test_src.py": "def test_value():\n    assert False\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "integrity", coder, target="tests/test_src.py"
    )
    try:
        blocked = False
        try:
            ExecutionEngine(runner).execute(
                run_id, coder=coder_seat, reviewers=reviewers
            )
        except TestIntegrityError:
            blocked = True
        checks.check("m3: coder cannot write a test file", blocked)
        checks.check("m3: test file remains original",
                     (runner.workspace(run_id).path / "tests" / "test_src.py").read_text(
                         encoding="utf-8"
                     ).startswith("def test_value"))
    finally:
        cleanup(runner, root, wt_root)


def test_no_progress(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 3\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "no-progress", coder
    )
    try:
        stopped = False
        try:
            ExecutionEngine(runner, max_repair_attempts=3, max_no_progress=1).execute(
                run_id, coder=coder_seat, reviewers=reviewers
            )
        except NoProgressError:
            stopped = True
        checks.check("m3: repeated failure stops on no-progress", stopped)
        checks.check("m3: no-progress main unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
    finally:
        cleanup(runner, root, wt_root)


def test_budget_stop(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "budget", coder, max_requests=3
    )
    try:
        stopped = False
        try:
            ExecutionEngine(runner).execute(
                run_id, coder=coder_seat, reviewers=reviewers
            )
        except BudgetExceeded:
            stopped = True
        checks.check("m3: phase budget stops before review overrun", stopped)
        checks.check("m3: budget stop leaves main unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
        checks.check("m3: budget stop leaves worktree at base",
                     (runner.workspace(run_id).path / "src.py").read_text(encoding="utf-8")
                     == "VALUE = 1\n")
    finally:
        cleanup(runner, root, wt_root)


def main() -> int:
    rmtree(TEMP_ROOT)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    checks = Checks()
    print("[1/7] happy path ...")
    test_happy_path(checks)
    print("[2/7] repair ...")
    test_repair(checks)
    print("[3/7] cross-family ...")
    test_cross_family(checks)
    print("[4/7] security veto ...")
    test_security_veto(checks)
    print("[5/7] test integrity ...")
    test_test_integrity(checks)
    print("[6/7] no-progress ...")
    test_no_progress(checks)
    print("[7/7] per-phase budget stop ...")
    test_budget_stop(checks)
    print("")
    print(f"V2 M3: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    rmtree(TEMP_ROOT)
    return 0 if checks.passed == checks.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
