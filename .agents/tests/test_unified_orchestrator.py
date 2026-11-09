#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified orchestrator: preflight -> plan -> Usage -> execute -> handoff.

    python .agents/tests/test_unified_orchestrator.py

The suite exercises the real v2 worktree/state/execution engine with fake model
seats. It also pins the two non-negotiable safety invariants:

* preflight is recorded before any planner or model call;
* a required Usage decision stops the run before a seat is built, and the
  orchestrator never merges into main.
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

from unified.contracts import UnifiedRequest  # noqa: E402
from unified.orchestrator import UnifiedOrchestrator  # noqa: E402
from v2.execution import ExecutionSeat  # noqa: E402
from v2.plan import validate_plan  # noqa: E402
from v2.planner_adapter import PlanningError, from_goal  # noqa: E402
from v2.swarm_cli import main as swarm_main  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "unified_orchestrator_tests"


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
        return {target: "# created by fake orchestrator coder\n"}


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


def good_doctor(log: list[str]):
    def doctor(root: Path):
        log.append("preflight")
        return [
            {"name": "git", "status": "ok", "detail": "repo"},
            {"name": "worktree", "status": "ok", "detail": str(root)},
        ]

    return doctor


def bad_doctor(log: list[str]):
    def doctor(root: Path):
        log.append("preflight")
        return [{"name": "git", "status": "bad", "detail": "git missing"}]

    return doctor


def request_for(root: Path, state: Path, worktrees: Path, **kwargs) -> UnifiedRequest:
    base = dict(
        root=str(root),
        state_root=str(state),
        worktree_root=str(worktrees),
        approved_by="tests",
    )
    base.update(kwargs)
    return UnifiedRequest(**base)


