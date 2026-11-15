#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 5 / F04 - provider registry / dependency injection point."""

from __future__ import annotations

import os
from typing import Any

from .base_adapter import AdapterCapabilities, LLMAdapter
from .mock_adapter import MockAdapter
from .opencode_adapter import (
    ClaudeCodeAdapter,
    GenericCliAdapter,
    OpenCodeAdapter,
)


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories = {
            "opencode": lambda **kwargs: OpenCodeAdapter(**kwargs),
            "claude-code": lambda **kwargs: ClaudeCodeAdapter(**kwargs),
            "generic-cli": lambda **kwargs: GenericCliAdapter(**kwargs),
            "mock": lambda **kwargs: MockAdapter(**kwargs),
        }

    def register(self, name: str, factory) -> None:
        self._factories[str(name).lower()] = factory

    def names(self) -> list[str]:
        return sorted(self._factories)

    def build(self, name: str = "opencode", **kwargs) -> LLMAdapter:
        key = str(name or "opencode").lower()
        factory = self._factories.get(key)
        if factory is None:
            raise ValueError(f"provider khong ho tro: {name}")
        adapter = factory(**kwargs)
        if not isinstance(adapter, LLMAdapter):
            raise TypeError(f"provider {key} khong tra ve LLMAdapter")
        return adapter

    def capabilities(self, name: str) -> AdapterCapabilities:
        """Declared capabilities for one provider (no process is spawned)."""
        return self.build(name).capabilities()

    def capability_matrix(self) -> dict[str, dict[str, Any]]:
        """Every provider's capability flags, for runtime/tier selection."""
        matrix: dict[str, dict[str, Any]] = {}
        for name in self.names():
            try:
                matrix[name] = self.capabilities(name).as_dict()
            except Exception as exc:  # noqa: BLE001 - a broken provider is data
                matrix[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        return matrix

    def from_env(self, **kwargs) -> LLMAdapter:
        return self.build(os.environ.get("SWARM_PROVIDER", "opencode"), **kwargs)
