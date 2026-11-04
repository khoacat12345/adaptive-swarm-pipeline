#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 AgentRuntime contract for multi-client execution.

This is a design boundary, not a client implementation. M3.5 adapters can be
wrapped behind it without changing worktree, budget or verify semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from .errors import ExecutionError


@dataclass(frozen=True)
class RuntimeCapabilities:
    headless: bool = False
    tools: bool = True
    json_output: bool = False
    usage_report: bool = False
    write_mode: str = "patch"  # patch | workspace
    streaming: bool = False
    custom_base_url: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "headless": self.headless,
            "tools": self.tools,
            "json_output": self.json_output,
            "usage_report": self.usage_report,
            "write_mode": self.write_mode,
            "streaming": self.streaming,
            "custom_base_url": self.custom_base_url,
        }


@dataclass
class RuntimeResult:
    status: str
    model: str = ""
    family: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    diff: str = ""
    error: str | None = None
    raw: Any = None

    def validate(self) -> "RuntimeResult":
        if self.status not in {"ok", "failed", "blocked", "paused"}:
            raise ExecutionError(f"invalid runtime status: {self.status!r}")
        if not isinstance(self.usage, dict):
            raise ExecutionError("runtime usage must be an object")
        if not isinstance(self.files, dict):
            raise ExecutionError("runtime files must be an object")
        return self


@runtime_checkable
class AgentRuntime(Protocol):
    name: str

    def capabilities(self) -> RuntimeCapabilities:
        ...

    def health(self) -> dict[str, Any]:
        ...

    def execute(
        self,
        work_order: Mapping[str, Any],
        *,
        worktree: Any,
        budget: Any,
        feedback: str | None = None,
    ) -> RuntimeResult:
        ...


class RuntimeRegistry:
    """Small registry for built-in and future runtime adapters."""

    def __init__(self) -> None:
        self._runtimes: dict[str, AgentRuntime] = {}

    def register(self, runtime: AgentRuntime) -> None:
        name = str(getattr(runtime, "name", "") or "").strip()
        if not name:
            raise ExecutionError("runtime must have a name")
        if name in self._runtimes:
            raise ExecutionError(f"runtime already registered: {name}")
        self._runtimes[name] = runtime

    def get(self, name: str) -> AgentRuntime:
        try:
            return self._runtimes[str(name)]
        except KeyError as exc:
            raise ExecutionError(f"unknown runtime: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._runtimes)

    def capabilities(self) -> dict[str, dict[str, Any]]:
        return {
            name: runtime.capabilities().as_dict()
            for name, runtime in self._runtimes.items()
        }
