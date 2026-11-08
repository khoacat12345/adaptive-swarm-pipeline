#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-command swarm entrypoint: doctor -> planner -> usage -> execute -> handoff.

    swarm "fix the failing add() test"
    swarm "task" --tier E2 --preset free-only
    swarm "task" --dry-run
    swarm doctor
    swarm planner "task" --out plan.json
    swarm status --last

The CLI never writes the main tree. Execution happens in a guarded worktree and
only ``swarm merge`` (or ``--merge``) applies the handoff patch explicitly.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v2.adapters import FREE_MODELS, build_worker_seats, free_seats_from_config  # noqa: E402
from v2.doctor import collect as doctor_collect  # noqa: E402
from v2.doctor import main as doctor_main  # noqa: E402
from v2.errors import ExecutionError, V2Error  # noqa: E402
from v2.execution import ExecutionEngine, resolve_tier_profile  # noqa: E402
from v2.models import is_free, load_config  # noqa: E402
from v2.plan import PlanValidationError, plan_hash, validate_plan  # noqa: E402
from v2.planner_adapter import (  # noqa: E402
    PlanAdapterResult,
    PlanningError,
    adapt_file,
    from_goal,
    write_plan,
)
from v2.presets import free_only as preset_free_only  # noqa: E402
from v2.runner import V2Runner  # noqa: E402

__version__ = "2.2.0"

COMMANDS = {"doctor", "planner", "run", "status", "handoff", "merge"}
REVIEW_ROLES = ("security", "qa", "perf", "docs")


