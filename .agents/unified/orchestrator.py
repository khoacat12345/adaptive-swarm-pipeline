#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified orchestrator: preflight -> plan -> Usage -> execute -> handoff.

This module is the single implementation of the run pipeline. The terminal CLI
is only a parser/pretty-printer around it, so the War Room and future clients can
call the same code without copying state-machine rules.

Hard rules enforced here:

* preflight runs before planning and before any model seat is built;
* the Usage decision is always evaluated and a required approval stops the run
  before signoff/execution;
* verification, checkpointing and handoff remain inside the v2 engine;
* the orchestrator never merges. ``merge`` is an explicit operator action in the
  CLI/dashboard.
"""

from __future__ import annotations

import inspect
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unified.contracts import (  # noqa: E402
    ContractError,
    PlanDecision,
    PreflightStatus,
    RunProgress,
    STATUS_AWAITING_SIGNOFF,
    STATUS_AWAITING_USAGE,
    STATUS_DOCTOR_FAILED,
    STATUS_FAILED,
    STATUS_HANDOFF_READY,
    STATUS_LOCK_BUSY,
    STATUS_PLANNED,
    UnifiedRequest,
    UnifiedResult,
)
from v2.adapters import FREE_MODELS, build_worker_seats, free_seats_from_config  # noqa: E402
from v2.doctor import collect as doctor_collect  # noqa: E402
from v2.errors import ExecutionError, V2Error  # noqa: E402
from v2.execution import ExecutionEngine, resolve_tier_profile  # noqa: E402
from v2.models import is_free, load_config  # noqa: E402
from v2.plan import (  # noqa: E402
    PlanValidationError,
    plan_hash,
    validate_plan,
)
from v2.planner_adapter import (  # noqa: E402
    PlanAdapterResult,
    PlanningError,
    adapt_file,
    from_goal,
    from_order,
    write_plan,
)
from v2.presets import free_only as preset_free_only  # noqa: E402
from v2.runner import V2Runner  # noqa: E402

REVIEW_ROLES = ("security", "qa", "perf", "docs")


def _row_field(row: Any, name: str, default: Any = "") -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _row_dict(row: Any) -> dict[str, Any]:
    return {
        "name": str(_row_field(row, "name", "")),
        "status": str(_row_field(row, "status", "")),
        "detail": str(_row_field(row, "detail", "")),
    }


def _head_sha(root: Path) -> str:
    try:
        from v2.isolation import git_root, resolve_base_sha

        return resolve_base_sha(git_root(root), "HEAD")
    except Exception:
        return "HEAD"


def _resolve_optional(root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def _scale_requests(values: list[int], cap: int) -> list[int]:
    """Shrink integer allocations to fit ``cap`` while keeping each one >= 1."""
    total = sum(values)
    scaled = [max(1, int(value * cap / total)) for value in values]
    while sum(scaled) > cap:
        index = max(range(len(scaled)), key=lambda item: scaled[item])
        scaled[index] -= 1
    return scaled


def cap_plan_budget(
    plan: Mapping[str, Any],
    *,
    max_cost: float | None,
    max_requests: int | None,
    max_wall_seconds: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Lower a plan budget to caller caps before the Usage decision."""
    if max_cost is None and max_requests is None and max_wall_seconds is None:
        return dict(plan), {}
    updated = json.loads(json.dumps(plan))
    budget = updated.get("budget")
    if not isinstance(budget, Mapping):
        raise V2Error("plan has no budget to cap")
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
                raise V2Error(
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


def with_capped_plan(
    result: PlanAdapterResult,
    *,
    max_cost: float | None,
    max_requests: int | None,
    max_wall_seconds: int | None,
) -> tuple[PlanAdapterResult, dict[str, Any]]:
    plan, applied = cap_plan_budget(
        result.plan,
        max_cost=max_cost,
        max_requests=max_requests,
        max_wall_seconds=max_wall_seconds,
    )
    if not applied:
        return result, {}
    from dataclasses import replace

    return replace(result, plan=plan, plan_hash=plan_hash(plan)), applied


def _accepts(function: Callable[..., Any], name: str) -> bool:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return True
    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return True
    return name in signature.parameters


def _filtered_kwargs(function: Callable[..., Any], **kwargs: Any) -> dict[str, Any]:
    return {key: value for key, value in kwargs.items() if _accepts(function, key)}


def parse_reviewers(items: Sequence[str]) -> dict[str, str]:
    models: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise V2Error(f"bad --reviewer value: {item!r}; use role=model")
        role, model = item.split("=", 1)
        role = role.strip()
        model = model.strip()
        if not role or not model:
            raise V2Error(f"bad --reviewer value: {item!r}; use role=model")
        models[role] = model
    return models


def select_seats(
    request: UnifiedRequest,
    *,
    profile: Any,
    config: Mapping[str, Any] | None,
) -> tuple[str, dict[str, str], bool]:
    """Resolve coder/reviewer models for the signed tier profile."""
    explicit = parse_reviewers(request.reviewers)
    use_free = bool(
        request.free
        or preset_free_only(request.preset)
        or not request.coder_model
    )
    if preset_free_only(request.preset) and request.coder_model:
        if not is_free(request.coder_model, config or {}):
            raise V2Error(
                "free-only preset blocks paid coder: " + request.coder_model
            )
    coder_model = request.coder_model
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
        raise V2Error("no coder model available; pass --coder-model or --free")
    missing = [role for role in profile.reviewer_roles if not reviewer_models.get(role)]
    if missing:
        raise V2Error(
            f"{profile.tier} requires reviewer model(s): {', '.join(missing)}"
        )
    if preset_free_only(request.preset):
        paid = [
            model for model in [coder_model, *reviewer_models.values()]
            if not (
                is_free(model, config or {})
                if config is not None
                else str(model).lower().endswith("-free")
            )
        ]
        if paid:
            raise V2Error(
                "free-only preset blocks paid models: " + ", ".join(paid)
            )
    return coder_model, reviewer_models, use_free


class UnifiedOrchestrator:
    """Dependency-injectable implementation of the unified run pipeline."""

    def __init__(
        self,
        *,
        seat_factory: Callable[..., Any] | None = None,
        planner_goal: Callable[..., Any] | None = None,
        planner_file: Callable[..., Any] | None = None,
        planner_order: Callable[..., Any] | None = None,
        planner_call: Callable[[str, str | None, str], Any] | None = None,
        doctor: Callable[[Path], Sequence[Any]] | None = None,
        runner_factory: Callable[..., Any] | None = None,
        lock_factory: Callable[[Path], Any] | None = None,
        engine_factory: Callable[[Any], Any] | None = None,
        config_loader: Callable[[Path], Any] | None = None,
    ):
        self.seat_factory = seat_factory or build_worker_seats
        self.planner_goal = planner_goal or from_goal
        self.planner_file = planner_file or adapt_file
        self.planner_order = planner_order or from_order
        self.planner_call = planner_call
        self.doctor = doctor or doctor_collect
        self.runner_factory = runner_factory
        self.lock_factory = lock_factory
        self.engine_factory = engine_factory or (lambda runner: ExecutionEngine(runner))
        self.config_loader = config_loader or load_config

    # -- preflight -----------------------------------------------------
    def preflight(self, request: UnifiedRequest, root: Path) -> PreflightStatus:
        rows = list(self.doctor(root))
        checks = tuple(_row_dict(row) for row in rows)
        bad = [row for row in rows if _row_field(row, "status") == "bad"]
        blocking = [
            row for row in bad
            if _row_field(row, "name") != "models"
            and not str(_row_field(row, "name")).startswith("opencode/")
        ]
        note = {
            "bad": len(bad),
            "warn": sum(1 for row in rows if _row_field(row, "status") == "warn"),
            "fallback_models": any(_row_field(row, "name") == "models" for row in bad),
        }
        errors = tuple(
            f"{_row_field(row, 'name')}: {_row_field(row, 'detail')}"
            for row in blocking
        )
        return PreflightStatus(
            ok=not blocking,
            checks=checks,
            blocking=tuple(_row_dict(row) for row in blocking),
            note=note,
            errors=errors,
        )

    # -- planning ------------------------------------------------------
    def _model_planner_call(
        self,
        models: Sequence[str],
        *,
        root: Path,
    ) -> Callable[[str, str | None, str], Any] | None:
        """Build a real model-backed council callable from ``--planner-model``.

        The deterministic council remains the default. This path is only used
        when the operator explicitly names seat models; every returned payload
        must still pass plan validation.
        """
        seat_models = [str(model) for model in models if str(model).strip()]
        if not seat_models:
            return None
        backends: dict[str, Any] = {}

        def call(role: str, model: str | None, prompt: str) -> Any:
            if not model:
                raise PlanningError(
                    f"{role} council seat has no model; pass --planner-model"
                )
            backend = backends.get(model)
            if backend is None:
                from v2.planning import OpenCodePlanner

                backend = OpenCodePlanner(model, cwd=str(root))
                backends[model] = backend
            return backend.complete(role, prompt)

        return call

    def _plan(
        self,
        request: UnifiedRequest,
        *,
        root: Path,
        run_id: str,
        base_sha: str,
    ) -> PlanAdapterResult:
        planner_call = self.planner_call or self._model_planner_call(
            request.planner_models, root=root,
        )
        common = {
            "run_id": run_id,
            "base_sha": base_sha,
            "root": root,
            "tier": request.tier,
            "low_risk_opt_in": request.e1_opt_in,
            "verify_command": request.verify_command,
            "council": request.council,
            "planner_models": request.planner_models,
            "planner_call": planner_call,
            "council_rounds": request.council_rounds,
        }
        if request.order_path:
            # v1 orders may create a new file (for example a new module); the
            # order was already validated by the v1 Work Order schema.
            return self.planner_order(
                self._load_json_path(Path(request.order_path), root),
                **_filtered_kwargs(
                    self.planner_order,
                    **{**common, "allow_new_files": True},
                ),
            )
        if request.plan_path:
            path = Path(request.plan_path)
            if not path.is_absolute():
                path = (root / path).resolve()
            return self.planner_file(
                path,
                **_filtered_kwargs(
                    self.planner_file,
                    **{**common, "allow_new_files": request.allow_new_files},
                ),
            )
        return self.planner_goal(
            str(request.goal or ""),
            **_filtered_kwargs(
                self.planner_goal,
                **{
                    **common,
                    "targets": list(request.targets),
                    "allow_new_files": request.allow_new_files,
                },
            ),
        )

    @staticmethod
    def _load_json_path(path: Path, root: Path) -> dict[str, Any]:
        target = path if path.is_absolute() else (root / path).resolve()
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PlanningError(f"cannot read order: {exc}") from exc
        if not isinstance(payload, dict):
            raise PlanningError("order root must be an object")
        return payload

    # -- runner / lock -------------------------------------------------
    def _runner(self, root: Path, request: UnifiedRequest) -> Any:
        state_root = _resolve_optional(root, request.state_root)
        worktree_root = _resolve_optional(root, request.worktree_root)
        if self.runner_factory is not None:
            return self.runner_factory(
                root,
                state_root=state_root,
                worktree_root=worktree_root,
            )
        return V2Runner(
            root,
            state_root=state_root,
            worktree_root=worktree_root,
            require_clean=True,
        )

    def _lock(self, root: Path) -> Any:
        if self.lock_factory is not None:
            return self.lock_factory(root)
        from run_lock import RepoLock

        return RepoLock(root, owner="swarm").acquire()

    # -- result helpers ------------------------------------------------
    def _failure(
        self,
        status: str,
        error: str,
        *,
        exit_code: int = 2,
        preflight: PreflightStatus | None = None,
        doctor: Mapping[str, Any] | None = None,
        decision: PlanDecision | None = None,
        caps: Mapping[str, Any] | None = None,
        run_id: str | None = None,
        tier: str | None = None,
        planner: str | None = None,
        plan_hash_value: str | None = None,
        plan_path: str | None = None,
        stage: str = "failed",
    ) -> UnifiedResult:
        return UnifiedResult(
            ok=False,
            status=status,
            exit_code=exit_code,
            run_id=run_id,
            tier=tier,
            planner=planner,
            plan_hash=plan_hash_value,
            plan_path=plan_path,
            error=error,
            doctor=dict(doctor or {}),
            preflight=preflight.as_dict() if preflight else None,
            plan_decision=decision.as_dict() if decision else None,
            caps=dict(caps or {}),
            progress=RunProgress(
                run_id=run_id, stage=stage, status=status,
            ).as_dict(),
        )

    # -- main pipeline -------------------------------------------------
    def run(self, request: UnifiedRequest) -> UnifiedResult:
        # Stage order is part of the contract: preflight -> plan -> prepare ->
        # Usage -> signoff/execute -> handoff. Reordering this without updating
        # test_unified_orchestrator.py would break the C1 invariants.
        request.validate()
        root = Path(request.root).expanduser().resolve()

        # 1. Preflight is acquired before planning and before any model call.
        preflight = self.preflight(request, root)
        if not preflight.ok:
            return self._failure(
                STATUS_DOCTOR_FAILED,
                "preflight failed: " + "; ".join(preflight.errors),
                preflight=preflight,
                doctor=preflight.note,
                stage="preflight",
            )

        caps: dict[str, Any] = {}
        provisional: PlanAdapterResult | None = None
        try:
            provisional = self._plan(
                request,
                root=root,
                run_id=request.run_id or "plan-preview",
                base_sha=_head_sha(root),
            )
            provisional, caps = with_capped_plan(
                provisional,
                max_cost=request.max_cost,
                max_requests=request.max_requests,
                max_wall_seconds=request.max_wall_seconds,
            )
        except (PlanningError, PlanValidationError, V2Error) as exc:
            return self._failure(
                STATUS_FAILED,
                str(exc),
                preflight=preflight,
                doctor=preflight.note,
                caps=caps,
                stage="planning",
            )

        plan_only_path: Path | None = None
        if request.plan_only:
            plan_only_path = Path(
                request.out or (root / ".agents" / "tmp" / "swarm_plan.json")
            )
            if not plan_only_path.is_absolute():
                plan_only_path = (root / plan_only_path).resolve()
            write_plan(plan_only_path, provisional)
            decision = PlanDecision.from_adapter(
                provisional, caps=caps, plan_path=str(plan_only_path),
            )
            return UnifiedResult(
                ok=True,
                status=STATUS_PLANNED,
                exit_code=0,
                tier=provisional.tier,
                planner=provisional.planner,
                plan_hash=provisional.plan_hash,
                plan_path=str(plan_only_path),
                summary=provisional.summary,
                doctor=preflight.note,
                preflight=preflight.as_dict(),
                plan_decision=decision.as_dict(),
                caps=caps,
                artifacts={"plan": str(plan_only_path)},
                progress=RunProgress(
                    stage="planned",
                    status=STATUS_PLANNED,
                    tier=provisional.tier,
                    planner=provisional.planner,
                    plan_path=str(plan_only_path),
                ).as_dict(),
            )

        # 2. Create the guarded run, then plan against the real run/base.
        runner = self._runner(root, request)
        try:
            lock = self._lock(root)
        except Exception as exc:
            from run_lock import LockBusy

            if isinstance(exc, LockBusy):
                return self._failure(
                    STATUS_LOCK_BUSY,
                    str(exc),
                    preflight=preflight,
                    doctor=preflight.note,
                    caps=caps,
                    stage="lock",
                )
            raise

        try:
            try:
                prepared = runner.prepare(
                    provisional.plan.get("goal")
                    or request.goal
                    or "swarm task",
                    base_sha=request.base_sha,
                    run_id=request.run_id,
                    phase_ids=[
                        str(phase["id"]) for phase in provisional.plan["phases"]
                    ],
                    preset=request.preset,
                )
                run_id = str(prepared["run_id"])
                workspace = prepared["workspace"]
                plan_result = self._plan(
                    request,
                    root=root,
                    run_id=run_id,
                    base_sha=str(workspace["base_sha"]),
                )
                plan_result, caps = with_capped_plan(
                    plan_result,
                    max_cost=request.max_cost,
                    max_requests=request.max_requests,
                    max_wall_seconds=request.max_wall_seconds,
                )
                plan = dict(plan_result.plan)
                if str(plan.get("schema_version") or "") == "2.1" and plan.get("phases"):
                    plan["run_id"] = run_id
                    plan["base_sha"] = str(workspace["base_sha"])
                validate_plan(plan, worktree=workspace["worktree_path"])
                run_dir = runner.load(run_id).run_dir
                plan_path = run_dir / "proposed_plan.json"
                write_plan(plan_path, plan_result)
                submitted = runner.submit_plan(run_id, plan)
                usage_decision = runner.usage_decision(run_id)
                usage = usage_decision.as_dict()
                decision = PlanDecision(
                    source=plan_result.source,
                    tier=str(
                        plan.get("execution_tier")
                        or plan.get("tier")
                        or plan_result.tier
                    ),
                    planner=str(
                        plan.get("planner_level") or plan_result.planner
                    ),
                    plan_hash=str(submitted["plan_hash"]),
                    summary=plan_result.summary,
                    caps=caps,
                    council=plan_result.council,
                    plan_path=str(plan_path),
                    source_hash=plan_result.source_hash,
                )
                base = dict(
                    run_id=run_id,
                    tier=decision.tier,
                    planner=decision.planner,
                    plan_hash=decision.plan_hash,
                    worktree=workspace.get("worktree_path"),
                    state_root=str(run_dir),
                    plan_path=str(plan_path),
                    summary=plan_result.summary,
                    doctor=preflight.note,
                    preflight=preflight.as_dict(),
                    plan_decision=decision.as_dict(),
                    usage=usage,
                    caps=caps,
                    artifacts={
                        "plan": str(plan_path),
                        "state_root": str(run_dir),
                        "worktree": workspace.get("worktree_path"),
                    },
                )
                waiting = RunProgress(
                    run_id=run_id,
                    stage="usage",
                    status=submitted["status"],
                    tier=decision.tier,
                    planner=decision.planner,
                    worktree=workspace.get("worktree_path"),
                    state_root=str(run_dir),
                    plan_path=str(plan_path),
                    usage=usage,
                    events=("run.prepared", "plan.submitted"),
                ).as_dict()
                if usage_decision.required and not (
                    request.approve_usage or request.yes
                ):
                    return UnifiedResult(
                        ok=True,
                        status=STATUS_AWAITING_USAGE,
                        exit_code=3,
                        next_action=(
                            "rerun with --approve-usage after reviewing the "
                            "estimate"
                        ),
                        progress=waiting,
                        **base,
                    )
                if request.dry_run:
                    return UnifiedResult(
                        ok=True,
                        status=submitted["status"],
                        exit_code=0,
                        next_action="rerun without --dry-run to sign off and execute",
                        progress=waiting,
                        **base,
                    )

                # 3. The Usage gate is satisfied (or was not required); sign the
                # exact plan before any seat can call a model.
                runner.signoff(run_id, approved_by=request.approved_by)
                profile = resolve_tier_profile(
                    plan,
                    tier=request.tier,
                    low_risk_opt_in=request.e1_opt_in,
                )
                try:
                    config = self.config_loader(root)
                except Exception:
                    config = None
                coder_model, reviewer_models, use_free = select_seats(
                    request, profile=profile, config=config,
                )
                coder, reviewers = self.seat_factory(
                    coder_model=coder_model,
                    reviewer_models=reviewer_models,
                    sandbox_root=run_dir / "coder_sandbox",
                    coder_kind=request.coder_kind,
                    timeout=request.timeout,
                    config=config,
                    paid_cost=request.paid_cost,
                    free_only=bool(
                        use_free or preset_free_only(request.preset)
                    ),
                )
                result = self.engine_factory(runner).execute(
                    run_id,
                    coder=coder,
                    reviewers=reviewers,
                    tier=profile.tier,
                    low_risk_opt_in=request.e1_opt_in,
                )
                ok = result.status == STATUS_HANDOFF_READY
                progress = RunProgress(
                    run_id=run_id,
                    stage="handoff" if ok else "execute",
                    status=result.status,
                    tier=decision.tier,
                    planner=decision.planner,
                    worktree=workspace.get("worktree_path"),
                    state_root=str(run_dir),
                    plan_path=str(plan_path),
                    usage=usage,
                    execution=result.as_dict(),
                    handoff=result.handoff,
                    events=("run.prepared", "plan.submitted", "signoff.approved",
                            "tier.selected", f"execution.{result.status}"),
                ).as_dict()
                return UnifiedResult(
                    ok=ok,
                    status=result.status,
                    exit_code=0 if ok else 1,
                    next_action=(
                        f"review the handoff, then `swarm merge --run-id {run_id}` "
                        "if the patch is correct"
                    ),
                    execution=result.as_dict(),
                    handoff=result.handoff,
                    progress=progress,
                    **base,
                )
            except (PlanningError, PlanValidationError, ExecutionError, V2Error) as exc:
                return self._failure(
                    STATUS_FAILED,
                    str(exc),
                    preflight=preflight,
                    doctor=preflight.note,
                    caps=caps,
                    stage="execute",
                )
        finally:
            try:
                lock.release()
            except Exception:
                pass


__all__ = [
    "ContractError",
    "UnifiedOrchestrator",
    "cap_plan_budget",
    "with_capped_plan",
    "parse_reviewers",
    "select_seats",
]
