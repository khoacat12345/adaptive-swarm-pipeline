#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 5 - provider registry / dependency injection point."""

from __future__ import annotations

import os

from .base_adapter import LLMAdapter
from .mock_adapter import MockAdapter
from .opencode_adapter import OpenCodeAdapter


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories = {
            "opencode": lambda **kwargs: OpenCodeAdapter(**kwargs),
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

    def from_env(self, **kwargs) -> LLMAdapter:
        return self.build(os.environ.get("SWARM_PROVIDER", "opencode"), **kwargs)
