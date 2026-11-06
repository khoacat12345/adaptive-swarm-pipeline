#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-14: extracted-capability registry and per-tier policy.

The registry distinguishes three things:

* **source** — which research idea it came from;
* **tiers** — which execution tiers are allowed to use it;
* **wiring** — what is actually connected in v1/v2 today.

Do not advertise a capability as fully live when ``wiring`` says partial.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from tier_policy import TIER_ORDER, capabilities_for_tier


@dataclass(frozen=True)
class Capability:
    name: str
    source: str
    tiers: tuple[str, ...]
    roles: tuple[str, ...]
    wiring: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


CAPABILITIES: dict[str, Capability] = {
    "repo_map": Capability(
        "repo_map", "aider", TIER_ORDER, ("planner",),
        "partial", "v2 planning prompt; v1 planner does not load it yet",
    ),
    "lessons": Capability(
        "lessons", "mem0", TIER_ORDER, ("planner", "executor"),
        "partial", "v2 planning hints + v2 execution capture; v1 meeting not wired",
    ),
    "aci": Capability(
        "aci", "SWE-agent", TIER_ORDER, ("coder", "executor"),
        "partial", "v2 post-edit validation; not yet a coder tool-loop context",
    ),
    "memory_budget": Capability(
        "memory_budget", "letta-code", TIER_ORDER, ("planner", "reviewer"),
        "partial", "v2 context-budget primitives; not every live runner",
    ),
    "events": Capability(
        "events", "OpenHands", TIER_ORDER, ("all",),
        "partial", "event schema + replay; not every runner path",
    ),
}


def get(name: str) -> Capability | None:
    return CAPABILITIES.get(str(name or "").lower())


def for_tier(tier: str) -> list[Capability]:
    tier = str(tier or "E2")
    names = capabilities_for_tier(tier)
    return [CAPABILITIES[name] for name in names if name in CAPABILITIES]


def matrix() -> dict[str, Any]:
    return {
        "tiers": list(TIER_ORDER),
        "capabilities": {
            name: capability.as_dict() for name, capability in CAPABILITIES.items()
        },
    }


def report() -> dict[str, Any]:
    return {
        "tiers": {
            tier: [cap.name for cap in for_tier(tier)]
            for tier in TIER_ORDER
        },
        "wiring": {
            name: cap.wiring for name, cap in CAPABILITIES.items()
        },
    }
