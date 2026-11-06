#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0-07 four-tier execution and three-level planner policy.

This module is the single deterministic mapping used by v1 and v2:

    E1 coder only
    E2 coder + one reviewer
    E3 coder + additional specialist reviewer(s)
    E4 meeting (full review protocol)

    P1 one planner
    P2 two planners (proposal + critique)
    P3 three planners (council)

The existing classifier Level 0-3 is task scope, not an execution tier.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable, Mapping


TIER_ORDER = ("E1", "E2", "E3", "E4")
PLANNER_ORDER = ("P1", "P2", "P3")


@dataclass(frozen=True)
class TierSpec:
    tier: str
    label: str
    reviewer_count: int
    planner_level: str
    needs_wave: bool
    needs_meeting: bool
    description: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


TIERS: dict[str, TierSpec] = {
    "E1": TierSpec(
        "E1", "coder only", 0, "P1", False, False,
        "one coder; no independent reviewer",
    ),
    "E2": TierSpec(
        "E2", "coder + one reviewer", 1, "P1", False, False,
        "standard quick path",
    ),
    "E3": TierSpec(
        "E3", "coder + specialist reviewers", 4, "P2", True, False,
        "wave/panel with additional reviewer roles",
    ),
    "E4": TierSpec(
        "E4", "meeting", 4, "P3", True, True,
        "round-based meeting with full council",
    ),
}


@dataclass(frozen=True)
class TierDecision:
    tier: str
    planner: str
    reviewer_count: int
    reasons: tuple[str, ...]
    spec: TierSpec

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "planner": self.planner,
            "reviewer_count": self.reviewer_count,
            "reasons": list(self.reasons),
            "spec": self.spec.as_dict(),
        }


def select_tier(
    *,
    mode: str = "panel",
    reviewer: str = "heuristic",
    reviewer_kind: str | None = None,
    risk: bool = False,
    multi_phase: bool = False,
) -> TierDecision:
    """Choose an execution tier deterministically.

    Explicit meeting/wave always wins. ``reviewer`` and ``reviewer_kind`` are
    accepted so both v1 (reviewer kind) and v2 (reviewer mode) can call this.
    """
    mode = str(mode or "panel").lower()
    reviewer = str(reviewer or "heuristic").lower()
    kind = str(reviewer_kind or "").lower()
    reasons: list[str] = []
    if mode == "meeting" or reviewer == "meeting" or kind == "meeting-reviewer":
        reasons.append("meeting protocol")
        tier = "E4"
    elif reviewer == "wave" or kind in {"wave-reviewer", "role-reviewer"}:
        reasons.append("wave reviewer")
        tier = "E3"
    elif reviewer == "none" or kind == "none":
        reasons.append("reviewer=none")
        tier = "E1"
    elif reviewer in {"llm", "heuristic"} or kind in {"heuristic", "llm"}:
        reasons.append(f"reviewer={reviewer or kind}")
        tier = "E2"
    else:
        reasons.append("default quick path")
        tier = "E2"
    if risk and tier == "E2":
        reasons.append("risk upgrade to E3")
        tier = "E3"
    if multi_phase and tier == "E1":
        reasons.append("multi-phase upgrade to E2")
        tier = "E2"

    spec = TIERS[tier]
    return TierDecision(
        tier=tier,
        planner=spec.planner_level,
        reviewer_count=spec.reviewer_count,
        reasons=tuple(reasons),
        spec=spec,
    )


def planner_for_tier(tier: str) -> str:
    return TIERS.get(str(tier or "E2"), TIERS["E2"]).planner_level


def required_planners(tier: str) -> int:
    return {"P1": 1, "P2": 2, "P3": 3}.get(planner_for_tier(tier), 1)


def tier_from_plan_reviewer(plan: Mapping[str, Any], reviewer: str = "heuristic",
                            mode: str = "panel") -> TierDecision:
    return select_tier(mode=mode, reviewer=reviewer)


# Capability policy is intentionally separate from the current wiring truth.
# It says what a tier is allowed to use; capability_registry.py says what is
# actually wired today.
CAPABILITY_POLICY: dict[str, dict[str, Any]] = {
    "repo_map": {"tiers": list(TIER_ORDER), "roles": ["planner"]},
    "lessons": {"tiers": list(TIER_ORDER), "roles": ["planner", "executor"]},
    "aci": {"tiers": list(TIER_ORDER), "roles": ["coder", "executor"]},
    "memory_budget": {"tiers": list(TIER_ORDER), "roles": ["planner", "reviewer"]},
    "events": {"tiers": list(TIER_ORDER), "roles": ["all"]},
}


def capabilities_for_tier(tier: str) -> list[str]:
    tier = str(tier or "E2")
    return [
        name for name, policy in CAPABILITY_POLICY.items()
        if tier in policy["tiers"]
    ]


def compact_timeline(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Small E2-friendly timeline: no graph, only meaningful stages."""
    mapping = {
        "tier.selected": "tier",
        "planner.selected": "planner",
        "recipe.start": "start",
        "recipe.coder": "coder",
        "recipe.draft_verify": "verify",
        "reviewer.findings": "reviewer",
        "recipe.apply": "apply",
        "safe_writer.verify": "verify",
        "recipe.done": "done",
        "job.done": "done",
    }
    result: list[dict[str, Any]] = []
    for event in events or []:
        event_type = str(event.get("event_type") or "")
        stage = mapping.get(event_type)
        if not stage:
            continue
        result.append({
            "stage": stage,
            "event_type": event_type,
            "timestamp": event.get("timestamp"),
            "status": (event.get("result") or {}).get("status")
            if isinstance(event.get("result"), Mapping) else None,
            "run_id": event.get("run_id"),
        })
    return result


def summary(decision: TierDecision) -> str:
    return f"{decision.tier} / {decision.planner} ({decision.spec.label})"
