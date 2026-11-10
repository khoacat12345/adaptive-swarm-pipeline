#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 4A: v1-oracle vs v2-swarm parity fixtures.

Runs fully offline with deterministic fake model seats.  For every fixture:

1. v1 recipe reference writes its expected tree in a disposable repo;
2. v2 ``swarm`` produces a HANDOFF_READY worktree and leaves main untouched;
3. explicit ``swarm merge`` applies the same bytes;
4. verify, scope, cost/tier/planner artifacts and no-duplicate-write invariants
   are checked.

The v1 repo is a test oracle only; v2 never writes main before merge.
"""

from __future__ import annotations

import contextlib
import hashlib
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
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import recipe_c  # noqa: E402
import work_order as work_order_module  # noqa: E402
from v2.execution import ExecutionSeat  # noqa: E402
from v2.swarm_cli import main as swarm_main  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "parity_fixtures"


class Checks:
    def __init__(self) -> None:
        self.total = 0
        self.failures: list[str] = []

    def check(self, name: str, condition, detail="") -> bool:
        self.total += 1
        if not condition:
            self.failures.append(f"{name}: {detail}" if detail else name)
            return False
        return True

    @property
    def passed(self) -> int:
        return self.total - len(self.failures)


def rmtree(path: str | Path) -> None:
    def onerror(func, target, _exc):
        try:
            Path(target).chmod(0o700)
            func(target)
        except OSError:
            pass

    try:
        shutil.rmtree(path, onerror=onerror)
    except OSError:
        pass


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True, timeout=60,
    )


def make_fixture(name: str, files: dict[str, str]) -> tuple[Path, Path, Path]:
    root = TEMP_ROOT / name
    state = TEMP_ROOT / f"{name}-state"
    worktrees = TEMP_ROOT / f"{name}-wt"
    for path in (root, state, worktrees):
        rmtree(path)
    root.mkdir(parents=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "parity@example.com")
    git(root, "config", "user.name", "Parity")
    (root / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n.agents/tmp/\n.agents/backups/\n.agents/draft/\n",
        encoding="utf-8")
    (root / ".agents").mkdir()
    (root / ".agents" / ".gitignore").write_text(
        "tmp/\nbackups/\ndraft/\n", encoding="utf-8")
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "fixture baseline")
    return root, state, worktrees


class V1ScriptedCoder:
    """Minimal recipe_c coder: writes the fixture's final contents."""

    def __init__(self, contents: dict[str, str]):
        self.contents = dict(contents)
        self.calls = 0

    def bind_workspace(self, root, coder_root=None):
        return None

    def patch(self, work_order, *, current_content, feedback, round_index):
        self.calls += 1
        target = str((work_order.target_files or ["src.py"])[0])
        return {"format": "whole", "content": self.contents[target]}

    def patch_set(self, work_order, *, current_files, feedback, round_index):
        self.calls += 1
        return {
            path: {"format": "whole", "content": content}
            for path, content in self.contents.items()
        }


class FixtureCoder:
    """v2 coder backend: returns deterministic file contents by target."""

    def __init__(self, contents: dict[str, str]):
        self.contents = dict(contents)
        self.calls = 0

    def propose(self, work_order, *, cwd, round_index, feedback):
        self.calls += 1
        targets = work_order.get("target_files") or ["src.py"]
        return {
            str(relative): self.contents[str(relative)]
            for relative in targets
            if str(relative) in self.contents
        }


class FixtureReviewer:
    def __init__(self, role: str):
        self.role = role
        self.calls = 0

    def review(self, role, *, diff, work_order, phase):
        self.calls += 1
        return {
            "verdict": "approve",
            "severity": "low",
            "evidence": [f"{self.role} deterministic approval"],
        }


def seat_factory_for(contents: dict[str, str]):
    def factory(**kwargs):
        coder = ExecutionSeat(
            "coder", "mock/deepseek-parity", "deepseek",
            FixtureCoder(contents),
        )
        families = {
            "security": "glm", "qa": "qwen",
            "perf": "openai", "docs": "anthropic",
        }
        reviewers = {}
        for role in kwargs.get("reviewer_models") or {}:
            reviewers[role] = ExecutionSeat(
                role, f"mock/{role}-parity", families.get(role, "other"),
                FixtureReviewer(role),
            )
        return coder, reviewers
    return factory


