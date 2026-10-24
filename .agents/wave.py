#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 2 - reviewer panel helpers for meeting mode.

Kept separate from `workers.py` so the meeting core does not depend on the
OpenCode CLI.  The defaults are the same four roles used by wave review.
"""

from __future__ import annotations

import os

DEFAULT_ROLES = {
    "security": "opencode/nemotron-3.5-super-vl-free",
    "qa": "opencode/minimax-m3.1-flash-free",
    "perf": "opencode/deepseek-v4.1-flash-free",
    "docs": "opencode/glm-5.3-flash-free",
}
ROLE_ORDER = ("security", "qa", "perf", "docs")


def normalize_roles(raw: str | None = None) -> dict[str, str]:
    """Parse `security=model,qa=model,...`; fall back to defaults."""
    value = raw if raw is not None else os.environ.get("SWARM_WAVE_ROLES")
    if not value:
        return dict(DEFAULT_ROLES)
    roles: dict[str, str] = {}
    for item in str(value).split(","):
        role, _, model = item.partition("=")
        role, model = role.strip().lower(), model.strip()
        if role and model:
            roles[role] = model
    return roles or dict(DEFAULT_ROLES)


def build_panel(roles: dict | None = None, *, factory=None) -> dict:
    """Build role -> Reviewer objects.

    `factory(role, model)` is injectable for tests and future providers.
    """
    if roles is None:
        roles = normalize_roles()
    if factory is None:
        import workers
        factory = lambda role, model: workers.RoleReviewer(role, model)
    return {role: factory(role, model) for role, model in roles.items()}


def select_participants(conflicts, limit: int = 2) -> list[str]:
    """Pick at most `limit` dissenters, security/qa first."""
    roles: list[str] = []
    for conflict in conflicts or []:
        role = conflict.get("role") if isinstance(conflict, dict) else getattr(conflict, "role", None)
        role = str(role or "").lower()
        if role and role not in roles:
            roles.append(role)
    priority = {role: index for index, role in enumerate(ROLE_ORDER)}
    roles.sort(key=lambda item: (priority.get(item, 99), item))
    return roles[:max(0, int(limit))]
