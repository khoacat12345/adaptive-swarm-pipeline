#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Named v2 execution presets (P2-09)."""

from __future__ import annotations

from typing import Any

import approval_policy
from .errors import ExecutionError

PRESETS = ("free-only", "strict")
PRESET_CONFIG: dict[str, dict[str, Any]] = {
    "free-only": {
        "free_only": True,
        "planner": "free-only",
        "seats": "free-only",
        "reviewers": 4,
        "paid_approval": "never",
    },
    "strict": {
        "free_only": False,
        "planner": "default",
        "seats": "four-reviewer",
        "reviewers": 4,
        "secret_scan": True,
        "usage_thresholds": {
            "request_threshold": 1,
            "cost_threshold_usd": 0.01,
            "ttl_seconds": approval_policy.DEFAULT_TTL_SECONDS,
        },
    },
}


def normalize(name: str | None) -> str | None:
    value = str(name or "").strip().lower()
    if not value:
        return None
    if value not in PRESET_CONFIG:
        raise ExecutionError(f"unknown preset: {name!r}; choose {', '.join(PRESETS)}")
    return value


def config_for(name: str | None) -> dict[str, Any]:
    normalized = normalize(name)
    if normalized is None:
        return {}
    return dict(PRESET_CONFIG[normalized])


def strict_thresholds(name: str | None) -> approval_policy.Thresholds | None:
    if normalize(name) != "strict":
        return None
    raw = PRESET_CONFIG["strict"]["usage_thresholds"]
    return approval_policy.Thresholds(
        request_threshold=int(raw["request_threshold"]),
        cost_threshold_usd=float(raw["cost_threshold_usd"]),
        ttl_seconds=int(raw["ttl_seconds"]),
    )


def free_only(name: str | None) -> bool:
    return normalize(name) == "free-only"
