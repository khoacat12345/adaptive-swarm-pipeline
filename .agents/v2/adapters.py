#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M3.5 adapters and the agent-agnostic runtime protocol (F04).

Two layers live here:

* ``WorkersCoderAdapter`` / ``WorkersReviewerAdapter`` bridge the existing
  ``workers`` classes to the M3 execution protocol;
* the ``*Runtime`` classes implement ``start_task`` / ``send_message`` /
  ``wait`` / ``cancel`` so a run can use OpenCode, Claude Code, a generic CLI
  or a deterministic mock without changing the engine.

The bridge never writes project files itself.  It only proposes file contents to
the Lead/ExecutionEngine, which applies them through safe_writer in the guarded
worktree.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .errors import ExecutionError, ReviewerUnavailable
from .execution import ExecutionSeat, model_family
from .models import is_free, select_free_seats, validate_explicit
from .runtime import (
    RuntimeCapabilities,
    RuntimeRegistry,
    RuntimeResult,
)

# Emergency fallback only; the preferred path resolves from .agents/config.json.
FREE_MODELS = {
    "coder": "opencode/glm-5.3-flash-free",
    "security": "opencode/nemotron-3.5-super-vl-free",
    "qa": "opencode/deepseek-v4.1-flash-free",
    "perf": "opencode/mimo-v2.6-flash-free",
    "docs": "opencode/nemotron-3.5-lightning-free",
}


class RuntimeAdapterError(ExecutionError):
    code = "RUNTIME_ADAPTER_ERROR"


class CapabilityRejected(ExecutionError):
    """A runtime/model cannot satisfy the tier's required capabilities."""

    code = "CAPABILITY_REJECTED"


TIER_REQUIREMENTS: dict[str, dict[str, bool]] = {
    "E1": {},
    "E2": {"tools": True},
    "E3": {"tools": True, "reasoning": True},
    "E4": {"tools": True, "reasoning": True, "messaging": True},
}


def tier_requirements(tier: str) -> dict[str, bool]:
    return dict(TIER_REQUIREMENTS.get(str(tier or "E3").strip().upper(),
                                      TIER_REQUIREMENTS["E3"]))


def assert_capabilities(
    runtime: Any,
    *,
    tier: str = "E3",
    model_info: Mapping[str, Any] | None = None,
    model: str = "",
) -> RuntimeCapabilities:
    """Fail closed when a runtime/model cannot serve the selected tier."""
    capabilities = runtime.capabilities()
    wanted = tier_requirements(tier)
    missing = [
        key for key, value in wanted.items()
        if bool(value) and not bool(getattr(capabilities, key, False))
    ]
    if missing:
        raise CapabilityRejected(
            f"runtime {getattr(runtime, 'name', '?')} lacks {', '.join(missing)} "
            f"required by tier {str(tier).upper()}"
        )
    caps = (model_info or {}).get("capabilities") if isinstance(model_info, Mapping) else None
    if isinstance(caps, Mapping) and "reasoning" in caps:
        try:
            reasoning = float(caps.get("reasoning"))
        except (TypeError, ValueError):
            reasoning = 1.0
        if str(tier).upper() == "E4" and reasoning <= 0.0:
            raise CapabilityRejected(
                f"model {model or '?'} has no reasoning capability; "
                "tier E4 needs a reasoning seat"
            )
    return capabilities


