#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stable data contracts shared by the CLI, the dashboard and the engine.

The classes here are intentionally plain dataclasses with ``as_dict`` helpers.
They are the only shape a caller has to understand:

    UnifiedRequest   -> what the operator asked for
    PreflightStatus  -> whether the environment may spend a model call
    PlanDecision     -> which tier/planner/plan was selected
    RunProgress      -> what the run looks like at any point in time
    UnifiedResult    -> the final, serializable outcome

Nothing in this module writes files, calls a model or touches Git. That makes
it safe to import from a read-only dashboard and easy to freeze in tests.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

# Bump only when a field changes meaning or disappears; additive fields are
# backwards compatible for the CLI and the War Room.
CONTRACT_VERSION = "1"

COUNCIL_LEVELS = ("P1", "P2", "P3")
TIER_LEVELS = ("E1", "E2", "E3", "E4")

STATUS_PLANNED = "PLANNED"
STATUS_DOCTOR_FAILED = "DOCTOR_FAILED"
STATUS_LOCK_BUSY = "LOCK_BUSY"
STATUS_AWAITING_USAGE = "AWAITING_USAGE"
STATUS_AWAITING_SIGNOFF = "AWAITING_SIGNOFF"
STATUS_HANDOFF_READY = "HANDOFF_READY"
STATUS_FAILED = "FAILED"


class ContractError(ValueError):
    """A caller built an impossible unified request."""


