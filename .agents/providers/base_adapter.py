#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 5 - common LLM adapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AdapterCapabilities:
    """Provider-side mirror of ``v2.runtime.RuntimeCapabilities``.

    Keeping this tiny (no imports from ``v2``) lets provider code stay a leaf
    dependency while still declaring what the runtime dispatcher may select.
    """

    headless: bool = True
    tools: bool = True
    reasoning: bool = True
    json_output: bool = False
    messaging: bool = False
    write_mode: str = "workspace"
    max_parallel: int = 1

    def as_dict(self) -> dict:
        return {
            "headless": self.headless,
            "tools": self.tools,
            "reasoning": self.reasoning,
            "json_output": self.json_output,
            "messaging": self.messaging,
            "write_mode": self.write_mode,
            "max_parallel": self.max_parallel,
        }


@dataclass
class AgentReply:
    """Normalized reply returned by every provider.

    `data` is populated when the model returned a JSON object; `raw` keeps the
    original text for debugging without making it a required interface.
    """

    text: str
    data: dict | None = None
    model: str = ""
    usage: dict = field(default_factory=dict)
    raw: str | None = None

    def __post_init__(self) -> None:
        self.text = str(self.text or "")
        if self.data is not None and not isinstance(self.data, dict):
            self.data = dict(self.data) if isinstance(self.data, (list, tuple)) else None
        self.model = str(self.model or "")
        self.usage = dict(self.usage or {})
        self.raw = str(self.raw) if self.raw is not None else None


class LLMAdapter(ABC):
    """Provider-neutral boundary used by workers and meeting tests."""

    name = "base"
    default_capabilities = AdapterCapabilities()

    def capabilities(self) -> AdapterCapabilities:
        return self.default_capabilities

    def health(self) -> dict:
        """Cheap readiness probe; subclasses override for real CLI checks."""
        return {"ok": True, "name": self.name}

    @abstractmethod
    def complete(self, messages: list[dict], schema: dict | None = None) -> AgentReply:
        raise NotImplementedError