def _prompt_for(task: Mapping[str, Any], *, messages: Sequence[str] = ()) -> str:
    lines = [
        f"# Task {task.get('id') or task.get('work_order_id') or 'work'}",
        f"Objective: {task.get('objective') or task.get('title') or ''}",
    ]
    for key, label in (
        ("boundaries", "Boundaries"),
        ("input_context", "Input context"),
        ("acceptance_criteria", "Acceptance criteria"),
        ("verify_command", "Verify command"),
    ):
        value = str(task.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    targets = [str(item) for item in (task.get("target_files") or [])]
    if targets:
        lines.append("Target files: " + ", ".join(targets))
    if messages:
        lines.append("")
        lines.append("## Operator messages")
        lines.extend(f"- {message}" for message in messages)
    return "\n".join(lines).strip() + "\n"


@dataclass
class _Handle:
    handle: str
    task: dict[str, Any]
    model: str
    tier: str
    worktree: Path | None
    messages: list[str] = field(default_factory=list)
    state: str = "QUEUED"
    result: RuntimeResult | None = None
    cancelled: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


class BaseTaskRuntime:
    """Shared handle bookkeeping for the concrete CLI runtimes."""

    name = "base"
    family = "unknown"

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        timeout: float = 300.0,
        runner: Callable[..., Any] | None = None,
        capabilities: RuntimeCapabilities | None = None,
        env: Mapping[str, str] | None = None,
    ):
        self.command = [str(item) for item in (command or [])]
        self.timeout = float(timeout)
        self._runner = runner
        self._capabilities = capabilities or self.default_capabilities()
        self.env = dict(env or {})
        self._handles: dict[str, _Handle] = {}

    def default_capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            headless=True, tools=True, json_output=True,
            usage_report=False, write_mode="patch", streaming=False,
        )

    def capabilities(self) -> RuntimeCapabilities:
        return self._capabilities

    def health(self) -> dict[str, Any]:
        return {
            "ok": bool(self.command),
            "name": self.name,
            "command": self.command[0] if self.command else None,
            "handles": len(self._handles),
        }

    # -- command construction ---------------------------------------
    def build_command(self, prompt: str, *, model: str = "",
                      cwd: Path | None = None) -> list[str]:
        if not self.command:
            raise RuntimeAdapterError(f"runtime {self.name} has no command")
        values = {
            "prompt": prompt,
            "model": model or "",
            "cwd": str(cwd or ""),
        }
        argv: list[str] = []
        for part in self.command:
            text = str(part)
            for key, value in values.items():
                text = text.replace("{" + key + "}", value)
            if "{" not in text:
                argv.append(text)
            else:
                # Leave unknown placeholders untouched; they are a config bug.
                argv.append(text)
        return argv

    def _run(self, argv: list[str], *, cwd: Path | None) -> dict[str, Any]:
        if self._runner is not None:
            return dict(self._runner(argv, cwd=cwd, timeout=self.timeout) or {})
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout,
            env={**os.environ, **self.env},
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
        }

    # -- protocol ----------------------------------------------------
    def start_task(
        self,
        task: Mapping[str, Any],
        *,
        worktree: Any = None,
        model: str = "",
        tier: str = "E3",
    ) -> str:
        assert_capabilities(self, tier=tier, model=model)
        handle = f"{self.name}-{uuid.uuid4().hex[:10]}"
        self._handles[handle] = _Handle(
            handle=handle,
            task=dict(task),
            model=str(model or ""),
            tier=str(tier or "E3"),
            worktree=Path(worktree).resolve() if worktree else None,
        )
        return handle

    def _get(self, handle: str) -> _Handle:
        try:
            return self._handles[str(handle)]
        except KeyError as exc:
            raise RuntimeAdapterError(f"unknown handle: {handle}") from exc

    def send_message(self, handle: str, message: str) -> dict[str, Any]:
        item = self._get(handle)
        if item.state in {"DONE", "FAILED", "CANCELLED"}:
            raise RuntimeAdapterError(f"cannot message a {item.state} handle")
        text = str(message or "").strip()
        if text:
            item.messages.append(text)
        return {
            "handle": item.handle,
            "state": item.state,
            "queued_messages": len(item.messages),
        }

    def cancel(self, handle: str, *, reason: str = "") -> dict[str, Any]:
        item = self._get(handle)
        item.cancelled = True
        item.state = "CANCELLED"
        item.result = RuntimeResult(
            status="failed", model=item.model, family=self.family,
            error=str(reason or "cancelled"),
        )
        return {"handle": item.handle, "state": item.state, "reason": reason}

    def wait(self, handle: str, *, timeout: float | None = None) -> RuntimeResult:
        item = self._get(handle)
        if item.cancelled or item.state == "CANCELLED":
            return item.result or RuntimeResult(
                status="failed", model=item.model, error="cancelled",
            )
        if item.result is not None and item.state in {"DONE", "FAILED"}:
            return item.result
        item.state = "RUNNING"
        prompt = _prompt_for(item.task, messages=item.messages)
        try:
            argv = self.build_command(prompt, model=item.model, cwd=item.worktree)
            completed = self._run(argv, cwd=item.worktree)
        except subprocess.TimeoutExpired as exc:
            item.state = "FAILED"
            item.result = RuntimeResult(
                status="failed", model=item.model, family=self.family,
                error=f"timeout after {self.timeout}s: {exc}"[:500],
            )
            return item.result
        except Exception as exc:  # noqa: BLE001 - normalize into a result
            item.state = "FAILED"
            item.result = RuntimeResult(
                status="failed", model=item.model, family=self.family,
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
            return item.result

        returncode = int(completed.get("returncode", 1) or 0)
        stdout = str(completed.get("stdout") or "")
        stderr = str(completed.get("stderr") or "")
        files = _parse_files(stdout)
        status = "ok" if returncode == 0 else "failed"
        item.state = "DONE" if status == "ok" else "FAILED"
        item.result = RuntimeResult(
            status=status,
            model=item.model,
            family=self.family,
            files=files,
            diff="" if files else stdout,
            error=None if status == "ok" else (stderr or stdout)[:500],
            raw={"returncode": returncode, "stdout": stdout, "stderr": stderr},
        ).validate()
        return item.result

    def handle_state(self, handle: str) -> dict[str, Any]:
        item = self._get(handle)
        return {
            "handle": item.handle,
            "state": item.state,
            "messages": list(item.messages),
            "model": item.model,
            "tier": item.tier,
        }


def _parse_files(stdout: str) -> dict[str, str]:
    """Accept plain JSON ``{"files": {...}}`` or ``{"path": "content"}``."""
    text = str(stdout or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    candidate = payload.get("files") if "files" in payload else payload
    if not isinstance(candidate, Mapping):
        return {}
    return {str(key): str(value) for key, value in candidate.items()}


class MockRuntime(BaseTaskRuntime):
    """Deterministic runtime for tests and dry runs.

    ``script`` maps a task id (or ``"*"``) to ``{"files": ..., "usage": ...}``;
    ``latency`` simulates a slow agent without making tests flaky (it is 0 by
    default and only sleeps when explicitly requested).
    """

    name = "mock"

    def __init__(
        self,
        script: Mapping[str, Any] | None = None,
        *,
        capabilities: RuntimeCapabilities | None = None,
        fail: bool = False,
        latency: float = 0.0,
    ):
        super().__init__(
            command=["mock"],
            capabilities=capabilities or RuntimeCapabilities(
                headless=True, tools=True, json_output=True,
                usage_report=True, write_mode="patch", streaming=False,
            ),
        )
        self.script = dict(script or {})
        self.fail = bool(fail)
        self.latency = max(0.0, float(latency))
        self.received: list[dict[str, Any]] = []

    def wait(self, handle: str, *, timeout: float | None = None) -> RuntimeResult:
        item = self._get(handle)
        if self.latency and not item.cancelled:
            time.sleep(self.latency)
        self.received.append({
            "handle": handle,
            "task_id": item.task.get("id"),
            "messages": list(item.messages),
        })
        if item.cancelled:
            item.state = "CANCELLED"
            return item.result or RuntimeResult(status="failed", error="cancelled")
        if self.fail:
            item.state = "FAILED"
            item.result = RuntimeResult(
                status="failed", model=item.model, family="mock",
                error="mock runtime failure",
            )
            return item.result
        result = self.script.get(str(item.task.get("id"))) or self.script.get("*")
        if isinstance(result, RuntimeResult):
            item.state = "DONE" if result.status == "ok" else "FAILED"
            item.result = result.validate()
            return item.result
        payload = dict(result or {})
        files = payload.get("files") or {
            str(item.task.get("target_files", ["out.py"])[0]): "# mock runtime\n"
        }
        item.state = "DONE"
        item.result = RuntimeResult(
            status="ok",
            model=item.model or "mock/runner",
            family="mock",
            files={str(key): str(value) for key, value in files.items()},
            diff="",
            usage=dict(payload.get("usage") or {"requests": 1, "cost_usd": 0.0}),
            raw={"handle": handle, "messages": list(item.messages)},
        ).validate()
        return item.result

    def health(self) -> dict[str, Any]:
        return {"ok": True, "name": self.name, "scripted": sorted(self.script),
                "handles": len(self._handles)}


class GenericCliRuntime(BaseTaskRuntime):
    """Any CLI that accepts a prompt on argv/stdin."""

    name = "generic-cli"

    def __init__(
        self,
        command: Sequence[str],
        *,
        timeout: float = 300.0,
        runner: Callable[..., Any] | None = None,
        capabilities: RuntimeCapabilities | None = None,
    ):
        super().__init__(
            command=command, timeout=timeout, runner=runner,
            capabilities=capabilities or RuntimeCapabilities(
                headless=True, tools=True, json_output=False,
                usage_report=False, write_mode="patch", streaming=False,
                reasoning=True, messaging=True,
            ),
        )


class OpenCodeRuntime(BaseTaskRuntime):
    """OpenCode CLI runtime (``opencode run``)."""

    name = "opencode"

    def __init__(
        self,
        *,
        executable: str = "opencode",
        timeout: float = 300.0,
        runner: Callable[..., Any] | None = None,
        capabilities: RuntimeCapabilities | None = None,
    ):
        super().__init__(
            command=[executable, "run", "--model", "{model}", "{prompt}"],
            timeout=timeout, runner=runner,
            capabilities=capabilities or RuntimeCapabilities(
                headless=True, tools=True, json_output=True,
                usage_report=True, write_mode="workspace", streaming=True,
                reasoning=True, messaging=True, cancel=True,
            ),
        )

    def health(self) -> dict[str, Any]:
        import shutil

        executable = self.command[0] if self.command else ""
        found = shutil.which(executable) if executable else None
        return {
            "ok": bool(found) or self._runner is not None,
            "name": self.name,
            "executable": found or executable,
            "degraded": not bool(found),
        }


class ClaudeCodeRuntime(BaseTaskRuntime):
    """Claude Code print-mode runtime (``claude -p``)."""

    name = "claude-code"

    def __init__(
        self,
        *,
        executable: str = "claude",
        timeout: float = 300.0,
        runner: Callable[..., Any] | None = None,
        capabilities: RuntimeCapabilities | None = None,
    ):
        super().__init__(
            command=[executable, "-p", "{prompt}", "--model", "{model}"],
            timeout=timeout, runner=runner,
            capabilities=capabilities or RuntimeCapabilities(
                headless=True, tools=True, json_output=True,
                usage_report=False, write_mode="workspace", streaming=False,
                reasoning=True, messaging=False, cancel=True,
            ),
        )


def default_runtimes(
    *,
    opencode_executable: str = "opencode",
    claude_executable: str = "claude",
    generic_command: Sequence[str] | None = None,
) -> list[Any]:
    runtimes: list[Any] = [
        OpenCodeRuntime(executable=opencode_executable),
        ClaudeCodeRuntime(executable=claude_executable),
        MockRuntime(),
    ]
    if generic_command:
        runtimes.append(GenericCliRuntime(generic_command))
    return runtimes


def build_runtime_registry(**kwargs: Any) -> RuntimeRegistry:
    registry = RuntimeRegistry()
    registry.register_many(default_runtimes(**kwargs))
    return registry


def select_runtime(
    registry: RuntimeRegistry,
    *,
    runtime_name: str | None = None,
    tier: str = "E3",
    model_info: Mapping[str, Any] | None = None,
    model: str = "",
) -> Any:
    """Pick a runtime and enforce tier capabilities before it is used."""
    if runtime_name:
        runtime = registry.get(runtime_name)
        assert_capabilities(runtime, tier=tier, model_info=model_info, model=model)
        return runtime
    candidates = registry.requiring(
        **{key: True for key in tier_requirements(tier)}
    )
    if not candidates:
        raise CapabilityRejected(
            f"no runtime satisfies tier {str(tier).upper()} requirements: "
            f"{tier_requirements(tier)}"
        )
    runtime = candidates[0]
    assert_capabilities(runtime, tier=tier, model_info=model_info, model=model)
    return runtime


class RuntimeCoderAdapter:
    """``CoderBackend`` over the F04 runtime protocol."""

    def __init__(self, runtime: Any, *, model: str = "", tier: str = "E3"):
        self.runtime = runtime
        self.model = str(model or "")
        self.tier = str(tier or "E3")

    def propose(
        self,
        work_order: Mapping[str, Any],
        *,
        cwd: Path,
        round_index: int,
        feedback: str | None,
    ) -> dict[str, str]:
        handle = self.runtime.start_task(
            dict(work_order), worktree=cwd, model=self.model, tier=self.tier,
        )
        if feedback:
            self.runtime.send_message(handle, str(feedback))
        result = self.runtime.wait(handle)
        if result.status != "ok":
            raise ExecutionError(
                f"runtime {self.runtime.name} failed: {result.error or result.status}"
            )
        if not result.files:
            raise ExecutionError(
                f"runtime {self.runtime.name} returned no file changes"
            )
        return {
            str(key): str(value) for key, value in result.files.items()
        }

    def cancel(self, handle: str, *, reason: str = "") -> dict[str, Any]:
        return self.runtime.cancel(handle, reason=reason)


def _work_order(order: Mapping[str, Any]):
    import work_order  # type: ignore

    return work_order.WorkOrder(
        objective=str(order.get("objective") or ""),
        boundaries=str(order.get("boundaries") or ""),
        input_context=str(order.get("input_context") or ""),
        acceptance_criteria=str(order.get("acceptance_criteria") or ""),
        verify_command=str(order.get("verify_command") or ""),
        task_id=str(order.get("id") or ""),
        title=str(order.get("title") or ""),
        level=int(order.get("level", 1)),
        target_files=[str(item) for item in (order.get("target_files") or [])],
        sandbox_files=[str(item) for item in (order.get("sandbox_files") or [])],
        max_rounds=int(order.get("max_rounds", 1)),
    )


def _current_files(worktree: Path, order: Mapping[str, Any]) -> dict[str, str]:
    files: dict[str, str] = {}
    for relative in order.get("target_files") or []:
        path = (worktree / str(relative)).resolve()
        try:
            path.relative_to(worktree.resolve())
        except ValueError as exc:
            raise ExecutionError(f"target escapes worktree: {relative}") from exc
        files[str(relative)] = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    return files


def _normalize_patch(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("content", "new_content", "text"):
            if key in value:
                return str(value[key])
        raise ExecutionError("worker returned a patch without content")
    raise ExecutionError(f"unsupported worker result: {type(value).__name__}")


class WorkersCoderAdapter:
    """Wrap an existing workers.Coder and return proposed file contents."""

    def __init__(
        self,
        model: str,
        *,
        kind: str = "agentic",
        sandbox_root: str | Path,
        timeout: int = 300,
    ):
        from workers import AgenticCoder, OpenCodeCoder  # type: ignore

        self.model = str(model)
        self.kind = kind
        self.sandbox_root = Path(sandbox_root).resolve()
        self.sandbox_root.mkdir(parents=True, exist_ok=True)
        if kind == "opencode":
            self.coder = OpenCodeCoder(self.model, timeout=timeout)
        else:
            self.coder = AgenticCoder(self.model, timeout=timeout)

    def propose(
        self,
        work_order: Mapping[str, Any],
        *,
        cwd: Path,
        round_index: int,
        feedback: str | None,
    ) -> dict[str, str]:
        order = _work_order(work_order)
        files = _current_files(cwd, work_order)
        if hasattr(self.coder, "bind_workspace"):
            self.coder.bind_workspace(cwd, sandbox=self.sandbox_root)
        try:
            raw = self.coder.patch_set(
                order,
                current_files=files,
                feedback=feedback,
                round_index=round_index,
            )
        except Exception as exc:  # noqa: BLE001
            raise ExecutionError(f"coder failed: {str(exc)[:500]}") from exc
        result: dict[str, str] = {}
        for relative, value in (raw or {}).items():
            content = _normalize_patch(value)
            if content is not None and content != files.get(str(relative)):
                result[str(relative)] = content
        if not result:
            raise ExecutionError("coder did not change any target file")
        return result


class WorkersReviewerAdapter:
    """Wrap workers.RoleReviewer and normalize its verdict to M3 findings."""

    def __init__(self, role: str, model: str, *, timeout: int = 240):
        from workers import RoleReviewer  # type: ignore

        self.role = str(role)
        self.model = str(model)
        self.reviewer = RoleReviewer(self.role, self.model, timeout=timeout)

    def review(
        self,
        role: str,
        *,
        diff: str,
        work_order: Mapping[str, Any],
        phase: Mapping[str, Any],
    ) -> dict[str, Any]:
        order = _work_order(work_order)
        try:
            payload = self.reviewer.review(
                order,
                diff=diff,
                new_content="",
                old_content="",
                level=int(work_order.get("level", 1)),
                sink=None,
            )
        except Exception as exc:  # noqa: BLE001
            raise ExecutionError(f"reviewer {role} failed: {str(exc)[:500]}") from exc
        verdict = str(payload.get("verdict") or "error").lower()
        reasons = [str(item) for item in (payload.get("reasons") or [])]
        if verdict == "error":
            raise ReviewerUnavailable(
                f"reviewer {role} infrastructure failure: "
                + "; ".join(reasons)[:500]
            )
        if verdict not in {"approve", "reject"}:
            verdict = "reject"
        if verdict == "reject":
            severity = "blocker" if role == "security" else "high"
        else:
            severity = "low"
        return {
            "verdict": verdict,
            "severity": severity,
            "evidence": reasons,
            "required_change": reasons[0] if reasons else "",
            "model": self.model,
        }


def build_worker_seats(
    *,
    coder_model: str,
    reviewer_models: Mapping[str, str],
    sandbox_root: str | Path,
    coder_kind: str = "agentic",
    timeout: int = 300,
    config: Mapping[str, Any] | None = None,
    paid_cost_usd: float = 0.0,
    free_only: bool = False,
    runtime: Any | None = None,
    runtime_name: str | None = None,
    tier: str = "E3",
) -> tuple[ExecutionSeat, dict[str, ExecutionSeat]]:
    if config is not None:
        validate_explicit(coder_model, reviewer_models, config, free_only=free_only)
    if runtime is None and runtime_name:
        registry = build_runtime_registry()
        model_info = (config or {}).get("models", {}).get(coder_model) \
            if isinstance(config, Mapping) else None
        runtime = select_runtime(
            registry, runtime_name=runtime_name, tier=tier,
            model_info=model_info if isinstance(model_info, Mapping) else None,
            model=coder_model,
        )
    if runtime is not None:
        coder_backend = RuntimeCoderAdapter(
            runtime, model=coder_model, tier=tier,
        )
    else:
        coder_backend = WorkersCoderAdapter(
            coder_model, kind=coder_kind, sandbox_root=sandbox_root,
            timeout=timeout,
        )
    coder_free = is_free(coder_model, config or {})
    coder = ExecutionSeat(
        "coder",
        coder_model,
        model_family(coder_model),
        coder_backend,
        is_free=coder_free,
        cost_usd=0.0 if coder_free else max(0.0, float(paid_cost_usd)),
    )
    reviewers: dict[str, ExecutionSeat] = {}
    for role, model in reviewer_models.items():
        free = is_free(str(model), config or {})
        reviewers[str(role)] = ExecutionSeat(
            str(role),
            str(model),
            model_family(str(model)),
            WorkersReviewerAdapter(str(role), str(model), timeout=timeout),
            is_free=free,
            cost_usd=0.0 if free else max(0.0, float(paid_cost_usd)),
        )
    return coder, reviewers


def free_reviewer_models() -> dict[str, str]:
    return {role: FREE_MODELS[role] for role in ("security", "qa", "perf", "docs")}


def free_seats_from_config(config: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
    return select_free_seats(config)
