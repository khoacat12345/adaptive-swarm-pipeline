#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider/catalog-aware model selection for v2.1.

The machine-local config.json is produced by scout.py from the live OpenCode
catalog and auth list. v2 must select from that catalog, not from a hardcoded
list of model names. Free-only is the default; paid models require an explicit
cost estimate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .errors import ExecutionError

CONFIG_REL = Path(".agents") / "config.json"
ROLE_ORDER = ("security", "qa", "perf", "docs")


def load_config(root: str | Path) -> dict[str, Any]:
    path = Path(root).resolve() / CONFIG_REL
    if not path.exists():
        raise ExecutionError(
            f"missing {path}; run `python .agents/scout.py --write --verify` first"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionError(f"cannot read model config: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExecutionError("model config must be an object")
    return payload


def model_table(config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    table = config.get("models") or {}
    return {str(key): dict(value) for key, value in table.items()
            if isinstance(value, dict)}


def model_info(model: str, config: Mapping[str, Any]) -> dict[str, Any] | None:
    return model_table(config).get(str(model))


def is_free(model: str, config: Mapping[str, Any]) -> bool:
    entry = model_info(model, config)
    if entry is not None:
        return bool(entry.get("is_free")) or str(entry.get("cost_tier")) == "free"
    return str(model).lower().endswith("-free")


def is_usable(model: str, config: Mapping[str, Any]) -> bool:
    entry = model_info(model, config)
    if entry is None:
        return True  # explicit model may be known only to the live CLI
    return str(entry.get("auth_status") or "available") == "available"


def provider_status(config: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in ((config.get("catalog") or {}).get("providers") or {}).items()
    }


def _candidate_models(
    config: Mapping[str, Any],
    *,
    free_only: bool,
    exclude_families: set[str],
    role_hint: str | None = None,
) -> list[dict[str, Any]]:
    entries = list(model_table(config).values())
    entries = [
        entry for entry in entries
        if is_usable(str(entry.get("id") or ""), config)
        and (not free_only or entry.get("is_free") or entry.get("cost_tier") == "free")
        and str(entry.get("family") or "unknown") not in exclude_families
    ]

    def score(entry: Mapping[str, Any]) -> tuple:
        caps = entry.get("capabilities") or {}
        if role_hint == "coder":
            quality = float(caps.get("code") or 0.0) + float(caps.get("reasoning") or 0.0)
        elif role_hint == "security":
            quality = float(caps.get("reasoning") or 0.0) + float(caps.get("code") or 0.0)
        else:
            quality = float(caps.get("reasoning") or 0.0) + float(caps.get("long_context") or 0.0)
        free_rank = 0 if entry.get("is_free") else 1
        return (free_rank, -quality, str(entry.get("id") or ""))

    return sorted(entries, key=score)


def select_free_seats(config: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
    """Choose a free coder and four free reviewers from the live catalog.

    Reviewers must use a different family from the coder. If the catalog cannot
    satisfy a role, fail closed instead of silently choosing a paid model.
    """
    coder_candidates = _candidate_models(
        config, free_only=True, exclude_families=set(), role_hint="coder"
    )
    if not coder_candidates:
        raise ExecutionError("no free coder model is available in config.json")
    coder = str(coder_candidates[0].get("id"))
    coder_family = str(coder_candidates[0].get("family") or "unknown")

    reviewers: dict[str, str] = {}
    used: set[str] = set()
    for role in ROLE_ORDER:
        candidates = _candidate_models(
            config,
            free_only=True,
            exclude_families={coder_family},
            role_hint="security" if role == "security" else role,
        )
        choice = next(
            (entry for entry in candidates if str(entry.get("id")) not in used),
            None,
        )
        if choice is None:
            raise ExecutionError(
                f"no free {role} reviewer from a different family than coder {coder_family}"
            )
        model = str(choice.get("id"))
        reviewers[role] = model
        used.add(model)
    return coder, reviewers


def validate_explicit(
    coder_model: str,
    reviewer_models: Mapping[str, str],
    config: Mapping[str, Any],
    *,
    free_only: bool = True,
) -> None:
    """Validate explicit models without replacing the caller's choice."""
    if free_only:
        for model in [coder_model, *reviewer_models.values()]:
            if not is_free(model, config):
                raise ExecutionError(f"paid model blocked by free-only policy: {model}")
    for model in [coder_model, *reviewer_models.values()]:
        if not is_usable(model, config):
            raise ExecutionError(f"model is not available in auth/catalog: {model}")
    coder_family = str((model_info(coder_model, config) or {}).get("family") or "")
    if not coder_family:
        return
    for role, model in reviewer_models.items():
        family = str((model_info(model, config) or {}).get("family") or "")
        if family and family == coder_family:
            raise ExecutionError(
                f"reviewer {role} family {family} equals coder family {coder_family}"
            )
