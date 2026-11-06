#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0-06: conditional Usage approval.

Plan approval is no longer the default human gate.  A plan may start
automatically; only an expensive/long run needs a human spend approval.

This module is intentionally dependency-free so v1 and v2 can share it.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

DEFAULT_REQUEST_THRESHOLD = 8
DEFAULT_COST_THRESHOLD_USD = 0.50
DEFAULT_TTL_SECONDS = 2 * 60 * 60
APPROVAL_FILENAME = "usage_approval.json"

# Conservative estimate only; real billing is read from OpenCode stats.
_COST_PER_CALL = {
    "free": 0.0,
    "cheap": 0.02,
    "standard": 0.10,
    "premium": 0.50,
    "unknown": 0.50,
}


class ApprovalError(RuntimeError):
    """Base class for approval failures."""


class ApprovalRequired(ApprovalError):
    """The run needs a Usage approval that does not exist yet."""


class ApprovalInvalid(ApprovalError):
    """The approval exists but does not match the current plan/estimate."""


class ApprovalExpired(ApprovalInvalid):
    """The approval is older than its TTL."""


@dataclass(frozen=True)
class Thresholds:
    request_threshold: int = DEFAULT_REQUEST_THRESHOLD
    cost_threshold_usd: float = DEFAULT_COST_THRESHOLD_USD
    ttl_seconds: int = DEFAULT_TTL_SECONDS

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_threshold": self.request_threshold,
            "cost_threshold_usd": self.cost_threshold_usd,
            "ttl_seconds": self.ttl_seconds,
        }


@dataclass(frozen=True)
class UsageEstimate:
    requests: int
    cost_usd: float | None
    wall_seconds: int
    confidence: str
    agents: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "cost_usd": self.cost_usd,
            "wall_seconds": self.wall_seconds,
            "confidence": self.confidence,
            "agents": list(self.agents),
            "models": list(self.models),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class UsageDecision:
    required: bool
    reasons: tuple[str, ...]
    estimate: UsageEstimate
    thresholds: Thresholds
    policy_hash: str
    plan_hash: str
    estimate_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "reasons": list(self.reasons),
            "estimate": self.estimate.as_dict(),
            "thresholds": self.thresholds.as_dict(),
            "policy_hash": self.policy_hash,
            "plan_hash": self.plan_hash,
            "estimate_hash": self.estimate_hash,
        }


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=_json_default)


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _as_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def load_thresholds(root: str | Path | None = None) -> Thresholds:
    """Load project thresholds, then allow environment overrides.

    A missing/invalid file must never silently disable the gate.
    """
    data: dict[str, Any] = {}
    if root is not None:
        path = Path(root).resolve() / ".agents" / "approval.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, json.JSONDecodeError):
            data = {}
    request_threshold = _as_int(
        os.environ.get(
            "SWARM_USAGE_REQUEST_THRESHOLD",
            data.get("request_threshold", DEFAULT_REQUEST_THRESHOLD),
        ),
        DEFAULT_REQUEST_THRESHOLD,
    )
    cost_threshold = _as_float(
        os.environ.get(
            "SWARM_USAGE_COST_THRESHOLD_USD",
            data.get("cost_threshold_usd", DEFAULT_COST_THRESHOLD_USD),
        ),
        DEFAULT_COST_THRESHOLD_USD,
    )
    ttl_seconds = _as_int(
        os.environ.get(
            "SWARM_USAGE_APPROVAL_TTL",
            data.get("ttl_seconds", DEFAULT_TTL_SECONDS),
        ),
        DEFAULT_TTL_SECONDS,
    )
    return Thresholds(request_threshold, cost_threshold, ttl_seconds)


def load_catalog(root: str | Path | None = None) -> dict[str, Any]:
    if root is None:
        return {}
    path = Path(root).resolve() / ".agents" / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def model_meta(catalog: Mapping[str, Any] | None, model: str) -> dict[str, Any]:
    models = (catalog or {}).get("models") or {}
    entry = models.get(str(model))
    return entry if isinstance(entry, dict) else {}


def plan_hash(plan: Mapping[str, Any]) -> str:
    body = {key: value for key, value in (plan or {}).items()
            if not str(key).startswith("_")}
    return sha256_json(body)


