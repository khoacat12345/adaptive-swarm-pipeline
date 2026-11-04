#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 0 feature flags for adaptive integrations.

All flags default to ``off``. ``shadow`` means compute/record but do not change
the prompt or execution path. ``on`` is opt-in after acceptance. Unknown values
raise ``AdaptiveFlagError`` so a typo can never silently enable a feature.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Mapping

MODES = ("off", "shadow", "on")
FLAG_NAMES = ("REPO_MAP", "ACI", "LESSONS", "MEMORY_BUDGET", "EVENTS")
ENV_NAMES = {
    "REPO_MAP": "SWARM_ADAPTIVE_REPO_MAP",
    "ACI": "SWARM_ADAPTIVE_ACI",
    "LESSONS": "SWARM_ADAPTIVE_LESSONS",
    "MEMORY_BUDGET": "SWARM_ADAPTIVE_MEMORY_BUDGET",
    "EVENTS": "SWARM_ADAPTIVE_EVENTS",
}


class AdaptiveFlagError(ValueError):
    code = "ADAPTIVE_FLAG_INVALID"


@dataclass(frozen=True)
class AdaptiveFlags:
    repo_map: str = "off"
    aci: str = "off"
    lessons: str = "off"
    memory_budget: str = "off"
    events: str = "off"

    def __post_init__(self) -> None:
        for field in fields(self):
            value = str(getattr(self, field.name)).strip().lower()
            if value not in MODES:
                raise AdaptiveFlagError(
                    f"invalid adaptive flag {field.name}={value!r}; expected one of {MODES}"
                )
            object.__setattr__(self, field.name, value)

    @property
    def all_off(self) -> bool:
        return all(self.get(name) == "off" for name in FLAG_NAMES)

    def get(self, name: str) -> str:
        key = str(name).strip().upper()
        if key not in FLAG_NAMES:
            raise AdaptiveFlagError(f"unknown adaptive flag: {name}")
        return str(getattr(self, key.lower()))

    def enabled(self, name: str) -> bool:
        return self.get(name) == "on"

    def shadow(self, name: str) -> bool:
        return self.get(name) == "shadow"

    def active(self, name: str) -> bool:
        return self.get(name) in {"shadow", "on"}

    def as_dict(self) -> dict[str, str]:
        return {name: self.get(name) for name in FLAG_NAMES}

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "AdaptiveFlags":
        normalized: dict[str, str] = {}
        for name in FLAG_NAMES:
            key = name.lower()
            if key in values:
                normalized[key] = values[key]
        return cls(**normalized)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        strict: bool = True,
    ) -> "AdaptiveFlags":
        source = os.environ if env is None else env
        values: dict[str, str] = {}
        errors: list[str] = []
        for name in FLAG_NAMES:
            raw = source.get(ENV_NAMES[name])
            if raw is None or str(raw).strip() == "":
                values[name.lower()] = "off"
                continue
            value = str(raw).strip().lower()
            if value not in MODES:
                errors.append(f"{ENV_NAMES[name]}={raw!r}")
            else:
                values[name.lower()] = value
        if errors:
            if strict:
                raise AdaptiveFlagError(
                    "invalid adaptive flags: " + ", ".join(errors)
                )
            return cls()
        return cls(**values)


def load_flags(
    env: Mapping[str, str] | None = None,
    *,
    strict: bool = True,
) -> AdaptiveFlags:
    """Load flags from environment; strict mode is fail-closed."""
    return AdaptiveFlags.from_env(env, strict=strict)
