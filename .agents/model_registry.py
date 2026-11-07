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
    json_mode: bool | None
    pricing: dict[str, float] | None
    is_free: bool
    cost_tier: str
    auth_status: str
    health: dict[str, Any]
    fallback_metadata: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Offline/9router-down metadata. These are conservative capability facts used
# only when the live catalog omits a field. They are not a billing source.
FALLBACK_METADATA: dict[str, dict[str, Any]] = {
    "opencode/deepseek-v4.1-flash-free": {
        "provider": "opencode", "family": "deepseek", "context_window": 128000,
        "supports_tools": True, "json_mode": True,
        "pricing": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    },
    "opencode-go/deepseek-v4.1-flash": {
        "provider": "opencode-go", "family": "deepseek", "context_window": 128000,
        "supports_tools": True, "json_mode": True,
        "pricing": {"input": 0.30, "output": 1.20, "cache_read": 0.006, "cache_write": 0.0},
    },
    "opencode/glm-5.3-flash-free": {
        "provider": "opencode", "family": "glm", "context_window": 128000,
        "supports_tools": True, "json_mode": True,
        "pricing": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    },
    "opencode-go/glm-5.3": {
        "provider": "opencode-go", "family": "glm", "context_window": 200000,
        "supports_tools": True, "json_mode": True,
        "pricing": {"input": 0.60, "output": 2.20, "cache_read": 0.06, "cache_write": 0.0},
    },
    "opencode/nemotron-3.5-super-vl-free": {
        "provider": "opencode", "family": "nemotron", "context_window": 128000,
        "supports_tools": True, "json_mode": False,
        "pricing": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    },
    "opencode/nemotron-3.5-lightning-free": {
        "provider": "opencode", "family": "nemotron", "context_window": 128000,
        "supports_tools": True, "json_mode": False,
        "pricing": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    },
    "opencode-go/qwen3.8-max": {
        "provider": "opencode-go", "family": "qwen", "context_window": 1000000,
        "supports_tools": True, "json_mode": True,
        "pricing": {"input": 1.20, "output": 4.80, "cache_read": 0.12, "cache_write": 0.0},
    },
    "opencode-go/qwen3.8-flash": {
        "provider": "opencode-go", "family": "qwen", "context_window": 128000,
        "supports_tools": True, "json_mode": True,
        "pricing": {"input": 0.20, "output": 0.80, "cache_read": 0.02, "cache_write": 0.0},
    },
}


def fallback_for(model_id: str) -> dict[str, Any] | None:
    model_id = str(model_id)
    if model_id in FALLBACK_METADATA:
        return dict(FALLBACK_METADATA[model_id])
    lower = model_id.lower()
    for key, value in FALLBACK_METADATA.items():
        family = str(value.get("family") or "")
        if family and family in lower:
            return dict(value)
    return None


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
    fallback = fallback_for(model_id) or {}
    merged = dict(fallback)
    merged.update({key: value for key, value in entry.items() if value is not None})
    family = str(merged.get("family") or "unknown")
    provider = str(merged.get("provider") or model_id.split("/", 1)[0])
    pricing = _pricing(merged)
    is_free = bool(merged.get("is_free")) or str(merged.get("cost_tier") or "") == "free"
    if is_free:
        pricing = {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}
    health_data = (health or {}).get("entries", {}).get(model_id) or {}
    return ModelCapability(
        model_id=str(model_id),
        provider=provider,
        family=family,
        context_window=merged.get("context_window"),
        supports_tools=_supports_tools(merged),
        json_mode=(merged.get("json_mode")
                   if isinstance(merged.get("json_mode"), bool) else None),
        pricing=pricing,
        is_free=is_free,
        cost_tier=str(merged.get("cost_tier") or ("free" if is_free else "unknown")),
        auth_status=str(merged.get("auth_status") or "unknown"),
        health=dict(health_data) if isinstance(health_data, Mapping) else {},
        fallback_metadata=bool(fallback and (
            entry.get("context_window") is None
            or entry.get("pricing") is None
            or _supports_tools(entry) is None
        )),
    )


def load(root: str | Path) -> dict[str, ModelCapability]:
    try:
        data = json.loads(config_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    health = _health(root)
    models = data.get("models") if isinstance(data, Mapping) else None
    if not isinstance(models, Mapping) or not models:
        models = {
            model_id: {
                **meta, "id": model_id, "auth_status": "fallback",
                "is_free": not meta.get("pricing")
                or not any(meta.get("pricing", {}).values()),
                "cost_tier": "free" if not meta.get("pricing")
                or not any(meta.get("pricing", {}).values()) else "unknown",
            }
            for model_id, meta in FALLBACK_METADATA.items()
        }
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
