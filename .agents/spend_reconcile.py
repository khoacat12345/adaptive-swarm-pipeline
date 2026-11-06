#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Actual-spend reconciliation (P0-02 / P0-04).

The pipeline reserves an estimate before a call. This module compares a
provider usage snapshot before and after a call with that estimate, so a paid
run cannot silently drift above its reservation.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class SpendSnapshot:
    cost_usd: float = 0.0
    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    source: str = "manual"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReconcileResult:
    estimated_cost_usd: float
    actual_cost_usd: float
    actual_requests: int
    delta_cost_usd: float
    delta_requests: int
    over_budget: bool
    source: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def from_mapping(data: Mapping[str, Any], *, source: str = "mapping") -> SpendSnapshot:
    return SpendSnapshot(
        cost_usd=float(data.get("cost_usd", data.get("cost", 0.0)) or 0.0),
        requests=int(data.get("requests", 0) or 0),
        tokens_in=int(data.get("tokens_in", 0) or 0),
        tokens_out=int(data.get("tokens_out", 0) or 0),
        source=source,
    )


def snapshot_from_quota(root: str | Path) -> SpendSnapshot:
    """Read the real local usage database through quota.QuotaTracker."""
    try:
        import quota  # type: ignore

        tracker = quota.QuotaTracker(root=root).refresh(force=True)
        spend = (tracker.spend or {}).get("calendar_today") or {}
        return SpendSnapshot(
            cost_usd=float(spend.get("cost", 0.0) or 0.0),
            requests=int(spend.get("requests", 0) or 0),
            tokens_in=int(spend.get("tokens_in", 0) or 0),
            tokens_out=int(spend.get("tokens_out", 0) or 0),
            source="opencode.db",
        )
    except Exception:  # noqa: BLE001
        return SpendSnapshot(source="unavailable")


def reconcile(
    before: SpendSnapshot,
    after: SpendSnapshot,
    *,
    estimated_cost_usd: float,
    max_cost_usd: float | None = None,
) -> ReconcileResult:
    actual_cost = max(0.0, float(after.cost_usd) - float(before.cost_usd))
    actual_requests = max(0, int(after.requests) - int(before.requests))
    delta = actual_cost - float(estimated_cost_usd)
    over = False
    if max_cost_usd is not None:
        over = actual_cost > float(max_cost_usd) + 1e-9
    else:
        over = actual_cost > float(estimated_cost_usd) + 1e-9
    return ReconcileResult(
        estimated_cost_usd=round(float(estimated_cost_usd), 8),
        actual_cost_usd=round(actual_cost, 8),
        actual_requests=actual_requests,
        delta_cost_usd=round(delta, 8),
        delta_requests=actual_requests,
        over_budget=over,
        source=after.source or before.source,
    )
