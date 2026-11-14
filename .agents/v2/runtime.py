#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 AgentRuntime contract for multi-client execution.

This is a design boundary, not a client implementation. M3.5 adapters can be
wrapped behind it without changing worktree, budget or verify semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

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
    reasoning: bool = True
    messaging: bool = True
    cancel: bool = True
    sandbox: bool = True
    max_parallel: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "headless": self.headless,
            "tools": self.tools,
            "json_output": self.json_output,
            "usage_report": self.usage_report,
            "write_mode": self.write_mode,
            "streaming": self.streaming,
            "custom_base_url": self.custom_base_url,
            "reasoning": self.reasoning,
            "messaging": self.messaging,
            "cancel": self.cancel,
            "sandbox": self.sandbox,
            "max_parallel": self.max_parallel,
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


@runtime_checkable
class TaskRuntime(Protocol):
    """Agent-agnostic task protocol (F04).

    A runtime owns a *handle* for one task; the orchestrator can message it,
    wait for the result and cancel it without knowing which CLI is underneath.
    ``execute`` stays available for the older batch adapters.
    """

    name: str

    def capabilities(self) -> RuntimeCapabilities:
        ...

    def health(self) -> dict[str, Any]:
        ...

    def start_task(
        self,
        task: Mapping[str, Any],
        *,
        worktree: Any = None,
        model: str = "",
        tier: str = "E3",
    ) -> str:
        ...

    def send_message(self, handle: str, message: str) -> dict[str, Any]:
        ...

    def wait(self, handle: str, *, timeout: float | None = None) -> RuntimeResult:
        ...

    def cancel(self, handle: str, *, reason: str = "") -> dict[str, Any]:
        ...


class ClaudeCodeRuntimeStub:
    """P1-07 second-runtime boundary; deliberately not executable yet."""

    name = "claude-code-stub"

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            headless=True, tools=True, json_output=True,
            usage_report=False, write_mode="patch", streaming=False,
        )

    def health(self) -> dict[str, Any]:
        return {"ok": False, "stub": True, "reason": "adapter not implemented"}

    def execute(self, work_order: Mapping[str, Any], *, worktree: Any,
                budget: Any, feedback: str | None = None) -> RuntimeResult:
        raise ExecutionError("claude-code runtime adapter is a stub")


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

    def register_many(self, runtimes: Iterable[AgentRuntime]) -> None:
        for runtime in runtimes:
            self.register(runtime)

    def find(self, name: str) -> AgentRuntime | None:
        return self._runtimes.get(str(name))

    def requiring(
        self,
        *,
        reasoning: bool | None = None,
        tools: bool | None = None,
        headless: bool | None = None,
        messaging: bool | None = None,
    ) -> list[AgentRuntime]:
        """Runtimes that satisfy every requested capability."""
        wanted = {
            "reasoning": reasoning,
            "tools": tools,
            "headless": headless,
            "messaging": messaging,
        }
        result = []
        for name in sorted(self._runtimes):
            runtime = self._runtimes[name]
            caps = runtime.capabilities()
            if all(
                value is None or bool(getattr(caps, key, False)) is bool(value)
                for key, value in wanted.items()
            ):
                result.append(runtime)
        return result

    def health(self) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for name, runtime in sorted(self._runtimes.items()):
            try:
                rows[name] = dict(runtime.health())
            except Exception as exc:  # noqa: BLE001 - a health probe never raises
                rows[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        return rows
