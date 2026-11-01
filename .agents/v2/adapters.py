#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M3.5 adapters: bridge existing OpenCode workers to the M3 execution protocol.

The bridge never writes project files itself. It only proposes file contents to
the Lead/ExecutionEngine, which applies them through safe_writer in the guarded
worktree.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .errors import ExecutionError
from .execution import ExecutionSeat, model_family

# Free-only defaults for the explicit ``--free`` path. They are still passed
# through the worker layer; callers can override every model.
FREE_MODELS = {
    "coder": "opencode/glm-5.3-flash-free",
    "security": "opencode/nemotron-3.5-super-vl-free",
    "qa": "opencode/deepseek-v4.1-flash-free",
    "perf": "opencode/mimo-v2.6-flash-free",
    "docs": "opencode/nemotron-3.5-lightning-free",
}


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
) -> tuple[ExecutionSeat, dict[str, ExecutionSeat]]:
    coder_backend = WorkersCoderAdapter(
        coder_model, kind=coder_kind, sandbox_root=sandbox_root, timeout=timeout
    )
    coder = ExecutionSeat("coder", coder_model, model_family(coder_model), coder_backend)
    reviewers: dict[str, ExecutionSeat] = {}
    for role, model in reviewer_models.items():
        reviewers[str(role)] = ExecutionSeat(
            str(role),
            str(model),
            model_family(str(model)),
            WorkersReviewerAdapter(str(role), str(model), timeout=timeout),
        )
    return coder, reviewers


def free_reviewer_models() -> dict[str, str]:
    return {role: FREE_MODELS[role] for role in ("security", "qa", "perf", "docs")}
