#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M1 acceptance - worktree isolation, state machine and per-phase budget.

    python .agents/tests/test_v2_m1.py

These tests use temporary Git repositories only. They never call a model and
never write to the real project worktree.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2 import isolation  # noqa: E402
from v2.budget import (  # noqa: E402
    BudgetConfig,
    BudgetController,
    Envelope,
    PhaseBudget,
)
from v2.errors import (  # noqa: E402
    BudgetBlocked,
    BudgetError,
    BudgetExceeded,
    BudgetOverrun,
    CleanTreeRequired,
    IsolationGuardFailed,
    StateTransitionError,
)
from v2.runner import V2Runner  # noqa: E402
from v2.state import RunStore  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "v2_m1_tests"


def rmtree(path: str | Path, *, ignore_errors: bool = False) -> None:
    """Remove a temporary directory, including read-only Git object files."""
    def onerror(func, target, exc_info):
        try:
            Path(target).chmod(0o700)
            func(target)
        except OSError:
            pass

    try:
        shutil.rmtree(path, onerror=onerror)
    except OSError:
        if not ignore_errors:
            raise


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


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        timeout=60,
    )


def make_repo(name: str) -> Path:
    root = TEMP_ROOT / name
    rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Tests")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    (root / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return root


def remove_workspace(workspace: isolation.Workspace) -> None:
    try:
        isolation.remove_worktree(workspace, keep_branch=False)
    except Exception:
        pass
    rmtree(workspace.worktree_root, ignore_errors=True)