def two_subtask_plan(root: Path) -> dict:
    plan = {
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
    path = TEMP_ROOT / f"{root.name}-usage_plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def test_e2_end_to_end(checks: Checks) -> None:
    root, state, worktrees = make_repo("e2")
    log: list[str] = []
    try:
        planner_calls: list[str] = []

        def planner_goal(goal, **kwargs):
            planner_calls.append(goal)
            from v2.planner_adapter import from_goal

            return from_goal(goal, **kwargs)

        def seat_factory(**kwargs):
            log.append("model")
            return fake_seat_factory(**kwargs)

        orchestrator = UnifiedOrchestrator(
            seat_factory=seat_factory,
            planner_goal=planner_goal,
            doctor=good_doctor(log),
        )
        result = orchestrator.run(
            request_for(
                root, state, worktrees,
                goal="Set VALUE to 2 in src.py",
                verify_command="python check.py",
                tier="E2",
            )
        )
        payload = result.as_dict()
        checks.check("e2e: reaches HANDOFF_READY",
                     result.status == "HANDOFF_READY", payload)
        checks.check("e2e: exit code is zero", result.exit_code == 0)
        checks.check("e2e: tier is E2", result.tier == "E2", result.tier)
        checks.check("e2e: planner is P1", result.planner == "P1", result.planner)
        checks.check("e2e: run id is reported", bool(result.run_id), payload)
        checks.check("e2e: reviewer roles are E2",
                     (result.execution or {}).get("reviewer_roles") == ["security"],
                     result.execution)
        checks.check("e2e: main stays unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
        checks.check("e2e: worktree has the verified change",
                     bool(result.worktree)
                     and (Path(result.worktree) / "src.py").read_text(
                         encoding="utf-8") == "VALUE = 2\n",
                     result.worktree)
        checks.check("e2e: handoff patch exists",
                     bool(result.run_id) and
                     (state / "runs" / result.run_id / "handoff" /
                      "handoff.patch").is_file())
        checks.check("e2e: proposed plan is inspectable",
                     bool(result.plan_path) and Path(result.plan_path).is_file(),
                     result.plan_path)
        checks.check("e2e: progress is exposed",
                     (result.progress or {}).get("stage") == "handoff",
                     result.progress)
        checks.check("e2e: result never exposes merge",
                     "merge" not in payload and not hasattr(result, "merge"))
        checks.check("e2e: preflight ran before planner",
                     log[0] == "preflight" and len(planner_calls) == 2)
        checks.check("e2e: preflight ran before a model seat",
                     log.index("preflight") < log.index("model"), log)
        checks.check("e2e: doctor note is carried",
                     (result.doctor or {}).get("bad") == 0, result.doctor)
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def test_preflight_blocks(checks: Checks) -> None:
    root, state, worktrees = make_repo("preflight")
    log: list[str] = []
    try:
        planner_called = False
        seat_called = False

        def planner_goal(goal, **kwargs):
            nonlocal planner_called
            planner_called = True
            raise AssertionError("planner must not run after a failed preflight")

        def seat_factory(**kwargs):
            nonlocal seat_called
            seat_called = True
            raise AssertionError("a model seat must not be built after a failed preflight")

        orchestrator = UnifiedOrchestrator(
            seat_factory=seat_factory,
            planner_goal=planner_goal,
            doctor=bad_doctor(log),
        )
        result = orchestrator.run(
            request_for(
                root, state, worktrees,
                goal="Set VALUE to 2 in src.py",
                verify_command="python check.py",
            )
        )
        checks.check("preflight: failure is DOCTOR_FAILED",
                     result.status == "DOCTOR_FAILED", result.status)
        checks.check("preflight: failure exits 2", result.exit_code == 2)
        checks.check("preflight: failure carries the blocker",
                     bool((result.preflight or {}).get("blocking")),
                     result.preflight)
        checks.check("preflight: planner is not called", planner_called is False)
        checks.check("preflight: model seat is not built", seat_called is False)
        checks.check("preflight: no run state is created",
                     not (state / "runs").exists())
        checks.check("preflight: log contains only the gate", log == ["preflight"], log)
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def test_usage_gate(checks: Checks) -> None:
    root, state, worktrees = make_repo("usage")
    log: list[str] = []
    try:
        plan_path = two_subtask_plan(root)
        seat_calls: list[str] = []

        def seat_factory(**kwargs):
            seat_calls.append("model")
            return fake_seat_factory(**kwargs)

        orchestrator = UnifiedOrchestrator(
            seat_factory=seat_factory,
            doctor=good_doctor(log),
        )
        waiting = orchestrator.run(
            request_for(root, state, worktrees, plan_path=str(plan_path))
        )
        checks.check("usage: required approval exits 3",
                     waiting.exit_code == 3, waiting.as_dict())
        checks.check("usage: status is AWAITING_USAGE",
                     waiting.status == "AWAITING_USAGE", waiting.status)
        checks.check("usage: reasons are reported",
                     bool((waiting.usage or {}).get("reasons")),
                     waiting.usage)
        checks.check("usage: no model seat before approval",
                     seat_calls == [], seat_calls)
        checks.check("usage: main stays unchanged before approval",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
        checks.check("usage: proposed plan is still recorded",
                     bool(waiting.plan_path) and Path(waiting.plan_path).is_file(),
                     waiting.plan_path)
        checks.check("usage: council metadata follows the P2 tier",
                     (waiting.plan_decision or {}).get("council", {}).get("level")
                     == "P2",
                     waiting.plan_decision)

        approved = orchestrator.run(
            request_for(root, state, worktrees, plan_path=str(plan_path), yes=True)
        )
        checks.check("usage: --yes approves and reaches handoff",
                     approved.status == "HANDOFF_READY", approved.as_dict())
        checks.check("usage: approved run builds one seat factory call",
                     seat_calls == ["model"], seat_calls)
        checks.check("usage: approved run leaves main unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
        checks.check("usage: approved result never auto-merges",
                     "merge" not in approved.as_dict())
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def test_plan_only_and_caps(checks: Checks) -> None:
    root, state, worktrees = make_repo("plan-only")
    log: list[str] = []
    try:
        orchestrator = UnifiedOrchestrator(
            doctor=good_doctor(log),
            seat_factory=lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("plan-only must not build a model seat")
            ),
        )
        out = root / "preview_plan.json"
        result = orchestrator.run(
            request_for(
                root, state, worktrees,
                goal="Set VALUE to 2 in src.py",
                verify_command="python check.py",
                tier="E2",
                plan_only=True,
                out=str(out),
                max_cost=0.1,
                max_requests=12,
                max_wall_seconds=600,
            )
        )
        checks.check("plan-only: status is PLANNED", result.status == "PLANNED")
        checks.check("plan-only: exits zero", result.exit_code == 0)
        checks.check("plan-only: plan file is written", out.is_file(), str(out))
        checks.check("plan-only: no run state is created",
                     not (state / "runs").exists())
        checks.check("plan-only: no model seat is built", log == ["preflight"], log)
        checks.check("plan-only: caps are reported",
                     (result.caps or {}).get("max_requests") == 12
                     and (result.caps or {}).get("max_cost_usd") == 0.1,
                     result.caps)
        plan = json.loads(out.read_text(encoding="utf-8"))
        checks.check("plan-only: capped plan still validates",
                     bool(validate_plan(plan, worktree=root)))
        checks.check("plan-only: envelope cannot be raised",
                     float(plan["budget"]["cost_usd"]) <= 0.1,
                     plan["budget"])
        checks.check("plan-only: plan decision matches the plan",
                     (result.plan_decision or {}).get("plan_hash") == result.plan_hash,
                     result.plan_decision)
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def test_order_shim(checks: Checks) -> None:
    root, state, worktrees = make_repo("order")
    log: list[str] = []
    try:
        order = {
            "task_id": "NEW1",
            "title": "Create a new module",
            "level": 1,
            "objective": "Create newmod.py with a marker",
            "boundaries": "Only newmod.py",
            "input_context": "The file does not exist yet",
            "acceptance_criteria": "newmod.py exists",
            "verify_command": (
                "python -c \"from pathlib import Path; "
                "assert Path('newmod.py').is_file()\""
            ),
            "target_files": ["newmod.py"],
            "max_rounds": 1,
        }
        order_path = TEMP_ROOT / "order-new1.json"
        order_path.write_text(json.dumps(order), encoding="utf-8")

        result = UnifiedOrchestrator(
            seat_factory=fake_seat_factory,
            doctor=good_doctor(log),
        ).run(
            request_for(root, state, worktrees, order_path=str(order_path))
        )
        checks.check("order: shim reaches HANDOFF_READY",
                     result.status == "HANDOFF_READY", result.as_dict())
        checks.check("order: plan source is recorded",
                     (result.plan_decision or {}).get("source") == "order",
                     result.plan_decision)
        checks.check("order: new file is created in the worktree",
                     bool(result.worktree)
                     and (Path(result.worktree) / "newmod.py").is_file(),
                     result.worktree)
        checks.check("order: main is untouched",
                     not (root / "newmod.py").exists())
        checks.check("order: plan validates",
                     bool(validate_plan(
                         json.loads(Path(result.plan_path).read_text(encoding="utf-8")),
                         worktree=Path(str(result.worktree)),
                     )))
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def test_cli_order_and_council(checks: Checks) -> None:
    root, state, worktrees = make_repo("cli-order")
    try:
        order = {
            "task_id": "ORD2",
            "title": "Order through the CLI",
            "level": 1,
            "objective": "Set VALUE to 2 in src.py",
            "boundaries": "Only src.py",
            "input_context": "VALUE starts at 1",
            "acceptance_criteria": "check.py exits 0",
            "verify_command": "python check.py",
            "target_files": ["src.py"],
            "max_rounds": 1,
        }
        order_path = TEMP_ROOT / "order-cli.json"
        order_path.write_text(json.dumps(order), encoding="utf-8")

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = swarm_main(
                [
                    "planner", "--order", str(order_path),
                    "--root", str(root), "--json",
                    "--council",
                ]
            )
        text = buffer.getvalue()
        checks.check("cli order: planner exits 0", code == 0, text[-800:])
        planned = json.loads(text) if text.strip().startswith("{") else {}
        checks.check("cli order: planner reports an order source",
                     planned.get("source") == "order", planned)
        checks.check("cli order: planner emits a plan",
                     bool(planned.get("plan")), planned)

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = swarm_main(
                [
                    "run", "--order", str(order_path),
                    "--root", str(root),
                    "--state-root", str(state),
                    "--worktree-root", str(worktrees),
                    "--json", "--yes",
                ],
                seat_factory=fake_seat_factory,
            )
        text = buffer.getvalue()
        payload = json.loads(text) if text.strip().startswith("{") else {}
        checks.check("cli order: run exits 0", code == 0, text[-1200:])
        checks.check("cli order: run reaches handoff",
                     payload.get("status") == "HANDOFF_READY", payload)
        checks.check("cli order: main unchanged",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def test_council_execution(checks: Checks) -> None:
    root, state, worktrees = make_repo("council")
    try:
        common = dict(
            run_id="r",
            base_sha="HEAD",
            root=root,
            verify_command="python check.py",
        )
        plain = from_goal(
            "Set VALUE to 2 in src.py", tier="E3", council=False, **common,
        )
        deterministic = from_goal(
            "Set VALUE to 2 in src.py", tier="E3", **common,
        )
        checks.check("council: E3 selects P2",
                     deterministic.planner == "P2", deterministic.planner)
        checks.check("council: deterministic P2 executes two seats",
                     (deterministic.council or {}).get("required_planners") == 2
                     and (deterministic.council or {}).get("valid_seats") == 2,
                     deterministic.council)
        checks.check("council: deterministic council keeps the plan hash stable",
                     deterministic.plan_hash == plain.plan_hash,
                     (deterministic.plan_hash, plain.plan_hash))
        checks.check("council: selected plan validates",
                     bool(validate_plan(deterministic.plan, worktree=root)))

        calls: list[tuple[str, str | None]] = []

        def planner_call(role, model, prompt):
            calls.append((role, model))
            return plain.plan

        model_council = from_goal(
            "Set VALUE to 2 in src.py",
            tier="E3",
            council=True,
            planner_models=["mock/architect", "mock/pragmatist"],
            planner_call=planner_call,
            **common,
        )
        checks.check("council: model seats receive their roles and models",
                     calls == [("architect", "mock/architect"),
                               ("pragmatist", "mock/pragmatist")],
                     calls)
        checks.check("council: model proposals are selected",
                     (model_council.council or {}).get("valid_seats") == 2,
                     model_council.council)
        checks.check("council: model plan validates",
                     bool(validate_plan(model_council.plan, worktree=root)))

        e4 = from_goal(
            "Set VALUE to 2 in src.py", tier="E4", council=True, **common,
        )
        checks.check("council: E4 selects P3",
                     e4.planner == "P3", e4.planner)
        checks.check("council: P3 executes three seats",
                     (e4.council or {}).get("required_planners") == 3
                     and (e4.council or {}).get("valid_seats") == 3,
                     e4.council)

        failed = False
        try:
            from_goal(
                "Set VALUE to 2 in src.py",
                tier="E3",
                council=True,
                planner_models=["mock/one"],
                planner_call=lambda role, model, prompt: {},
                **common,
            )
        except PlanningError:
            failed = True
        checks.check("council: invalid model proposals fail closed", failed)
    finally:
        rmtree(root)
        rmtree(state)
        rmtree(worktrees)


def main() -> int:
    rmtree(TEMP_ROOT)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    checks = Checks()
    print("[1/7] E2 end-to-end ...")
    test_e2_end_to_end(checks)
    print("[2/7] preflight blocks ...")
    test_preflight_blocks(checks)
    print("[3/7] Usage gate ...")
    test_usage_gate(checks)
    print("[4/7] plan-only + caps ...")
    test_plan_only_and_caps(checks)
    print("[5/7] order shim ...")
    test_order_shim(checks)
    print("[6/7] CLI order + council ...")
    test_cli_order_and_council(checks)
    print("[7/7] council execution ...")
    test_council_execution(checks)
    print("")
    print(f"UNIFIED ORCHESTRATOR: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    rmtree(TEMP_ROOT)
    return 0 if checks.passed == checks.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