def _json_safe(value: Any) -> Any:
    """Return a JSON-serializable copy without importing a JSON encoder."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return _json_safe(value.as_dict())
    return str(value)


@dataclass(frozen=True)
class UnifiedRequest:
    """One operator request, regardless of which entrypoint produced it."""

    goal: str | None = None
    plan_path: str | None = None
    order_path: str | None = None
    root: str = "."
    state_root: str | None = None
    worktree_root: str | None = None
    run_id: str | None = None
    base_sha: str = "HEAD"
    tier: str | None = None
    targets: tuple[str, ...] = ()
    verify_command: str | None = None
    e1_opt_in: bool = False
    allow_new_files: bool = False
    preset: str | None = None
    free: bool = False
    coder_model: str | None = None
    coder_kind: str = "agentic"
    reviewers: tuple[str, ...] = ()
    paid_cost: float = 0.0
    timeout: int = 300
    verify_timeout: int = 300
    max_cost: float | None = None
    max_requests: int | None = None
    max_wall_seconds: int | None = None
    approved_by: str = "swarm"
    approve_usage: bool = False
    yes: bool = False
    dry_run: bool = False
    plan_only: bool = False
    out: str | None = None
    council: bool | None = None
    planner_models: tuple[str, ...] = ()
    council_rounds: int = 1
    json_output: bool = False

    @classmethod
    def from_namespace(cls, args: Any) -> "UnifiedRequest":
        """Build a request from an argparse namespace (or any duck type)."""

        def value(name: str, default: Any) -> Any:
            return getattr(args, name, default)

        return cls(
            goal=value("goal", None),
            plan_path=value("plan", None),
            order_path=value("order", None),
            root=value("root", "."),
            state_root=value("state_root", None),
            worktree_root=value("worktree_root", None),
            run_id=value("run_id", None),
            base_sha=value("base_sha", "HEAD"),
            tier=value("tier", None),
            targets=tuple(value("target", ()) or ()),
            verify_command=value("verify", None),
            e1_opt_in=bool(value("e1_opt_in", False)),
            allow_new_files=bool(value("allow_new_files", False)),
            preset=value("preset", None),
            free=bool(value("free", False)),
            coder_model=value("coder_model", None),
            coder_kind=value("coder_kind", "agentic"),
            reviewers=tuple(value("reviewer", ()) or ()),
            paid_cost=float(value("paid_cost", 0.0) or 0.0),
            timeout=int(value("timeout", 300) or 300),
            verify_timeout=int(value("verify_timeout", 300) or 300),
            max_cost=value("max_cost", None),
            max_requests=value("max_requests", None),
            max_wall_seconds=value("max_wall_seconds", None),
            approved_by=value("approved_by", "swarm"),
            approve_usage=bool(value("approve_usage", False)),
            yes=bool(value("yes", False)),
            dry_run=bool(value("dry_run", False)),
            plan_only=bool(value("plan_only", False)),
            out=value("out", None),
            council=value("council", None),
            planner_models=tuple(value("planner_model", ()) or ()),
            council_rounds=int(value("council_rounds", 1) or 1),
            json_output=bool(value("json", False)),
        )

    def validate(self) -> "UnifiedRequest":
        sources = [
            bool(self.goal),
            bool(self.plan_path),
            bool(self.order_path),
        ]
        if sum(sources) != 1:
            raise ContractError(
                "a request needs exactly one source: goal, plan or order"
            )
        if self.tier is not None:
            tier = str(self.tier).strip().upper()
            if tier not in TIER_LEVELS:
                raise ContractError(f"unknown execution tier: {self.tier!r}")
        if self.preset not in (None, "free-only", "strict"):
            raise ContractError(f"unknown preset: {self.preset!r}")
        for name in ("max_cost", "max_requests", "max_wall_seconds"):
            value = getattr(self, name)
            if value is not None and float(value) < 0:
                raise ContractError(f"{name} cannot be negative")
        if self.timeout <= 0 or self.verify_timeout <= 0:
            raise ContractError("timeouts must be positive")
        if self.council is not None and not isinstance(self.council, bool):
            raise ContractError("council must be True, False or None (auto)")
        return self

    @property
    def source(self) -> str:
        if self.order_path:
            return "order"
        if self.plan_path:
            return "plan"
        return "goal"

    def as_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True)
class PreflightStatus:
    """The result of the environment gate that runs before any model call."""

    ok: bool
    checks: tuple[dict[str, Any], ...] = ()
    blocking: tuple[dict[str, Any], ...] = ()
    note: dict[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "checks": _json_safe(list(self.checks)),
            "blocking": _json_safe(list(self.blocking)),
            "note": _json_safe(self.note),
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class PlanDecision:
    """The plan/tier/planner decision that the Usage estimate is bound to."""

    source: str
    tier: str
    planner: str
    plan_hash: str
    summary: str = ""
    caps: dict[str, Any] = field(default_factory=dict)
    council: dict[str, Any] | None = None
    plan_path: str | None = None
    source_hash: str | None = None

    @classmethod
    def from_adapter(cls, result: Any, *, caps: Mapping[str, Any] | None = None,
                     plan_path: str | None = None) -> "PlanDecision":
        return cls(
            source=str(getattr(result, "source", "unknown")),
            tier=str(getattr(result, "tier", "E3")),
            planner=str(getattr(result, "planner", "P1")),
            plan_hash=str(getattr(result, "plan_hash", "")),
            summary=str(getattr(result, "summary", "")),
            caps=dict(caps or {}),
            council=_json_safe(getattr(result, "council", None)),
            plan_path=str(plan_path) if plan_path else None,
            source_hash=getattr(result, "source_hash", None),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "tier": self.tier,
            "planner": self.planner,
            "plan_hash": self.plan_hash,
            "summary": self.summary,
            "caps": _json_safe(self.caps),
            "council": _json_safe(self.council),
            "plan_path": self.plan_path,
            "source_hash": self.source_hash,
        }


@dataclass(frozen=True)
class RunProgress:
    """A point-in-time view of a run, safe to render in the War Room."""

    run_id: str | None = None
    stage: str = "init"
    status: str = ""
    tier: str | None = None
    planner: str | None = None
    phase: str | None = None
    worktree: str | None = None
    state_root: str | None = None
    plan_path: str | None = None
    usage: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    handoff: dict[str, Any] | None = None
    events: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "status": self.status,
            "tier": self.tier,
            "planner": self.planner,
            "phase": self.phase,
            "worktree": self.worktree,
            "state_root": self.state_root,
            "plan_path": self.plan_path,
            "usage": _json_safe(self.usage),
            "execution": _json_safe(self.execution),
            "handoff": _json_safe(self.handoff),
            "events": list(self.events),
        }


@dataclass(frozen=True)
class UnifiedResult:
    """Final outcome of one orchestrated request.

    ``merge`` is deliberately absent: merging is an explicit operator action in
    the CLI/dashboard and can never be performed by the orchestrator.
    """

    ok: bool
    status: str
    exit_code: int = 0
    run_id: str | None = None
    tier: str | None = None
    planner: str | None = None
    plan_hash: str | None = None
    worktree: str | None = None
    state_root: str | None = None
    plan_path: str | None = None
    summary: str | None = None
    error: str | None = None
    next_action: str | None = None
    doctor: dict[str, Any] | None = None
    preflight: dict[str, Any] | None = None
    plan_decision: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    caps: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] | None = None
    handoff: dict[str, Any] | None = None
    progress: dict[str, Any] | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "status": self.status,
            "run_id": self.run_id,
            "tier": self.tier,
            "planner": self.planner,
            "plan_hash": self.plan_hash,
            "worktree": self.worktree,
            "state_root": self.state_root,
            "plan": self.plan_path,
            "summary": self.summary,
            "error": self.error,
            "next": self.next_action,
            "doctor": _json_safe(self.doctor),
            "preflight": _json_safe(self.preflight),
            "plan_decision": _json_safe(self.plan_decision),
            "usage": _json_safe(self.usage),
            "caps": _json_safe(self.caps),
            "execution": _json_safe(self.execution),
            "handoff": _json_safe(self.handoff),
            "progress": _json_safe(self.progress),
            "artifacts": _json_safe(self.artifacts),
        }