def test_isolation(checks: Checks) -> None:
    root = make_repo("isolation")
    wt_root = TEMP_ROOT / "isolation-wt"
    rmtree(wt_root, ignore_errors=True)
    try:
        (root / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        refused = False
        try:
            isolation.create_worktree(root, "dirty", worktree_root=wt_root)
        except CleanTreeRequired:
            refused = True
        checks.check("isolation: dirty main tree is refused", refused)
        (root / "dirty.txt").unlink()

        workspace = isolation.create_worktree(root, "run-a", worktree_root=wt_root)
        checks.check("isolation: worktree exists", workspace.path.is_dir())
        checks.check(
            "isolation: worktree is outside main",
            not isolation._is_within(workspace.path, workspace.main_root),
        )
        checks.check(
            "isolation: guard accepts real worktree",
            isolation.assert_isolated_worktree(root, workspace.path, wt_root)
            == workspace.path,
        )

        # A user edit in the main tree must not appear in the worktree.
        (root / "README.md").write_text("user edit\n", encoding="utf-8")
        checks.check(
            "isolation: main edit does not reach worktree",
            (workspace.path / "README.md").read_text(encoding="utf-8") == "base\n",
        )

        # A worktree edit must not appear in the main tree.
        (workspace.path / "README.md").write_text("worktree edit\n", encoding="utf-8")
        checks.check(
            "isolation: worktree edit does not reach main",
            (root / "README.md").read_text(encoding="utf-8") == "user edit\n",
        )

        rejected = False
        try:
            isolation.assert_isolated_worktree(root, root, wt_root)
        except IsolationGuardFailed:
            rejected = True
        checks.check("isolation: main tree is rejected as worktree", rejected)

        outside = TEMP_ROOT / "outside-worktree"
        rejected = False
        try:
            isolation.assert_isolated_worktree(root, outside, wt_root)
        except IsolationGuardFailed:
            rejected = True
        checks.check("isolation: path outside root is rejected", rejected)

        remove_workspace(workspace)
        checks.check("isolation: cleanup removed worktree", not workspace.path.exists())
    finally:
        rmtree(wt_root, ignore_errors=True)
        rmtree(root, ignore_errors=True)


def test_checkpoint_and_quarantine(checks: Checks) -> None:
    root = make_repo("checkpoint")
    wt_root = TEMP_ROOT / "checkpoint-wt"
    rmtree(wt_root, ignore_errors=True)
    try:
        workspace = isolation.create_worktree(root, "run-cp", worktree_root=wt_root)
        (workspace.path / "src.py").write_text("VALUE = 2\n", encoding="utf-8")
        checkpoint = isolation.checkpoint(workspace, "P1")
        checks.check("checkpoint: has commit", len(checkpoint.get("sha", "")) == 40)
        checks.check("checkpoint: has ref", checkpoint.get("ref", "").startswith("refs/swarm/"))

        (workspace.path / "src.py").write_text("VALUE = 3\n", encoding="utf-8")
        (workspace.path / "new.txt").write_text("new\n", encoding="utf-8")
        evidence = isolation.quarantine_and_rollback(
            workspace,
            root / "state",
            "P1",
            reason="verify failed",
            last_checkpoint=checkpoint["sha"],
            verify={"exit_code": 1, "stderr": "boom"},
        )
        checks.check(
            "quarantine: worktree rolled back",
            (workspace.path / "src.py").read_text(encoding="utf-8") == "VALUE = 2\n",
        )
        checks.check(
            "quarantine: main remains unchanged",
            (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n",
        )
        checks.check("quarantine: patch saved", Path(evidence["patch"]).exists())
        checks.check(
            "quarantine: untracked file saved",
            (Path(evidence["patch"]).parent / "untracked" / "new.txt").exists(),
        )
        checks.check("quarantine: manifest saved",
                     (Path(evidence["patch"]).parent / "quarantine.json").exists())
        remove_workspace(workspace)
    finally:
        rmtree(wt_root, ignore_errors=True)
        rmtree(root, ignore_errors=True)


def test_budget(checks: Checks) -> None:
    root = TEMP_ROOT / "budget-state"
    rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    config = BudgetConfig(
        envelope=Envelope(
            max_requests=3,
            max_cost_usd=1.0,
            max_wall_seconds=100,
            emergency_reserve_pct=0,
            max_repair_attempts_per_phase=1,
        ),
        phases={"P1": PhaseBudget(2, 0.5, 60)},
    )
    budget = BudgetController(root, config)
    first = budget.reserve("P1", role="coder", model="free", cost_usd=0.0)
    budget.settle(first, requests=1, tokens=10, cost_usd=0.0)
    second = budget.reserve("P1", role="coder", model="free", cost_usd=0.0)
    budget.settle(second, requests=1, tokens=10, cost_usd=0.0)
    exceeded = False
    try:
        budget.reserve("P1", role="coder", model="free", cost_usd=0.0)
    except BudgetExceeded:
        exceeded = True
    checks.check("budget: four-phase default envelope validates",
                 BudgetConfig.default(["P1", "P2", "P3", "P4"]).validate() is not None)
    checks.check("budget: phase request cap blocks before call", exceeded)
    checks.check(
        "budget: phase remaining is zero",
        budget.remaining("P1")["phase"]["requests"] == 0,
        str(budget.remaining("P1")),
    )

    # A fresh controller must see the persisted spend.
    reloaded = BudgetController(root, config)
    checks.check(
        "budget: state survives reload",
        reloaded.remaining("P1")["phase"]["requests"] == 0,
    )

    # Config changes are rejected for an existing run.
    changed = BudgetConfig(
        envelope=Envelope(4, 1.0, 100, 0, 1),
        phases={"P1": PhaseBudget(2, 0.5, 60)},
    )
    mismatch = False
    try:
        BudgetController(root, changed)
    except BudgetError:
        mismatch = True
    checks.check("budget: config change is rejected after start", mismatch)

    invalid_state = TEMP_ROOT / "budget-invalid"
    rmtree(invalid_state, ignore_errors=True)
    invalid_state.mkdir(parents=True)
    invalid = False
    try:
        BudgetController(
            invalid_state,
            BudgetConfig(
                envelope=Envelope(2, 1.0, 100, 0, 1),
                phases={"P1": PhaseBudget(3, 0.5, 100)},
            ),
        )
    except BudgetError:
        invalid = True
    checks.check("budget: phase total over global is rejected", invalid)

    # Unknown cost blocks a paid call.
    unknown = False
    try:
        budget.reserve("P1", role="coder", model="paid", cost_usd=None)
    except BudgetBlocked:
        unknown = True
    checks.check("budget: unknown paid cost is blocked", unknown)

    # Repair attempts are capped independently.
    repair_state = TEMP_ROOT / "budget-repair"
    rmtree(repair_state, ignore_errors=True)
    repair_state.mkdir(parents=True)
    repair_budget = BudgetController(
        repair_state,
        BudgetConfig(
            envelope=Envelope(5, 1.0, 100, 0, 1),
            phases={"P1": PhaseBudget(5, 1.0, 100)},
        ),
    )
    rid = repair_budget.reserve(
        "P1", role="coder", model="free", cost_usd=0.0, repair=True
    )
    repair_budget.settle(rid, requests=1, cost_usd=0.0)
    repair_blocked = False
    try:
        repair_budget.reserve(
            "P1", role="coder", model="free", cost_usd=0.0, repair=True
        )
    except BudgetExceeded:
        repair_blocked = True
    checks.check("budget: repair cap is enforced", repair_blocked)

    # Overrun stops the run.
    over_state = TEMP_ROOT / "budget-overrun"
    rmtree(over_state, ignore_errors=True)
    over_state.mkdir(parents=True)
    over_budget = BudgetController(
        over_state,
        BudgetConfig(
            envelope=Envelope(4, 1.0, 100, 0, 1),
            phases={"P1": PhaseBudget(4, 1.0, 100)},
        ),
    )
    rid = over_budget.reserve("P1", role="coder", model="free", cost_usd=0.1)
    over = False
    try:
        over_budget.settle(rid, requests=1, cost_usd=0.2)
    except BudgetOverrun:
        over = True
    checks.check("budget: overrun raises", over)
    checks.check("budget: overrun stops future calls", over_budget.stopped)
    stopped = False
    try:
        over_budget.reserve("P1", role="coder", model="free", cost_usd=0.0)
    except BudgetBlocked:
        stopped = True
    checks.check("budget: stopped run rejects new reservation", stopped)


def test_state_machine(checks: Checks) -> None:
    root = TEMP_ROOT / "state-repo"
    rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    store = RunStore.create(root, goal="state test", base_sha="0" * 40)
    checks.check("state: starts CREATED", store.load()["status"] == "CREATED")
    store.transition("preflight_ok")
    store.transition("start_planning")
    store.transition("plan_valid")
    checks.check("state: reaches AWAITING_SIGNOFF",
                 store.load()["status"] == "AWAITING_SIGNOFF")
    illegal = False
    try:
        store.transition("verify_pass")
    except StateTransitionError:
        illegal = True
    checks.check("state: illegal transition is rejected", illegal)
    store.transition("pause")
    checks.check("state: pause stores PAUSED", store.load()["status"] == "PAUSED")
    store.transition("resume")
    checks.check("state: resume returns to previous state",
                 store.load()["status"] == "AWAITING_SIGNOFF")
    store.transition("block", reason="auth expired")
    checks.check("state: block stores BLOCKED", store.load()["status"] == "BLOCKED")
    store.transition("resume")
    checks.check("state: blocked run can resume",
                 store.load()["status"] == "AWAITING_SIGNOFF")
    store.record_checkpoint({"phase_id": "P1", "sha": "a" * 40})
    checks.check("state: checkpoint persisted",
                 store.checkpoint_for("P1")["sha"] == "a" * 40)


def test_runner(checks: Checks) -> None:
    root = make_repo("runner")
    wt_root = TEMP_ROOT / "runner-wt"
    rmtree(wt_root, ignore_errors=True)
    try:
        runner = V2Runner(root, state_root=root / "state", worktree_root=wt_root)
        prepared = runner.prepare("runner test", phase_ids=["P1"])
        run_id = prepared["run_id"]
        checks.check("runner: run is prechecked",
                     prepared["manifest"]["status"] == "PRECHECKED")
        checks.check("runner: budget exists", prepared["budget"] is not None)
        workspace = runner.workspace(run_id)
        checks.check("runner: worktree created", workspace.path.is_dir())

        store = runner.load(run_id)
        store.transition("start_planning")
        store.transition("plan_valid")
        store.transition("sign_off")
        store.transition("worktree_ready")
        (workspace.path / "src.py").write_text("VALUE = 9\n", encoding="utf-8")
        checkpoint = runner.checkpoint_phase(run_id, "P1")
        checks.check("runner: checkpoint recorded",
                     store.checkpoint_for("P1")["sha"] == checkpoint["sha"])
        handoff = runner.handoff(run_id)
        checks.check("runner: handoff patch exists", Path(handoff["patch"]).exists())
        checks.check("runner: handoff report exists", Path(handoff["report"]).exists())
        checks.check("runner: main not modified",
                     (root / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n")
        checks.check("runner: state is handoff ready",
                     store.load()["status"] == "HANDOFF_READY")
        remove_workspace(workspace)
    finally:
        rmtree(wt_root, ignore_errors=True)
        rmtree(root, ignore_errors=True)


def main() -> int:
    rmtree(TEMP_ROOT, ignore_errors=True)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    checks = Checks()
    print("[1/5] worktree isolation ...")
    test_isolation(checks)
    print("[2/5] checkpoint and quarantine ...")
    test_checkpoint_and_quarantine(checks)
    print("[3/5] per-phase budget ...")
    test_budget(checks)
    print("[4/5] state machine ...")
    test_state_machine(checks)
    print("[5/5] runner handoff ...")
    test_runner(checks)
    print("")
    print(f"V2 M1: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    rmtree(TEMP_ROOT, ignore_errors=True)
    return 0 if checks.passed == checks.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