def _reviewer_count(mode: str, reviewer: str, *, max_rounds: int) -> int:
    reviewer = str(reviewer or "heuristic").lower()
    mode = str(mode or "panel").lower()
    if reviewer == "wave":
        return 4 * max_rounds
    if reviewer == "meeting" or mode == "meeting":
        # Round 1 has four reviewers; allow one targeted rebuttal with two.
        return (4 * max_rounds) + (2 if max_rounds > 0 else 0)
    if reviewer == "llm":
        return max_rounds
    return 0


def _subtask_count(plan: Mapping[str, Any]) -> int:
    subtasks = (plan or {}).get("subtasks")
    if isinstance(subtasks, list) and subtasks:
        return len(subtasks)
    phases = (plan or {}).get("phases")
    if isinstance(phases, list) and phases:
        return max(1, len(phases))
    return 1


def _models_from_plan(plan: Mapping[str, Any]) -> list[str]:
    found: list[str] = []
    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip() and value not in found:
            found.append(value.strip())
        elif isinstance(value, (list, tuple)):
            for item in value:
                add(item)
    for key in ("models", "model", "coder_model", "reviewer_models"):
        add((plan or {}).get(key))
    for subtask in (plan or {}).get("subtasks") or []:
        if isinstance(subtask, dict):
            add(subtask.get("models"))
            add(subtask.get("model"))
            add(subtask.get("coder_model"))
    return found


def estimate_usage(
    plan: Mapping[str, Any],
    *,
    mode: str = "panel",
    reviewer: str = "heuristic",
    models: Iterable[str] = (),
    catalog: Mapping[str, Any] | None = None,
    multi_phase: bool = False,
    overnight: bool = False,
) -> UsageEstimate:
    """Build a conservative pre-run estimate.  It is not billing actuals."""
    count = _subtask_count(plan)
    max_rounds = 1
    for subtask in (plan or {}).get("subtasks") or []:
        if isinstance(subtask, dict):
            try:
                max_rounds = max(max_rounds, int(subtask.get("max_rounds", 1) or 1))
            except (TypeError, ValueError):
                continue
    if multi_phase:
        count = max(count, 2)

    reviewer_requests = _reviewer_count(mode, reviewer, max_rounds=max_rounds)
    requests = max(1, count * (max_rounds + reviewer_requests))
    if str(reviewer or "").lower() == "meeting" or str(mode or "").lower() == "meeting":
        requests += count  # one planner/coordination call per task at minimum

    names = list(dict.fromkeys(
        [str(item) for item in models if str(item).strip()] + _models_from_plan(plan)
    ))
    if not names:
        # The caller did not provide a route yet.  Do not force approval for
        # every quick task; the request threshold and explicit mode/reviewer
        # flags still protect the expensive paths.
        cost = 0.0
        confidence = "low"
        reasons = ["model list unavailable; cost assumed 0"]
    else:
        cost = 0.0
        unknown = False
        for model in names:
            entry = model_meta(catalog, model)
            tier = str(entry.get("cost_tier") or "unknown").lower()
            is_free = bool(entry.get("is_free")) or tier == "free"
            per_call = 0.0 if is_free else _COST_PER_CALL.get(tier)
            if per_call is None:
                unknown = True
                continue
            cost += per_call * requests
        if unknown:
            cost = None
            confidence = "unknown"
            reasons = ["pricing unavailable for one or more models"]
        else:
            confidence = "high" if all(
                bool(model_meta(catalog, model).get("is_free"))
                or str(model_meta(catalog, model).get("cost_tier") or "").lower() == "free"
                for model in names
            ) else "low"
            reasons = []

    wall_seconds = max(60, requests * 45)
    if overnight:
        wall_seconds = max(wall_seconds, 8 * 60 * 60)
    if multi_phase:
        wall_seconds = max(wall_seconds, 15 * 60)

    return UsageEstimate(
        requests=requests,
        cost_usd=None if cost is None else round(float(cost), 4),
        wall_seconds=int(wall_seconds),
        confidence=confidence,
        agents=tuple(("coder", "reviewer") if reviewer_requests else ("coder",)),
        models=tuple(names),
        reasons=tuple(reasons),
    )


