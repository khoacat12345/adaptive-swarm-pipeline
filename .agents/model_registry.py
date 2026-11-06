#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalized model capability registry (P0-03 / P1-08).

Reads the machine-local `.agents/config.json` produced by scout and exposes one
stable shape for routing, cost checks, tool support and health.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class ModelCapability:
    model_id: str
    provider: str
    family: str
    context_window: int | None
    supports_tools: bool | None
    pricing: dict[str, float] | None
    is_free: bool
    cost_tier: str
    auth_status: str
    health: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def config_path(root: str | Path) -> Path:
    return Path(root).resolve() / ".agents" / "config.json"


def _health(root: str | Path) -> dict[str, Any]:
    path = Path(root).resolve() / ".agents" / "tmp" / "health.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _pricing(entry: Mapping[str, Any]) -> dict[str, float] | None:
    raw = entry.get("pricing")
    if not isinstance(raw, Mapping):
        return None
    result: dict[str, float] = {}
    for key in ("input", "output", "cache_read", "cache_write"):
        value = raw.get(key)
        if value is None:
            continue
        try:
            result[key] = float(value)
        except (TypeError, ValueError):
            return None
    return result or None


def _supports_tools(entry: Mapping[str, Any]) -> bool | None:
    explicit = entry.get("supports_tools")
    if isinstance(explicit, bool):
        return explicit
    caps = entry.get("capabilities")
    if isinstance(caps, Mapping):
        value = caps.get("tooling")
        if value is not None:
            try:
                return float(value) >= 0.5
            except (TypeError, ValueError):
                return None
    return None


def normalize(model_id: str, entry: Mapping[str, Any],
              health: Mapping[str, Any] | None = None) -> ModelCapability:
    family = str(entry.get("family") or "unknown")
    provider = str(entry.get("provider") or model_id.split("/", 1)[0])
    pricing = _pricing(entry)
    is_free = bool(entry.get("is_free")) or str(entry.get("cost_tier") or "") == "free"
    if is_free:
        pricing = {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}
    health_data = (health or {}).get("entries", {}).get(model_id) or {}
    return ModelCapability(
        model_id=str(model_id),
        provider=provider,
        family=family,
        context_window=entry.get("context_window"),
        supports_tools=_supports_tools(entry),
        pricing=pricing,
        is_free=is_free,
        cost_tier=str(entry.get("cost_tier") or ("free" if is_free else "unknown")),
        auth_status=str(entry.get("auth_status") or "unknown"),
        health=dict(health_data) if isinstance(health_data, Mapping) else {},
    )


def load(root: str | Path) -> dict[str, ModelCapability]:
    try:
        data = json.loads(config_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    health = _health(root)
    models = data.get("models") if isinstance(data, Mapping) else None
    if not isinstance(models, Mapping):
        return {}
    return {
        str(model_id): normalize(str(model_id), entry, health)
        for model_id, entry in models.items()
        if isinstance(entry, Mapping)
    }


def get(root: str | Path, model_id: str) -> ModelCapability | None:
    return load(root).get(str(model_id))


def usable(root: str | Path, model_id: str, *, free_only: bool = False,
           require_tools: bool = False) -> bool:
    model = get(root, model_id)
    if model is None or model.auth_status not in {"available", "configured"}:
        return False
    if free_only and not model.is_free:
        return False
    if require_tools and model.supports_tools is False:
        return False
    return True


def estimate_cost(root: str | Path, model_id: str, *,
                  input_tokens: int = 0, output_tokens: int = 0,
                  cache_read_tokens: int = 0) -> float | None:
    model = get(root, model_id)
    if model is None or model.pricing is None:
        return None
    price = model.pricing
    cost = (
        (max(0, input_tokens) / 1_000_000.0) * float(price.get("input", 0.0))
        + (max(0, output_tokens) / 1_000_000.0) * float(price.get("output", 0.0))
        + (max(0, cache_read_tokens) / 1_000_000.0) * float(price.get("cache_read", 0.0))
    )
    return round(cost, 8)


def report(root: str | Path) -> dict[str, Any]:
    models = load(root)
    return {
        "count": len(models),
        "free": sum(1 for item in models.values() if item.is_free),
        "models": {key: item.as_dict() for key, item in models.items()},
    }
