#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tier-aware v2 execution: E1/E2/E3/E4 runtime policy.

    python .agents/tests/test_v2_tiers.py

All execution uses temporary Git worktrees and fake coder/reviewer backends.
No model is called and the real project is never written.
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

from v2 import isolation  # noqa: E402
from v2.errors import ExecutionError, TestIntegrityError  # noqa: E402
from v2.execution import (  # noqa: E402
    ExecutionEngine,
    ExecutionSeat,
    is_sensitive_path,
    tier_profile,
)
from v2.plan import validate_plan  # noqa: E402
from v2.runner import V2Runner  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "v2_tier_tests"


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


def make_repo(name: str, extra_files: dict[str, str] | None = None) -> Path:
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
    for relative, content in (extra_files or {}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return root


def make_plan(
    run_id: str,
    base_sha: str,
    *,
    tier: str | None = None,
    target: str = "src.py",
    opt_in: bool = False,
    extra_allowed: list[str] | None = None,
) -> dict:
    allowed = ["src.py", "check.py", "tests/test_src.py", target]
    allowed.extend(extra_allowed or [])
    plan = {
        "schema_version": "2.1",
        "run_id": run_id,
        "goal": "tier execution test",
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
                "title": "Tier execution",
                "depends_on": [],
                "allowed_paths": sorted(set(allowed)),
                "forbidden_paths": [".git", ".env", ".agents/tmp"],
                "budget": {"requests": 20, "cost_usd": 0.70, "wall_seconds": 600},
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
    if tier is not None:
        plan["execution_tier"] = tier
    if opt_in:
        plan["low_risk_opt_in"] = True
    return plan


class StaticCoder:
    def __init__(self, contents: list[dict[str, str]]):
        self.contents = contents
        self.calls = 0

    def propose(self, work_order, *, cwd, round_index, feedback):
        self.calls += 1
        return dict(self.contents[min(round_index - 1, len(self.contents) - 1)])


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


def seats(coder: StaticCoder):
    coder_seat = ExecutionSeat("coder", "mock/deepseek-coder", "deepseek", coder)
    reviewers = {
        "security": ExecutionSeat(
            "security", "mock/glm-security", "glm", StaticReviewer()
        ),
        "qa": ExecutionSeat("qa", "mock/qwen-qa", "qwen", StaticReviewer()),
        "perf": ExecutionSeat("perf", "mock/gpt-perf", "openai", StaticReviewer()),
        "docs": ExecutionSeat(
            "docs", "mock/claude-docs", "anthropic", StaticReviewer()
        ),
    }
    return coder_seat, reviewers


def setup_run(
    name: str,
    coder: StaticCoder,
    *,
    tier: str | None = None,
    target: str = "src.py",
    opt_in: bool = False,
    extra_files: dict[str, str] | None = None,
):
    root = make_repo(name, extra_files=extra_files)
    wt_root = TEMP_ROOT / f"{name}-wt"
    rmtree(wt_root)
    runner = V2Runner(root, state_root=root / "state", worktree_root=wt_root)
    prepared = runner.prepare("tier execution test", phase_ids=["P1"])
    run_id = prepared["run_id"]
    base = prepared["workspace"]["base_sha"]
    plan = make_plan(
        run_id, base, tier=tier, target=target, opt_in=opt_in,
        extra_allowed=sorted((extra_files or {}).keys()),
    )
    validate_plan(plan, worktree=prepared["workspace"]["worktree_path"])
    runner.submit_plan(run_id, plan)
    runner.signoff(run_id, approved_by="tests")
    coder_seat, reviewers = seats(coder)
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


def reviewer_calls(reviewers) -> dict[str, int]:
    return {role: seat.backend.calls for role, seat in reviewers.items()}


def test_default_plan_is_e3(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, payload = setup_run(
        "tier-default", coder
    )
    try:
        checks.check("tier: old plan carries no explicit tier",
                     "execution_tier" not in payload)
        result = ExecutionEngine(runner).execute(
            run_id, coder=coder_seat, reviewers=reviewers,
        )
        checks.check("tier: old plan defaults to E3",
                     result.tier == "E3" and result.planner == "P2", result.as_dict())
        checks.check("tier: default E3 calls four reviewers",
                     all(count == 1 for count in reviewer_calls(reviewers).values()),
                     reviewer_calls(reviewers))
        events = runner.load(run_id).read_events()
        checks.check("tier: selection is recorded",
                     any(event.get("event_type") == "tier.selected"
                         and (event.get("data") or {}).get("tier") == "E3"
                         for event in events))
    finally:
        cleanup(runner, root, wt_root)


def test_e2_security_only(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "tier-e2", coder, tier="E2"
    )
    try:
        result = ExecutionEngine(runner).execute(
            run_id, coder=coder_seat, reviewers=reviewers,
        )
        calls = reviewer_calls(reviewers)
        checks.check("E2: reaches handoff", result.status == "HANDOFF_READY",
                     result.as_dict())
        checks.check("E2: only the security reviewer is called",
                     calls["security"] == 1 and
                     all(calls[role] == 0 for role in ("qa", "perf", "docs")), calls)
        checks.check("E2: result exposes one reviewer",
                     result.reviewer_roles == ("security",), result.reviewer_roles)
        checks.check("E2: verify still runs",
                     (runner.workspace(run_id).path / "src.py").read_text(encoding="utf-8")
                     == "VALUE = 2\n")
    finally:
        cleanup(runner, root, wt_root)


def test_e2_missing_security_blocked(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "tier-e2-missing", coder, tier="E2"
    )
    try:
        partial = {role: reviewers[role] for role in ("qa", "perf", "docs")}
        blocked = False
        try:
            ExecutionEngine(runner).execute(
                run_id, coder=coder_seat, reviewers=partial,
            )
        except ExecutionError as exc:
            blocked = "security" in str(exc)
        checks.check("E2: missing security seat is fail-closed", blocked)
        checks.check("E2: coder is never called when a seat is missing",
                     coder.calls == 0)
    finally:
        cleanup(runner, root, wt_root)


def test_e3_missing_reviewer_blocked(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "tier-e3-missing", coder, tier="E3"
    )
    try:
        partial = {"security": reviewers["security"]}
        blocked = False
        try:
            ExecutionEngine(runner).execute(
                run_id, coder=coder_seat, reviewers=partial,
            )
        except ExecutionError:
            blocked = True
        checks.check("E3: four reviewer seats remain mandatory", blocked)
        checks.check("E3: coder is never called with missing seats",
                     coder.calls == 0)
    finally:
        cleanup(runner, root, wt_root)


def test_e1_requires_opt_in(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "tier-e1-no-optin", coder, tier="E1"
    )
    try:
        blocked = False
        try:
            ExecutionEngine(runner).execute(
                run_id, coder=coder_seat, reviewers=reviewers,
            )
        except ExecutionError as exc:
            blocked = "opt-in" in str(exc).lower()
        checks.check("E1: tier without opt-in is rejected", blocked)
        checks.check("E1: rejected plan never calls the coder", coder.calls == 0)
        checks.check("E1: rejected plan leaves main unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
    finally:
        cleanup(runner, root, wt_root)


def test_e1_zero_reviewers(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "tier-e1", coder, tier="E1", opt_in=True
    )
    try:
        result = ExecutionEngine(runner).execute(
            run_id, coder=coder_seat, reviewers={},
        )
        checks.check("E1: explicit opt-in reaches handoff",
                     result.status == "HANDOFF_READY", result.as_dict())
        checks.check("E1: no reviewer calls",
                     all(count == 0 for count in reviewer_calls(reviewers).values()),
                     reviewer_calls(reviewers))
        checks.check("E1: reviewer surface is empty",
                     result.reviewer_roles == (), result.reviewer_roles)
        checks.check("E1: verify still runs and changes the worktree",
                     (runner.workspace(run_id).path / "src.py").read_text(encoding="utf-8")
                     == "VALUE = 2\n")
        checks.check("E1: main stays unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
        events = runner.load(run_id).read_events()
        checks.check("E1: capability context is injected",
                     any(event.get("event_type") == "capability.context"
                         for event in events))
    finally:
        cleanup(runner, root, wt_root)


def test_e1_runtime_opt_in(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "tier-e1-runtime", coder, tier="E1", opt_in=False
    )
    try:
        result = ExecutionEngine(runner).execute(
            run_id, coder=coder_seat, reviewers={}, low_risk_opt_in=True,
        )
        checks.check("E1: CLI/runtime opt-in is honoured",
                     result.status == "HANDOFF_READY", result.as_dict())
    finally:
        cleanup(runner, root, wt_root)


def test_e1_sensitive_path_blocked(checks: Checks) -> None:
    checks.check("E1: sensitive path classifier catches secrets.py",
                 is_sensitive_path("secrets.py"))
    checks.check("E1: sensitive path classifier catches .env",
                 is_sensitive_path(".env"))
    checks.check("E1: normal source path stays allowed",
                 not is_sensitive_path("src.py"))
    coder = StaticCoder([{"secrets.py": "TOKEN = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "tier-e1-sensitive",
        coder,
        tier="E1",
        target="secrets.py",
        opt_in=True,
        extra_files={"secrets.py": "TOKEN = 1\n"},
    )
    try:
        blocked = False
        try:
            ExecutionEngine(runner).execute(
                run_id, coder=coder_seat, reviewers={},
            )
        except ExecutionError as exc:
            blocked = "sensitive" in str(exc).lower()
        checks.check("E1: sensitive target is blocked before the coder", blocked)
        checks.check("E1: sensitive file is untouched", coder.calls == 0)
    finally:
        cleanup(runner, root, wt_root)


def test_e1_secret_scan(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 'sk-abcdefghijklmnopqrstuvwx'\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "tier-e1-secret", coder, tier="E1", opt_in=True
    )
    try:
        blocked = False
        try:
            ExecutionEngine(runner).execute(
                run_id, coder=coder_seat, reviewers={},
            )
        except TestIntegrityError as exc:
            blocked = "secret" in str(exc).lower()
        checks.check("E1: secret scanning remains mandatory even with no reviewer",
                     blocked)
        checks.check("E1: secret never lands in the worktree",
                     (runner.workspace(run_id).path / "src.py").read_text(encoding="utf-8")
                     == "VALUE = 1\n")
    finally:
        cleanup(runner, root, wt_root)


def test_e4_meeting(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "tier-e4", coder, tier="E4"
    )
    try:
        result = ExecutionEngine(runner).execute(
            run_id, coder=coder_seat, reviewers=reviewers,
        )
        checks.check("E4: reaches handoff", result.status == "HANDOFF_READY",
                     result.as_dict())
        checks.check("E4: meeting flag is exposed",
                     result.tier == "E4" and result.planner == "P3",
                     result.as_dict())
        checks.check("E4: four reviewers called",
                     all(count == 1 for count in reviewer_calls(reviewers).values()),
                     reviewer_calls(reviewers))
        events = runner.load(run_id).read_events()
        types = [event.get("event_type") for event in events]
        checks.check("E4: blackboard meeting opened",
                     "meeting.opened" in types and "meeting.message" in types, types)
    finally:
        cleanup(runner, root, wt_root)


def test_unknown_tier(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, coder_seat, reviewers, _ = setup_run(
        "tier-unknown", coder, tier="E9"
    )
    try:
        blocked = False
        try:
            ExecutionEngine(runner).execute(
                run_id, coder=coder_seat, reviewers=reviewers,
            )
        except ExecutionError as exc:
            blocked = "unknown execution tier" in str(exc).lower()
        checks.check("tier: unknown tier is rejected", blocked)
    finally:
        cleanup(runner, root, wt_root)


def _run_cli(
    root: Path,
    state_root: Path,
    wt_root: Path,
    run_id: str,
    extra: list[str],
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(AGENTS_DIR / "v2" / "run.py"),
            "--root", str(root),
            "--state-root", str(state_root),
            "--worktree-root", str(wt_root),
            "execute",
            "--run-id", run_id,
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_run_cli_tier_guards(checks: Checks) -> None:
    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, _coder_seat, _reviewers, _ = setup_run(
        "tier-cli-e1", coder, tier="E1"
    )
    try:
        proc = _run_cli(
            root, root / "state", wt_root, run_id, ["--tier", "E1", "--free"]
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        checks.check("run.py: E1 without opt-in is rejected",
                     proc.returncode != 0 and "opt-in" in output.lower(), output[-800:])
    finally:
        cleanup(runner, root, wt_root)

    coder = StaticCoder([{"src.py": "VALUE = 2\n"}])
    runner, root, wt_root, run_id, _coder_seat, _reviewers, _ = setup_run(
        "tier-cli-e2", coder, tier="E2"
    )
    try:
        proc = _run_cli(
            root, root / "state", wt_root, run_id,
            ["--coder-model", "mock/coder", "--reviewer", "qa=mock/qa"],
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        checks.check("run.py: E2 requires the security seat",
                     proc.returncode != 0 and "security" in output.lower(),
                     output[-800:])
    finally:
        cleanup(runner, root, wt_root)


def test_profiles(checks: Checks) -> None:
    expected = {
        "E1": ((), False, "P1"),
        "E2": (("security",), False, "P1"),
        "E3": (("security", "qa", "perf", "docs"), False, "P2"),
        "E4": (("security", "qa", "perf", "docs"), True, "P3"),
    }
    for tier, (roles, meeting, planner) in expected.items():
        profile = tier_profile(tier)
        checks.check(f"profile: {tier} roles/meeting/planner",
                     profile.reviewer_roles == roles
                     and profile.meeting is meeting
                     and profile.planner == planner,
                     profile.as_dict())


def main() -> int:
    rmtree(TEMP_ROOT)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    checks = Checks()
    print("[1/13] profiles ...")
    test_profiles(checks)
    print("[2/13] default plan is E3 ...")
    test_default_plan_is_e3(checks)
    print("[3/13] E2 security only ...")
    test_e2_security_only(checks)
    print("[4/13] E2 missing security ...")
    test_e2_missing_security_blocked(checks)
    print("[5/13] E3 missing reviewer ...")
    test_e3_missing_reviewer_blocked(checks)
    print("[6/13] E1 requires opt-in ...")
    test_e1_requires_opt_in(checks)
    print("[7/13] E1 zero reviewers ...")
    test_e1_zero_reviewers(checks)
    print("[8/13] E1 runtime opt-in ...")
    test_e1_runtime_opt_in(checks)
    print("[9/13] E1 sensitive path ...")
    test_e1_sensitive_path_blocked(checks)
    print("[10/13] E1 secret scan ...")
    test_e1_secret_scan(checks)
    print("[11/13] E4 meeting ...")
    test_e4_meeting(checks)
    print("[12/13] unknown tier ...")
    test_unknown_tier(checks)
    print("[13/13] run.py tier guards ...")
    test_run_cli_tier_guards(checks)
    print("")
    print(f"V2 TIERS: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    rmtree(TEMP_ROOT)
    return 0 if checks.passed == checks.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
