#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-command swarm CLI: doctor, planner, usage gate, worktree, handoff.

    python .agents/tests/test_swarm_cli.py

The CLI is exercised end-to-end through ``v2.swarm_cli.main`` with fake model
seats. Git worktrees, plan validation, usage decisions, signoff, execution,
verification and handoff are all real.
"""

from __future__ import annotations

import contextlib
import io
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
from v2.execution import ExecutionSeat  # noqa: E402
from v2.plan import validate_plan  # noqa: E402
from v2.swarm_cli import main as swarm_main  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "swarm_cli_tests"


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


def make_repo(name: str) -> tuple[Path, Path, Path]:
    root = TEMP_ROOT / name
    state = TEMP_ROOT / f"{name}-state"
    worktrees = TEMP_ROOT / f"{name}-wt"
    for path in (root, state, worktrees):
        rmtree(path)
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
    (root / "tests").mkdir()
    (root / "tests" / "test_src.py").write_text(
        "def test_value():\n    assert True\n", encoding="utf-8"
    )
    (root / ".gitignore").write_text(
        ".agents/tmp/\n.agents/backups/\n", encoding="utf-8"
    )
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return root, state, worktrees


class FakeCoder:
    def __init__(self) -> None:
        self.calls = 0

    def propose(self, work_order, *, cwd, round_index, feedback):
        self.calls += 1
        target = str((work_order.get("target_files") or ["src.py"])[0])
        if target == "src.py":
            return {"src.py": "VALUE = 2\n"}
        return {target: "# updated by fake coder\n"}


class FakeReviewer:
    def __init__(self) -> None:
        self.calls = 0

    def review(self, role, *, diff, work_order, phase):
        self.calls += 1
        return {"verdict": "approve", "severity": "low", "evidence": [role]}


def fake_seat_factory(**kwargs):
    coder = ExecutionSeat("coder", "mock/deepseek-free", "deepseek", FakeCoder())
    families = {
        "security": "glm",
        "qa": "qwen",
        "perf": "openai",
        "docs": "anthropic",
    }
    reviewers: dict[str, ExecutionSeat] = {}
    for role in kwargs.get("reviewer_models") or {}:
        reviewers[role] = ExecutionSeat(
            role, f"mock/{role}-free", families.get(role, "other"), FakeReviewer()
        )
    return coder, reviewers


def run_cli(args: list[str], **kwargs):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = swarm_main(args, **kwargs)
    text = buffer.getvalue()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {}
    return code, payload, text


def base_run_args(root: Path, state: Path, worktrees: Path) -> list[str]:
    return [
        "--root", str(root),
        "--state-root", str(state),
        "--worktree-root", str(worktrees),
        "--json",
    ]


def test_wrappers(checks: Checks) -> None:
    for name in ("swarm.cmd", "swarm.sh"):
        path = AGENTS_DIR.parent / name
        checks.check(f"wrapper: {name} exists", path.is_file(), str(path))
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            checks.check(f"wrapper: {name} calls the CLI",
                         "swarm_cli.py" in text, text[:200])
    shell = AGENTS_DIR.parent / "swarm.sh"
    if shell.is_file():
        checks.check("wrapper: swarm.sh has a shebang",
                     shell.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash"))


def test_doctor(checks: Checks) -> None:
    root, state, worktrees = make_repo("doctor")
    try:
        code, payload, text = run_cli(
            ["doctor", *base_run_args(root, state, worktrees)]
        )
        checks.check("doctor: reports a machine-readable result",
                     code in (0, 2) and isinstance(payload, dict)
                     and "checks" in payload, text[-800:])
        code, payload, text = run_cli(
            ["doctor", "--root", str(root), "--json"]
        )
        checks.check("doctor: json output is machine readable",
                     code in (0, 2) and isinstance(payload, dict)
                     and "checks" in payload, text[-800:])
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def test_planner_only(checks: Checks) -> None:
    root, state, worktrees = make_repo("planner")
    try:
        out = root / "plan.json"
        code, payload, text = run_cli(
            [
                "planner",
                "--goal", "Set VALUE to 2 in src.py",
                "--root", str(root),
                "--verify", "python check.py",
                "--out", str(out),
                "--json",
            ]
        )
        checks.check("planner: exits 0", code == 0, text[-800:])
        checks.check("planner: writes a plan", out.is_file(), str(out))
        checks.check("planner: reports the tier",
                     payload.get("tier") == "E2", payload)
        if out.is_file():
            plan = json.loads(out.read_text(encoding="utf-8"))
            checks.check("planner: plan validates",
                         bool(validate_plan(plan, worktree=root)))
        checks.check("planner: does not create run state",
                     not (state / "runs").exists())
        checks.check("planner: does not touch main",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def test_e2_end_to_end(checks: Checks) -> None:
    root, state, worktrees = make_repo("e2-e2e")
    try:
        code, payload, text = run_cli(
            [
                "run",
                "Set VALUE to 2 in src.py",
                "--verify", "python check.py",
                "--tier", "E2",
                *base_run_args(root, state, worktrees),
            ],
            seat_factory=fake_seat_factory,
        )
        checks.check("e2e E2: exits 0", code == 0, text[-1200:])
        checks.check("e2e E2: reaches handoff",
                     payload.get("status") == "HANDOFF_READY", payload)
        execution = payload.get("execution") or {}
        checks.check("e2e E2: one reviewer role",
                     execution.get("reviewer_roles") == ["security"], execution)
        checks.check("e2e E2: main stays unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
        run_id = payload.get("run_id")
        checks.check("e2e E2: run id is reported", bool(run_id), payload)
        if run_id:
            runner_workspace = payload.get("worktree")
            path = Path(str(runner_workspace)) if runner_workspace else None
            checks.check("e2e E2: worktree contains verified change",
                         bool(path) and
                         (path / "src.py").read_text(encoding="utf-8") == "VALUE = 2\n",
                         runner_workspace)
            patch = state / "runs" / run_id / "handoff" / "handoff.patch"
            checks.check("e2e E2: handoff patch exists", patch.is_file(), str(patch))
            code, status, text = run_cli(
                ["status", "--run-id", run_id,
                 "--root", str(root), "--state-root", str(state), "--json"]
            )
            checks.check("e2e E2: status reports handoff",
                         code == 0 and status.get("status") == "HANDOFF_READY",
                         text[-800:])
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def test_e1_end_to_end(checks: Checks) -> None:
    root, state, worktrees = make_repo("e1-e2e")
    try:
        code, payload, text = run_cli(
            [
                "run",
                "Set VALUE to 2 in src.py",
                "--verify", "python check.py",
                "--tier", "E1",
                "--e1-opt-in",
                *base_run_args(root, state, worktrees),
            ],
            seat_factory=fake_seat_factory,
        )
        checks.check("e2e E1: exits 0", code == 0, text[-1200:])
        checks.check("e2e E1: reaches handoff",
                     payload.get("status") == "HANDOFF_READY", payload)
        execution = payload.get("execution") or {}
        checks.check("e2e E1: zero reviewer roles",
                     execution.get("reviewer_roles") == [], execution)
        checks.check("e2e E1: verify still changed the worktree",
                     (Path(str(payload.get("worktree"))) / "src.py")
                     .read_text(encoding="utf-8") == "VALUE = 2\n"
                     if payload.get("worktree") else False)
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def test_e1_opt_in_required(checks: Checks) -> None:
    root, state, worktrees = make_repo("e1-no-optin")
    try:
        code, payload, text = run_cli(
            [
                "run",
                "Set VALUE to 2 in src.py",
                "--verify", "python check.py",
                "--tier", "E1",
                *base_run_args(root, state, worktrees),
            ],
            seat_factory=fake_seat_factory,
        )
        checks.check("e2e E1: missing opt-in fails closed",
                     code == 2 and "opt-in" in str(payload.get("error", "")).lower(),
                     text[-1000:])
        checks.check("e2e E1: no run state is created for a rejected plan",
                     not (state / "runs").exists())
        checks.check("e2e E1: main is untouched",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def test_usage_gate(checks: Checks) -> None:
    root, state, worktrees = make_repo("usage-gate")
    try:
        v1_plan = {
            "job_id": "J1",
            "objective": "two-subtask usage gate",
            "subtasks": [
                {
                    "id": "S1",
                    "objective": "Set VALUE to 2",
                    "verify_command": "python check.py",
                    "target_files": ["src.py"],
                    "level": 1,
                    "max_rounds": 2,
                    "depends_on": [],
                },
                {
                    "id": "S2",
                    "objective": "Update extra.py",
                    "verify_command": "python -c \"print('ok')\"",
                    "target_files": ["extra.py"],
                    "level": 1,
                    "max_rounds": 1,
                    "depends_on": ["S1"],
                },
            ],
        }
        plan_path = TEMP_ROOT / "usage-gate-plan.json"
        plan_path.write_text(json.dumps(v1_plan), encoding="utf-8")

        code, payload, text = run_cli(
            [
                "run",
                "--plan", str(plan_path),
                *base_run_args(root, state, worktrees),
            ],
            seat_factory=fake_seat_factory,
        )
        checks.check("usage: required approval returns exit 3", code == 3,
                     text[-1200:])
        checks.check("usage: status is AWAITING_USAGE",
                     payload.get("status") == "AWAITING_USAGE", payload)
        checks.check("usage: estimate crosses the request threshold",
                     int((payload.get("usage") or {}).get("estimate", {})
                         .get("requests", 0)) >= 8, payload.get("usage"))
        checks.check("usage: main stays unchanged without approval",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")

        code, payload, text = run_cli(
            [
                "run",
                "--plan", str(plan_path),
                "--yes",
                *base_run_args(root, state, worktrees),
            ],
            seat_factory=fake_seat_factory,
        )
        checks.check("usage: --yes approves and exits 0", code == 0,
                     text[-1500:])
        checks.check("usage: approved run reaches handoff",
                     payload.get("status") == "HANDOFF_READY", payload)
        checks.check("usage: approved run still leaves main unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def test_dry_run(checks: Checks) -> None:
    root, state, worktrees = make_repo("dry-run")
    try:
        code, payload, text = run_cli(
            [
                "run",
                "Set VALUE to 2 in src.py",
                "--verify", "python check.py",
                "--tier", "E2",
                "--dry-run",
                *base_run_args(root, state, worktrees),
            ],
            seat_factory=fake_seat_factory,
        )
        checks.check("dry-run: exits 0", code == 0, text[-1000:])
        checks.check("dry-run: stops before execution",
                     payload.get("status") == "AWAITING_SIGNOFF", payload)
        checks.check("dry-run: no handoff directory",
                     not any((state / "runs").glob("*/handoff")))
        checks.check("dry-run: main stays unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def test_explicit_merge(checks: Checks) -> None:
    root, state, worktrees = make_repo("merge")
    try:
        code, payload, text = run_cli(
            [
                "run",
                "Set VALUE to 2 in src.py",
                "--verify", "python check.py",
                "--tier", "E2",
                *base_run_args(root, state, worktrees),
            ],
            seat_factory=fake_seat_factory,
        )
        run_id = payload.get("run_id")
        checks.check("merge: setup run succeeds", code == 0 and bool(run_id),
                     text[-1200:])
        if not run_id:
            return
        checks.check("merge: main unchanged before explicit merge",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
        code, merged, text = run_cli(
            ["merge", "--run-id", run_id,
             "--root", str(root), "--state-root", str(state), "--json"]
        )
        checks.check("merge: explicit merge exits 0", code == 0, text[-1200:])
        checks.check("merge: main is updated after merge",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 2\n",
                     merged)
        checks.check("merge: verify gate passed", merged.get("verify", {}).get("exit_code", 0) == 0
                     if isinstance(merged.get("verify"), dict) else True, merged)
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def main() -> int:
    rmtree(TEMP_ROOT)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    checks = Checks()
    print("[1/9] wrappers ...")
    test_wrappers(checks)
    print("[2/9] doctor ...")
    test_doctor(checks)
    print("[3/9] planner only ...")
    test_planner_only(checks)
    print("[4/9] E2 end-to-end ...")
    test_e2_end_to_end(checks)
    print("[5/9] E1 end-to-end ...")
    test_e1_end_to_end(checks)
    print("[6/9] E1 opt-in gate ...")
    test_e1_opt_in_required(checks)
    print("[7/9] usage gate ...")
    test_usage_gate(checks)
    print("[8/9] dry run ...")
    test_dry_run(checks)
    print("[9/9] explicit merge ...")
    test_explicit_merge(checks)
    print("")
    print(f"SWARM CLI: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    rmtree(TEMP_ROOT)
    return 0 if checks.passed == checks.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