def evaluate(
    plan: Mapping[str, Any],
    *,
    mode: str = "panel",
    reviewer: str = "heuristic",
    models: Iterable[str] = (),
    root: str | Path | None = None,
    catalog: Mapping[str, Any] | None = None,
    multi_phase: bool = False,
    overnight: bool = False,
    thresholds: Thresholds | None = None,
) -> UsageDecision:
    thresholds = thresholds or load_thresholds(root)
    catalog = catalog if catalog is not None else load_catalog(root)
    estimate = estimate_usage(
        plan,
        mode=mode,
        reviewer=reviewer,
        models=models,
        catalog=catalog,
        multi_phase=multi_phase,
        overnight=overnight,
    )
    reasons: list[str] = []
    if str(mode or "").lower() == "meeting":
        reasons.append("mode=meeting")
    if str(reviewer or "").lower() in {"wave", "meeting"}:
        reasons.append(f"reviewer={reviewer}")
    if multi_phase:
        reasons.append("multi_phase")
    if overnight:
        reasons.append("overnight")
    if estimate.confidence == "unknown":
        reasons.append("estimate_unknown")
    if estimate.cost_usd is None:
        reasons.append("cost_unknown")
    elif estimate.cost_usd >= thresholds.cost_threshold_usd:
        reasons.append(
            f"estimated_cost>=${thresholds.cost_threshold_usd:.2f}"
        )
    if estimate.requests >= thresholds.request_threshold:
        reasons.append(
            f"estimated_requests>={thresholds.request_threshold}"
        )
    # A paid model always needs a spend decision, even below the threshold.
    names = list(estimate.models)
    if names and any(
        not (bool(model_meta(catalog, model).get("is_free"))
              or str(model_meta(catalog, model).get("cost_tier") or "").lower() == "free")
        for model in names
    ):
        reasons.append("paid_model")

    required = bool(reasons)
    policy_hash = sha256_json({
        "plan_hash": plan_hash(plan),
        "thresholds": thresholds.as_dict(),
        "mode": str(mode or "panel"),
        "reviewer": str(reviewer or "heuristic"),
        "estimate": estimate.as_dict(),
    })
    estimate_hash = sha256_json({
        "plan_hash": plan_hash(plan),
        "estimate": estimate.as_dict(),
    })
    return UsageDecision(
        required=required,
        reasons=tuple(dict.fromkeys(reasons)),
        estimate=estimate,
        thresholds=thresholds,
        policy_hash=policy_hash,
        plan_hash=plan_hash(plan),
        estimate_hash=estimate_hash,
    )


def approval_path(root: str | Path, *, run_id: str | None = None) -> Path:
    base = Path(root).resolve() / ".agents" / "tmp"
    if run_id:
        return base / "v2" / "runs" / str(run_id) / APPROVAL_FILENAME
    return base / APPROVAL_FILENAME


def create_approval(
    path: str | Path,
    decision: UsageDecision,
    *,
    approved_by: str = "local-user",
    ttl_seconds: int | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = time.time() if now is None else float(now)
    ttl = int(decision.thresholds.ttl_seconds if ttl_seconds is None else ttl_seconds)
    record = {
        "schema_version": 1,
        "plan_hash": decision.plan_hash,
        "estimate_hash": decision.estimate_hash,
        "policy_hash": decision.policy_hash,
        "approved_by": str(approved_by or "local-user"),
        "approved_at": created,
        "expires_at": created + max(1, ttl),
        "reasons": list(decision.reasons),
        "estimate": decision.estimate.as_dict(),
        "thresholds": decision.thresholds.as_dict(),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return record


def read_approval(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ApprovalInvalid(f"cannot read usage approval: {exc}") from exc
    return data if isinstance(data, dict) else {}


def assert_approval(
    path: str | Path,
    decision: UsageDecision,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Fail closed when an approval is missing, stale or bound to another plan."""
    record = read_approval(path)
    if not record:
        raise ApprovalRequired(
            "usage approval required: " + ", ".join(decision.reasons)
        )
    if record.get("plan_hash") != decision.plan_hash:
        raise ApprovalInvalid("usage approval does not match the current plan")
    if record.get("estimate_hash") != decision.estimate_hash:
        raise ApprovalInvalid("usage approval does not match the current estimate")
    expires = float(record.get("expires_at") or 0)
    if expires and (time.time() if now is None else float(now)) > expires:
        raise ApprovalExpired("usage approval has expired")
    return record


def approval_status(
    path: str | Path,
    decision: UsageDecision,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    try:
        assert_approval(path, decision, now=now)
    except ApprovalRequired:
        return {"status": "awaiting", "required": True,
                "reasons": list(decision.reasons)}
    except ApprovalExpired as exc:
        return {"status": "expired", "required": True, "error": str(exc)}
    except ApprovalInvalid as exc:
        return {"status": "invalid", "required": True, "error": str(exc)}
    return {"status": "approved", "required": True, "decision": decision.as_dict()}