def _normalize_argv(argv: Sequence[str]) -> list[str]:
    values = list(argv)
    if not values:
        return values
    if values[0] in COMMANDS or values[0] in {"-h", "--help", "--version"}:
        return values
    return ["run", *values]


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", default=".")
    parser.add_argument("--state-root", default=None)
    parser.add_argument("--worktree-root", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def _resolve_root(args: argparse.Namespace) -> Path:
    return Path(args.root).expanduser().resolve()


def _resolve_state_paths(root: Path, args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    def resolve(value: str | None) -> Path | None:
        if not value:
            return None
        path = Path(value).expanduser()
        return path if path.is_absolute() else (root / path).resolve()

    return resolve(args.state_root), resolve(args.worktree_root)


def _head_sha(root: Path) -> str:
    try:
        from v2.isolation import git_root, resolve_base_sha

        return resolve_base_sha(git_root(root), "HEAD")
    except Exception:
        return "HEAD"


def _runner(root: Path, args: argparse.Namespace) -> V2Runner:
    state_root, worktree_root = _resolve_state_paths(root, args)
    return V2Runner(
        root,
        state_root=state_root,
        worktree_root=worktree_root,
        require_clean=True,
    )


def _doctor_bad(root: Path) -> list[Any]:
    return [row for row in doctor_collect(root) if row.status == "bad"]


def _provisional_plan(
    args: argparse.Namespace,
    *,
    root: Path,
    run_id: str,
    base_sha: str,
    planner_goal: Callable[..., Any],
    planner_file: Callable[..., Any],
):
    if args.plan:
        return planner_file(
            args.plan,
            run_id=run_id,
            base_sha=base_sha,
            root=root,
            tier=args.tier,
            low_risk_opt_in=args.e1_opt_in,
            verify_command=args.verify,
        )
    return planner_goal(
        args.goal,
        run_id=run_id,
        base_sha=base_sha,
        root=root,
        targets=args.target,
        verify_command=args.verify,
        tier=args.tier,
        low_risk_opt_in=args.e1_opt_in,
    )


def _scale_requests(values: list[int], cap: int) -> list[int]:
    """Shrink integer allocations to fit ``cap`` while keeping each one >= 1."""
    total = sum(values)
    scaled = [max(1, int(value * cap / total)) for value in values]
    while sum(scaled) > cap:
        index = max(range(len(scaled)), key=lambda item: scaled[item])
        scaled[index] -= 1
    return scaled


def _cap_plan_budget(
    plan: Mapping[str, Any],
    *,
    max_cost: float | None,
    max_requests: int | None,
    max_wall_seconds: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Lower a plan budget to caller caps before the Usage decision.

    Caps can only shrink the plan's own envelope; the Usage gate still decides
    whether the smaller estimate needs explicit approval. Phase allocations are
    down-scaled so ``validate_plan`` keeps the envelope invariant.
    """
    if max_cost is None and max_requests is None and max_wall_seconds is None:
        return dict(plan), {}
    updated = json.loads(json.dumps(plan))
    budget = updated.get("budget")
    if not isinstance(budget, Mapping):
        raise ExecutionError("plan has no budget to cap")
    budget = dict(budget)
    nested = budget.get("global")
    target = dict(nested) if isinstance(nested, Mapping) else budget
    applied: dict[str, Any] = {}
    try:
        reserve_pct = float(target.get("emergency_reserve_pct", 10) or 0)
    except (TypeError, ValueError):
        reserve_pct = 10.0
    reserve_pct = max(0.0, min(100.0, reserve_pct))
    if max_requests is not None:
        target["requests"] = min(
            int(target.get("requests", 120) or 120), max(1, int(max_requests))
        )
        applied["max_requests"] = target["requests"]
    if max_cost is not None:
        target["cost_usd"] = round(
            min(float(target.get("cost_usd", 3.5) or 0.0), max(0.0, float(max_cost))),
            6,
        )
        applied["max_cost_usd"] = target["cost_usd"]
    if max_wall_seconds is not None:
        target["wall_seconds"] = min(
            int(target.get("wall_seconds", 36000) or 36000),
            max(1, int(max_wall_seconds)),
        )
        applied["max_wall_seconds"] = target["wall_seconds"]
    target["emergency_reserve_pct"] = reserve_pct
    if isinstance(nested, Mapping):
        budget["global"] = target
    else:
        budget = target
    updated["budget"] = budget

    phases = list(updated.get("phases") or [])
    if phases:
        envelope_requests = int(target.get("requests", 120) or 120)
        envelope_cost = float(target.get("cost_usd", 3.5) or 0.0)
        envelope_wall = int(target.get("wall_seconds", 36000) or 36000)
        allowed_requests = max(
            1, int(envelope_requests - envelope_requests * reserve_pct / 100.0)
        )
        requests = [
            max(1, int((phase.get("budget") or {}).get("requests", 1) or 1))
            for phase in phases
        ]
        if sum(requests) > allowed_requests:
            if allowed_requests < len(phases):
                raise ExecutionError(
                    f"--max-requests {envelope_requests} cannot fund "
                    f"{len(phases)} phases"
                )
            requests = _scale_requests(requests, allowed_requests)
        allowed_cost = envelope_cost * (1.0 - reserve_pct / 100.0)
        costs = [
            max(0.0, float((phase.get("budget") or {}).get("cost_usd", 0.0) or 0.0))
            for phase in phases
        ]
        total_cost = sum(costs)
        if total_cost > allowed_cost + 1e-9:
            scale = allowed_cost / total_cost if total_cost > 0 else 0.0
            costs = [math.floor(cost * scale * 1e6) / 1e6 for cost in costs]
        for index, phase in enumerate(phases):
            phase_budget = dict(phase.get("budget") or {})
            phase_budget["requests"] = requests[index]
            phase_budget["cost_usd"] = costs[index]
            try:
                wall = int(phase_budget.get("wall_seconds", 1) or 1)
            except (TypeError, ValueError):
                wall = 1
            phase_budget["wall_seconds"] = max(1, min(wall, envelope_wall))
            phase["budget"] = phase_budget
    return updated, applied


def _with_capped_plan(
    result: PlanAdapterResult,
    *,
    max_cost: float | None,
    max_requests: int | None,
    max_wall_seconds: int | None,
) -> tuple[PlanAdapterResult, dict[str, Any]]:
    plan, applied = _cap_plan_budget(
        result.plan,
        max_cost=max_cost,
        max_requests=max_requests,
        max_wall_seconds=max_wall_seconds,
    )
    if not applied:
        return result, {}
    return replace(result, plan=plan, plan_hash=plan_hash(plan)), applied


def _latest_run(runner: V2Runner) -> str:
    runs = sorted(
        (runner.state_root / "runs").glob("*/run_manifest.json"),
        key=lambda path: path.stat().st_mtime,
    )
    if not runs:
        raise ExecutionError(f"no v2 runs under {runner.state_root}")
    return runs[-1].parent.name


def _parse_reviewers(items: Sequence[str]) -> dict[str, str]:
    models: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ExecutionError(f"bad --reviewer value: {item!r}; use role=model")
        role, model = item.split("=", 1)
        role = role.strip()
        model = model.strip()
        if not role or not model:
            raise ExecutionError(f"bad --reviewer value: {item!r}; use role=model")
        models[role] = model
    return models


def _seat_models(
    args: argparse.Namespace,
    *,
    root: Path,
    profile: Any,
    config: Mapping[str, Any] | None,
) -> tuple[str, dict[str, str], bool]:
    explicit = _parse_reviewers(args.reviewer)
    use_free = bool(args.free or preset_free_only(args.preset) or not args.coder_model)
    if preset_free_only(args.preset) and args.coder_model:
        if not is_free(args.coder_model, config or {}):
            raise ExecutionError(
                "free-only preset blocks paid coder: " + args.coder_model
            )
    coder_model = args.coder_model
    reviewer_models: dict[str, str] = {}
    if use_free:
        if config is not None:
            try:
                free_coder, free_reviewers = free_seats_from_config(config)
            except Exception:
                free_coder, free_reviewers = (
                    FREE_MODELS["coder"],
                    {role: FREE_MODELS[role] for role in REVIEW_ROLES},
                )
        else:
            free_coder = FREE_MODELS["coder"]
            free_reviewers = {role: FREE_MODELS[role] for role in REVIEW_ROLES}
        coder_model = coder_model or free_coder
        for role in profile.reviewer_roles:
            reviewer_models[role] = explicit.get(role) or free_reviewers.get(
                role, FREE_MODELS.get(role, "")
            )
    else:
        for role in profile.reviewer_roles:
            if explicit.get(role):
                reviewer_models[role] = explicit[role]
    if not coder_model:
        raise ExecutionError("no coder model available; pass --coder-model or --free")
    missing = [role for role in profile.reviewer_roles if not reviewer_models.get(role)]
    if missing:
        raise ExecutionError(
            f"{profile.tier} requires reviewer model(s): {', '.join(missing)}"
        )
    if preset_free_only(args.preset):
        paid = [
            model for model in [coder_model, *reviewer_models.values()]
            if not (is_free(model, config or {}) if config is not None
                    else str(model).lower().endswith("-free"))
        ]
        if paid:
            raise ExecutionError(
                "free-only preset blocks paid models: " + ", ".join(paid)
            )
    return coder_model, reviewer_models, use_free


def _emit(payload: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    status = payload.get("status") or payload.get("ok")
    print(f"status: {status}")
    for key in ("run_id", "tier", "planner", "worktree", "plan_hash",
                "state_root", "next"):
        if payload.get(key):
            print(f"{key}: {payload[key]}")
    handoff = payload.get("handoff") or {}
    if isinstance(handoff, Mapping):
        for key in ("patch", "report", "worktree"):
            if handoff.get(key):
                print(f"handoff.{key}: {handoff[key]}")


def _run_task(
    args: argparse.Namespace,
    *,
    seat_factory: Callable[..., Any],
    planner_goal: Callable[..., Any],
    planner_file: Callable[..., Any],
) -> tuple[int, dict[str, Any]]:
    root = _resolve_root(args)
    if not args.goal and not args.plan:
        raise ExecutionError("run needs a goal or --plan")
    checks = doctor_collect(root)
    bad = [row for row in checks if row.status == "bad"]
    # A live model catalog is optional here: the seat builder has a documented
    # free-model fallback. Git/worktree failures remain hard blockers.
    blocking = [
        row for row in bad
        if row.name != "models" and not row.name.startswith("opencode/")
    ]
    if blocking:
        return 2, {
            "ok": False,
            "status": "DOCTOR_FAILED",
            "checks": [
                {"name": row.name, "status": row.status, "detail": row.detail}
                for row in blocking
            ],
        }
    doctor_note = {
        "bad": len(bad),
        "warn": sum(1 for row in checks if row.status == "warn"),
        "fallback_models": any(row.name == "models" for row in bad),
    }

    base_sha = _head_sha(root)
    provisional = _provisional_plan(
        args,
        root=root,
        run_id=args.run_id or "plan-preview",
        base_sha=base_sha,
        planner_goal=planner_goal,
        planner_file=planner_file,
    )
    provisional, caps = _with_capped_plan(
        provisional,
        max_cost=args.max_cost,
        max_requests=args.max_requests,
        max_wall_seconds=args.max_wall_seconds,
    )
    if args.plan_only:
        out = Path(args.out or (root / ".agents" / "tmp" / "swarm_plan.json"))
        if not out.is_absolute():
            out = (root / out).resolve()
        write_plan(out, provisional)
        payload = {
            "ok": True,
            "status": "PLANNED",
            "tier": provisional.tier,
            "planner": provisional.planner,
            "plan_hash": provisional.plan_hash,
            "plan": str(out),
            "summary": provisional.summary,
            "caps": caps,
        }
        _emit(payload, as_json=args.json)
        return 0, payload

    state_root, worktree_root = _resolve_state_paths(root, args)
    runner = V2Runner(
        root, state_root=state_root, worktree_root=worktree_root,
        require_clean=True,
    )
    from run_lock import LockBusy, RepoLock

    try:
        lock = RepoLock(root, owner="swarm").acquire()
    except LockBusy as exc:
        return 2, {"ok": False, "status": "LOCK_BUSY", "error": str(exc)}
    try:
        prepared = runner.prepare(
            provisional.plan.get("goal") or args.goal or "swarm task",
            base_sha=args.base_sha,
            run_id=args.run_id,
            phase_ids=[str(phase["id"]) for phase in provisional.plan["phases"]],
            preset=args.preset,
        )
        run_id = str(prepared["run_id"])
        workspace = prepared["workspace"]
        plan_result = _provisional_plan(
            args,
            root=root,
            run_id=run_id,
            base_sha=str(workspace["base_sha"]),
            planner_goal=planner_goal,
            planner_file=planner_file,
        )
        plan_result, caps = _with_capped_plan(
            plan_result,
            max_cost=args.max_cost,
            max_requests=args.max_requests,
            max_wall_seconds=args.max_wall_seconds,
        )
        plan = dict(plan_result.plan)
        if str(plan.get("schema_version") or "") == "2.1" and plan.get("phases"):
            plan["run_id"] = run_id
            plan["base_sha"] = str(workspace["base_sha"])
        validate_plan(plan, worktree=workspace["worktree_path"])
        plan_path = runner.load(run_id).run_dir / "proposed_plan.json"
        write_plan(plan_path, plan_result)
        submitted = runner.submit_plan(run_id, plan)
        decision = runner.usage_decision(run_id)
        usage = decision.as_dict()
        base_payload = {
            "ok": True,
            "run_id": run_id,
            "tier": plan.get("execution_tier") or plan.get("tier") or "E3",
            "planner": plan.get("planner_level")
            or plan_result.planner,
            "plan_hash": submitted["plan_hash"],
            "worktree": workspace.get("worktree_path"),
            "state_root": str(runner.load(run_id).run_dir),
            "plan": str(plan_path),
            "usage": usage,
            "doctor": doctor_note,
            "caps": caps,
        }
        if decision.required and not (args.approve_usage or args.yes):
            payload = {
                **base_payload,
                "status": "AWAITING_USAGE",
                "next": "rerun with --approve-usage after reviewing the estimate",
            }
            _emit(payload, as_json=args.json)
            return 3, payload
        if args.dry_run:
            payload = {
                **base_payload,
                "status": submitted["status"],
                "next": "rerun without --dry-run to sign off and execute",
            }
            _emit(payload, as_json=args.json)
            return 0, payload

        runner.signoff(run_id, approved_by=args.approved_by)
        profile = resolve_tier_profile(
            plan, tier=args.tier, low_risk_opt_in=args.e1_opt_in,
        )
        config = None
        try:
            config = load_config(root)
        except Exception:
            config = None
        coder_model, reviewer_models, use_free = _seat_models(
            args, root=root, profile=profile, config=config,
        )
        store = runner.load(run_id)
        coder, reviewers = seat_factory(
            coder_model=coder_model,
            reviewer_models=reviewer_models,
            sandbox_root=store.run_dir / "coder_sandbox",
            coder_kind=args.coder_kind,
            timeout=args.timeout,
            config=config,
            paid_cost_usd=args.paid_cost,
            free_only=bool(use_free or preset_free_only(args.preset)),
        )
        result = ExecutionEngine(runner).execute(
            run_id,
            coder=coder,
            reviewers=reviewers,
            tier=profile.tier,
            low_risk_opt_in=args.e1_opt_in,
        )
        payload = {
            **base_payload,
            "status": result.status,
            "execution": result.as_dict(),
            "handoff": result.handoff,
            "next": (
                f"review the handoff, then `swarm merge --run-id {run_id}` "
                "if the patch is correct"
            ),
        }
        if args.merge:
            if result.status != "HANDOFF_READY":
                payload["merge"] = {"ok": False, "error": "run is not HANDOFF_READY"}
            else:
                from v2.delivery import merge as merge_handoff

                payload["merge"] = merge_handoff(
                    root, run_id, state_root=state_root,
                    verify_timeout=args.verify_timeout,
                )
        _emit(payload, as_json=args.json)
        return (0 if result.status == "HANDOFF_READY" else 1), payload
    finally:
        lock.release()


def _build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog="swarm",
        description="Adaptive Swarm v2 one-command entrypoint",
        parents=[common],
    )
    parser.add_argument(
        "--version", action="version", version=f"swarm {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", parents=[common])
    doctor.add_argument("--no-color", action="store_true")

    planner = sub.add_parser("planner", parents=[common])
    source = planner.add_mutually_exclusive_group(required=True)
    source.add_argument("--goal")
    source.add_argument("--plan")
    planner.add_argument("--tier", choices=("E1", "E2", "E3", "E4"), default=None)
    planner.add_argument("--target", action="append", default=[])
    planner.add_argument("--verify", default=None)
    planner.add_argument("--e1-opt-in", dest="e1_opt_in", action="store_true")
    planner.add_argument("--run-id", default="plan-preview")
    planner.add_argument("--base-sha", default="HEAD")
    planner.add_argument("--out", default=None)

    run = sub.add_parser("run", parents=[common])
    run.add_argument("goal", nargs="?")
    run.add_argument("--plan", default=None, help="v1 plan or existing v2 MasterPlan")
    run.add_argument("--tier", choices=("E1", "E2", "E3", "E4"), default=None)
    run.add_argument("--target", action="append", default=[])
    run.add_argument("--verify", default=None)
    run.add_argument("--e1-opt-in", dest="e1_opt_in", action="store_true")
    run.add_argument("--preset", choices=("free-only", "strict"), default=None)
    run.add_argument("--coder-model", default=None)
    run.add_argument("--coder-kind", choices=("agentic", "opencode"), default="agentic")
    run.add_argument("--reviewer", action="append", default=[])
    run.add_argument("--free", action="store_true")
    run.add_argument("--paid-cost", type=float, default=0.0)
    run.add_argument("--timeout", type=int, default=300)
    run.add_argument("--verify-timeout", type=int, default=300)
    run.add_argument("--max-cost", type=float, default=None,
                     help="lower the plan cost envelope before Usage approval")
    run.add_argument("--max-requests", type=int, default=None,
                     help="lower the plan request envelope before Usage approval")
    run.add_argument("--max-wall-seconds", type=int, default=None,
                     help="lower the plan wall-clock envelope before Usage approval")
    run.add_argument("--run-id", default=None)
    run.add_argument("--base-sha", default="HEAD")
    run.add_argument("--approved-by", default="swarm")
    run.add_argument("--approve-usage", action="store_true")
    run.add_argument("--yes", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--plan-only", action="store_true")
    run.add_argument("--out", default=None)
    run.add_argument("--merge", action="store_true")

    status = sub.add_parser("status", parents=[common])
    status.add_argument("--run-id", default=None)
    status.add_argument("--last", action="store_true")

    handoff = sub.add_parser("handoff", parents=[common])
    handoff.add_argument("--run-id", required=True)
    handoff.add_argument("--report", default=None)

    merge = sub.add_parser("merge", parents=[common])
    merge.add_argument("--run-id", required=True)
    merge.add_argument("--message", default=None)
    merge.add_argument("--verify-timeout", type=int, default=300)
    merge.add_argument("--dry-run", action="store_true")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    seat_factory: Callable[..., Any] | None = None,
    planner_goal: Callable[..., Any] | None = None,
    planner_file: Callable[..., Any] | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(_normalize_argv(sys.argv[1:] if argv is None else argv))
    root = _resolve_root(args)
    as_json = bool(getattr(args, "json", False))

    if args.command == "doctor":
        return doctor_main(root, json_output=as_json, color=not args.no_color)

    if args.command == "planner":
        try:
            if args.goal:
                result = from_goal(
                    args.goal,
                    run_id=args.run_id,
                    base_sha=args.base_sha,
                    root=root,
                    targets=args.target,
                    verify_command=args.verify,
                    tier=args.tier,
                    low_risk_opt_in=args.e1_opt_in,
                )
            else:
                result = adapt_file(
                    args.plan,
                    run_id=args.run_id,
                    base_sha=args.base_sha,
                    root=root,
                    tier=args.tier,
                    low_risk_opt_in=args.e1_opt_in,
                    verify_command=args.verify,
                )
        except (PlanningError, PlanValidationError, ExecutionError) as exc:
            _emit({"ok": False, "status": "PLAN_INVALID", "error": str(exc)},
                  as_json=as_json)
            return 2
        out = Path(args.out or (root / ".agents" / "tmp" / "swarm_plan.json"))
        if not out.is_absolute():
            out = (root / out).resolve()
        write_plan(out, result)
        payload = {
            "ok": True,
            "status": "PLANNED",
            "tier": result.tier,
            "planner": result.planner,
            "plan_hash": result.plan_hash,
            "plan": str(out),
            "summary": result.summary,
        }
        _emit(payload, as_json=as_json)
        return 0

    if args.command in {"status", "handoff", "merge"}:
        state_root, worktree_root = _resolve_state_paths(root, args)
        runner = V2Runner(root, state_root=state_root, worktree_root=worktree_root)
        try:
            if args.command == "status":
                run_id = args.run_id or _latest_run(runner)
                payload = runner.status(run_id)
                _emit(payload, as_json=as_json)
                return 0
            if args.command == "handoff":
                payload = runner.handoff(args.run_id, report=args.report)
                _emit(payload, as_json=as_json)
                return 0
            from v2.delivery import merge as merge_handoff

            payload = merge_handoff(
                root, args.run_id, state_root=state_root,
                message=args.message, verify_timeout=args.verify_timeout,
                dry_run=args.dry_run,
            )
            _emit(payload, as_json=as_json)
            return 0
        except (V2Error, ExecutionError) as exc:
            _emit({"ok": False, "status": "FAILED", "error": str(exc)},
                  as_json=as_json)
            return 2

    try:
        code, payload = _run_task(
            args,
            seat_factory=seat_factory or build_worker_seats,
            planner_goal=planner_goal or from_goal,
            planner_file=planner_file or adapt_file,
        )
    except (PlanningError, PlanValidationError, ExecutionError, V2Error) as exc:
        _emit({"ok": False, "status": "FAILED", "error": str(exc)},
              as_json=as_json)
        return 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