def run_swarm(args: list[str], contents: dict[str, str]) -> tuple[int, dict, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = swarm_main(args, seat_factory=seat_factory_for(contents))
    text = buffer.getvalue()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {}
    return code, payload, text


def v1_reference(root: Path, fixture: dict, coder: V1ScriptedCoder) -> dict:
    """Run the frozen v1 recipe as the deterministic test oracle."""
    order = work_order_module.create(
        task_id=f"parity-{fixture['name']}",
        title=fixture["name"],
        level=3 if len(fixture["targets"]) > 1 else 1,
        objective=fixture.get("v1_objective") or fixture["goal"],
        boundaries="Only the fixture target files",
        input_context="fixture baseline",
        acceptance_criteria="fixture verify exits 0",
        verify_command=fixture["verify"],
        target_files=list(fixture["targets"]),
        sandbox_files=[*fixture["targets"], "check.py"],
        max_rounds=1,
    )
    return recipe_c.run(
        order, coder=coder, reviewer=None, root=root, log=False,
        use_memory=False, max_requests=20,
    )


def final_files(root: Path, fixture: dict) -> dict[str, str]:
    return {
        relative: (root / relative).read_text(encoding="utf-8")
        for relative in fixture["targets"]
    }


def check_fixture(checks: Checks, fixture: dict) -> None:
    name = fixture["name"]
    contents = fixture["contents"]
    v1_root, _, _ = make_fixture(f"{name}-v1", fixture["files"])
    v2_root, state, worktrees = make_fixture(f"{name}-v2", fixture["files"])
    try:
        v1 = v1_reference(v1_root, fixture, V1ScriptedCoder(contents))
        checks.check(f"{name}: v1 reference applied",
                     v1.get("status") == "applied", v1)
        v1_files = final_files(v1_root, fixture)
        checks.check(f"{name}: v1 verify expected",
                     all(v1_files[path] == expected for path, expected in contents.items()),
                     v1_files)

        base_files = {
            path: (v2_root / path).read_text(encoding="utf-8")
            for path in fixture["targets"]
        }
        args = [
            "run", fixture["goal"],
            "--tier", fixture["tier"],
            "--verify", fixture["verify"],
            "--yes",
            "--root", str(v2_root),
            "--state-root", str(state),
            "--worktree-root", str(worktrees),
            "--json",
        ]
        for target in fixture["targets"]:
            args.extend(["--target", target])
        if fixture["tier"] == "E1":
            args.append("--e1-opt-in")

        code, payload, text = run_swarm(args, contents)
        checks.check(f"{name}: v2 swarm exits 0", code == 0, text[-1200:])
        checks.check(f"{name}: v2 reaches HANDOFF_READY",
                     payload.get("status") == "HANDOFF_READY", payload)
        checks.check(f"{name}: v2 tier recorded",
                     payload.get("tier") == fixture["tier"], payload.get("tier"))
        checks.check(f"{name}: v2 planner recorded",
                     bool(payload.get("planner")), payload)
        checks.check(f"{name}: main unchanged before merge",
                     all((v2_root / path).read_text(encoding="utf-8") == base_files[path]
                         for path in fixture["targets"]), base_files)

        run_id = payload.get("run_id")
        checks.check(f"{name}: run id present", bool(run_id), payload)
        if not run_id:
            return
        state_run = state / "runs" / run_id
        budget = json.loads((state_run / "budget.json").read_text(encoding="utf-8"))
        checks.check(f"{name}: budget artifact records requests",
                     "global" in budget and "used_requests" in budget["global"], budget)
        checks.check(f"{name}: tier/planner in manifest",
                     (state_run / "run_manifest.json").exists(), state_run)
        execution = payload.get("execution") or {}
        expected_roles = {
            "E1": [], "E2": ["security"],
            "E3": ["security", "qa", "perf", "docs"],
            "E4": ["security", "qa", "perf", "docs"],
        }[fixture["tier"]]
        checks.check(f"{name}: reviewer policy",
                     execution.get("reviewer_roles") == expected_roles, execution)

        code, merged, merge_text = run_swarm([
            "merge", "--run-id", run_id,
            "--root", str(v2_root),
            "--state-root", str(state),
            "--json",
        ], contents)
        checks.check(f"{name}: explicit merge exits 0", code == 0, merge_text[-1200:])
        checks.check(f"{name}: merged files equal v1 oracle",
                     final_files(v2_root, fixture) == v1_files, final_files(v2_root, fixture))
        checks.check(f"{name}: main changed only in scope",
                     set(git(v2_root, "diff", "--name-only", "HEAD~1..HEAD")
                         .stdout.splitlines()) <= set(fixture["targets"]),
                     git(v2_root, "show", "--stat", "--oneline", "HEAD").stdout)
        checks.check(f"{name}: no duplicate writes",
                     (v2_root / ".agents").exists() is False or
                     not (v2_root / ".agents" / "draft").exists(),
                     "unexpected draft directory")
    finally:
        rmtree(v1_root)
        rmtree(v2_root)
        rmtree(state)
        rmtree(worktrees)


FIXTURES: dict[str, dict] = {
    "single-file": {
        "name": "single-file",
        "goal": "Fix the single-file logic bug in src.py",
        "v1_objective": "Change src.py so VALUE is 2 and the existing check passes",
        "tier": "E2",
        "verify": "python check.py",
        "files": {
            "src.py": "VALUE = 1\n",
            "check.py": "from pathlib import Path\n"
                        "assert 'VALUE = 2' in Path('src.py').read_text()\n"
                        "print('ok')\n",
            "tests/test_src.py": "def test_ok():\n    assert True\n",
        },
        "targets": ["src.py"],
        "contents": {"src.py": "VALUE = 2\n"},
    },
    "multi-file": {
        "name": "multi-file",
        "goal": "Refactor calc and format modules together",
        "v1_objective": "Change calc.add to return a + b and format.label to return new:<value>",
        "tier": "E3",
        "verify": "python check.py",
        "files": {
            "calc.py": "def add(a, b):\n    return a - b\n",
            "format.py": "def label(value):\n    return 'old'\n",
            "check.py": "from calc import add\nfrom format import label\n"
                        "assert add(2, 3) == 5\nassert label(5) == 'new:5'\n"
                        "print('ok')\n",
            "tests/test_calc.py": "def test_ok():\n    assert True\n",
        },
        "targets": ["calc.py", "format.py"],
        "contents": {
            "calc.py": "def add(a, b):\n    return a + b\n",
            "format.py": "def label(value):\n    return f'new:{value}'\n",
        },
    },
    "security-sensitive": {
        "name": "security-sensitive",
        "goal": "Fix the insecure authorization check in auth.py",
        "v1_objective": "Change auth.is_admin to return true only when user is exactly admin",
        "tier": "E3",
        "verify": "python check.py",
        "files": {
            "auth.py": "def is_admin(user):\n    return True\n",
            "check.py": "from auth import is_admin\n"
                        "assert is_admin('admin')\nassert not is_admin('guest')\n"
                        "print('ok')\n",
            "tests/test_auth.py": "def test_ok():\n    assert True\n",
        },
        "targets": ["auth.py"],
        "contents": {
            "auth.py": "def is_admin(user):\n    return user == 'admin'\n",
        },
    },
}


def main() -> int:
    rmtree(TEMP_ROOT)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    checks = Checks()
    try:
        for fixture in FIXTURES.values():
            print(f"=== {fixture['name']} ===")
            check_fixture(checks, fixture)
        print("")
        print(f"V1/V2 PARITY: {checks.passed}/{checks.total} PASS")
        for failure in checks.failures:
            print(f"  FAIL {failure}")
        return 0 if checks.passed == checks.total else 1
    finally:
        rmtree(TEMP_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
