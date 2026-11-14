#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic task scheduler for v2 (F01 + F05).

The scheduler turns a :class:`~v2.taskgraph.TaskGraph` into dispatch waves and
tracks the state of every task.  Parallelism is expressed as a bounded set of
``RUNNING`` tasks; the order in which ready tasks are promoted is fully
deterministic (wave, plan priority, id).

A quota gate, when supplied, is consulted before a task leaves the queue.  The
scheduler never calls a model itself and never writes project files, so it is
safe to use from the runner, the CLI and read-only projections.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .errors import ExecutionError
from .taskgraph import Task, TaskGraph, TaskGraphError

TASK_PENDING = "PENDING"
TASK_READY = "READY"
TASK_RUNNING = "RUNNING"
TASK_DONE = "DONE"
TASK_FAILED = "FAILED"
TASK_PAUSED = "PAUSED"
TASK_BLOCKED = "BLOCKED"

TERMINAL_TASK_STATES = {TASK_DONE, TASK_FAILED, TASK_BLOCKED}
ACTIVE_TASK_STATES = {TASK_READY, TASK_RUNNING, TASK_PAUSED}


class ScheduleError(ExecutionError):
    code = "SCHEDULE_INVALID"


@dataclass
class DispatchDecision:
    """One dispatch decision, including the quota evidence that produced it."""

    task_id: str
    wave: int = 0
    model: str | None = None
    action: str = "dispatch"  # dispatch | reassign | pause | skip
    ok: bool = True
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "wave": self.wave,
            "model": self.model,
            "action": self.action,
            "ok": self.ok,
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }


def _as_model_resolver(
    model_for: Callable[[Task], str] | Mapping[str, str] | None,
    models: Mapping[str, str] | None,
    default_model: str | None,
) -> Callable[[Task], str | None]:
    if callable(model_for):
        return lambda task: str(model_for(task) or "") or None

    mapping: dict[str, str] = {}
    if isinstance(model_for, Mapping):
        mapping.update({str(key): str(value) for key, value in model_for.items()})
    if models:
        mapping.update({str(key): str(value) for key, value in models.items()})

    def resolve(task: Task) -> str | None:
        for key in (task.id, task.work_order_id, task.phase_id):
            if key and str(key) in mapping:
                return mapping[str(key)]
        return default_model

    return resolve


class Scheduler:
    """Bounded, deterministic dispatcher over a validated task graph."""

    def __init__(
        self,
        graph: TaskGraph,
        *,
        max_parallel: int = 4,
        gate: Any | None = None,
        model_for: Callable[[Task], str] | Mapping[str, str] | None = None,
        models: Mapping[str, str] | None = None,
        default_model: str | None = None,
        gate_candidates: Iterable[str] = (),
    ):
        if not isinstance(graph, TaskGraph):
            raise ScheduleError("Scheduler requires a TaskGraph")
        self.graph = graph.validate()
        self.max_parallel = max(1, int(max_parallel or 1))
        self.gate = gate
        self._resolve_model = _as_model_resolver(model_for, models, default_model)
        self.gate_candidates = tuple(str(item) for item in gate_candidates)
        self._waves = self.graph.levels()
        self._wave_of: dict[str, int] = {}
        for index, wave in enumerate(self._waves):
            for task_id in wave:
                self._wave_of[task_id] = index + 1
        self._states: dict[str, dict[str, Any]] = {
            task_id: {
                "task_id": task_id,
                "state": TASK_READY if not self.graph.get(task_id).depends_on
                else TASK_PENDING,
                "wave": self._wave_of.get(task_id, 0),
                "attempts": 0,
                "model": None,
                "reason": "",
            }
            for task_id in graph.ids
        }
        self.dispatched: list[DispatchDecision] = []
        self._paused: set[str] = set()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------
    @classmethod
    def for_plan(cls, plan: Mapping[str, Any], **kwargs: Any) -> "Scheduler":
        include_work_orders = bool(kwargs.pop("include_work_orders", False))
        graph = TaskGraph.from_plan(plan, include_work_orders=include_work_orders)
        return cls(graph, **kwargs)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def waves(self) -> list[list[str]]:
        return [list(wave) for wave in self._waves]

    def plan(self) -> list[dict[str, Any]]:
        rows = []
        for index, wave in enumerate(self._waves):
            rows.append({
                "wave": index + 1,
                "tasks": [
                    {
                        "task_id": task_id,
                        "title": self.graph.get(task_id).title,
                        "kind": self.graph.get(task_id).kind,
                        "priority": self.graph.get(task_id).priority,
                    }
                    for task_id in wave
                ],
            })
        return rows

    def state_of(self, task_id: str) -> str:
        return str(self._states[str(task_id)]["state"])

    def running(self) -> list[str]:
        return self._with_state(TASK_RUNNING)

    def completed(self) -> list[str]:
        return self._with_state(TASK_DONE)

    def failed(self) -> list[str]:
        return self._with_state(TASK_FAILED)

    def paused(self) -> list[str]:
        return self._with_state(TASK_PAUSED)

    def blocked(self) -> list[str]:
        return self._with_state(TASK_BLOCKED)

    def _with_state(self, state: str) -> list[str]:
        order = {task_id: index for index, task_id in enumerate(
            self.graph.topological_ids()
        )}
        return sorted(
            (task_id for task_id, row in self._states.items()
             if row["state"] == state),
            key=lambda task_id: (
                self._wave_of.get(task_id, 0),
                order.get(task_id, 0),
                task_id,
            ),
        )

    # ------------------------------------------------------------------
    # Queue
    # ------------------------------------------------------------------
    def _is_blocked(self, task_id: str) -> bool:
        return any(
            self._states.get(dep, {}).get("state") in {TASK_FAILED, TASK_BLOCKED}
            for dep in self.graph.parents(task_id)
        )

    def ready(self) -> list[str]:
        """Ready tasks in deterministic dispatch order."""
        done = set(self.completed())
        running = set(self.running())
        result: list[str] = []
        for task in self.graph.topological_order():
            task_id = task.id
            row = self._states[task_id]
            if row["state"] in TERMINAL_TASK_STATES | {TASK_RUNNING}:
                continue
            if task_id in self._paused:
                continue
            if all(dep in done for dep in task.depends_on):
                result.append(task_id)
        return sorted(
            result,
            key=lambda task_id: (
                self._wave_of.get(task_id, 0),
                self.graph.get(task_id).priority,
                task_id,
            ),
        )

    def refresh(self) -> None:
        """Promote pending tasks whose dependencies are all done."""
        done = set(self.completed())
        failed = set(self.failed()) | set(self.blocked())
        for task_id, row in self._states.items():
            if row["state"] in TERMINAL_TASK_STATES | {TASK_RUNNING, TASK_PAUSED}:
                continue
            deps = self.graph.parents(task_id)
            if any(dep in failed for dep in deps):
                row["state"] = TASK_BLOCKED
                row["reason"] = "upstream task failed"
            elif all(dep in done for dep in deps):
                row["state"] = TASK_READY
            else:
                row["state"] = TASK_PENDING

    def next_batch(self, *, limit: int | None = None,
                   dispatch: bool = True) -> list[DispatchDecision]:
        """Return (and by default dispatch) the next ready tasks.

        ``max_parallel`` bounds concurrently running tasks.  A quota gate can
        reassign the model or pause the task; either outcome is recorded as
        evidence for the run report.
        """
        self.refresh()
        capacity = self.max_parallel - len(self.running())
        if limit is not None:
            capacity = min(capacity, max(0, int(limit)))
        if capacity <= 0:
            return []
        decisions: list[DispatchDecision] = []
        for task_id in self.ready():
            if capacity <= 0:
                break
            task = self.graph.get(task_id)
            model = self._resolve_model(task)
            decision = DispatchDecision(
                task_id=task_id,
                wave=self._wave_of.get(task_id, 0),
                model=model,
            )
            if self.gate is not None:
                gate_decision = self.gate.decide(
                    model,
                    candidates=self.gate_candidates,
                    phase_id=task.phase_id,
                    task_id=task_id,
                )
                action = str(getattr(gate_decision, "action", "dispatch") or "dispatch")
                evidence = dict(getattr(gate_decision, "evidence", {}) or {})
                decision.action = action
                decision.ok = bool(getattr(gate_decision, "ok", action != "pause"))
                decision.reason = str(getattr(gate_decision, "reason", "") or "")
                decision.evidence = evidence
                chosen = getattr(gate_decision, "model", None)
                if chosen:
                    decision.model = str(chosen)
                if action == "pause":
                    self._paused.add(task_id)
                    self._states[task_id]["state"] = TASK_PAUSED
                    self._states[task_id]["reason"] = decision.reason or "quota paused"
                    decisions.append(decision)
                    continue
                if action == "skip":
                    decisions.append(decision)
                    continue
            self._states[task_id]["model"] = decision.model
            self._states[task_id]["state"] = TASK_RUNNING
            self._states[task_id]["attempts"] = int(
                self._states[task_id].get("attempts") or 0
            ) + 1
            self._states[task_id]["reason"] = ""
            self.dispatched.append(decision)
            decisions.append(decision)
            capacity -= 1
        return decisions

    # ------------------------------------------------------------------
    # Completion
    # ------------------------------------------------------------------
    def mark_running(self, task_id: str, *, model: str | None = None) -> dict[str, Any]:
        row = self._row(task_id)
        if row["state"] in TERMINAL_TASK_STATES:
            raise ScheduleError(f"cannot run terminal task {task_id}")
        row["state"] = TASK_RUNNING
        row["attempts"] = int(row.get("attempts") or 0) + 1
        if model:
            row["model"] = str(model)
        return row

    def mark_done(self, task_id: str, *, verified: bool = True) -> dict[str, Any]:
        row = self._row(task_id)
        if not verified:
            return self.mark_failed(task_id, reason="verification failed")
        row["state"] = TASK_DONE
        row["reason"] = ""
        self._paused.discard(str(task_id))
        self.refresh()
        return row

    def mark_failed(self, task_id: str, *, reason: str = "") -> dict[str, Any]:
        row = self._row(task_id)
        row["state"] = TASK_FAILED
        row["reason"] = str(reason or "failed")
        self._paused.discard(str(task_id))
        self.refresh()
        return row

    def pause(self, task_id: str, *, reason: str = "paused") -> dict[str, Any]:
        row = self._row(task_id)
        if row["state"] in TERMINAL_TASK_STATES:
            raise ScheduleError(f"cannot pause terminal task {task_id}")
        self._paused.add(str(task_id))
        row["state"] = TASK_PAUSED
        row["reason"] = str(reason or "paused")
        return row

    def resume(self, task_id: str) -> dict[str, Any]:
        row = self._row(task_id)
        if row["state"] != TASK_PAUSED:
            raise ScheduleError(f"task is not paused: {task_id}")
        self._paused.discard(str(task_id))
        row["state"] = TASK_READY
        row["reason"] = ""
        self.refresh()
        return row

    def _row(self, task_id: str) -> dict[str, Any]:
        try:
            return self._states[str(task_id)]
        except KeyError as exc:
            raise ScheduleError(f"unknown scheduled task: {task_id}") from exc

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------
    def quota_evidence(self) -> list[dict[str, Any]]:
        return [
            decision.as_dict() for decision in self.dispatched
            if decision.evidence or decision.action != "dispatch"
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "2.1",
            "max_parallel": self.max_parallel,
            "waves": self.waves(),
            "plan": self.plan(),
            "tasks": [
                dict(self._states[task_id], title=self.graph.get(task_id).title)
                for task_id in self.graph.topological_ids()
            ],
            "counts": {
                state: len(self._with_state(state))
                for state in (
                    TASK_PENDING, TASK_READY, TASK_RUNNING, TASK_DONE,
                    TASK_FAILED, TASK_PAUSED, TASK_BLOCKED,
                )
            },
            "dispatched": [decision.as_dict() for decision in self.dispatched],
            "quota": self.quota_evidence(),
        }


def schedule_plan(plan: Mapping[str, Any], **kwargs: Any) -> Scheduler:
    return Scheduler.for_plan(plan, **kwargs)


def build_quota_gate(root: str | Path | None = None, *, tracker: Any | None = None,
                     candidates: Iterable[str] = (),
                     reserve_pct: float = 15.0,
                     strict: bool | None = None,
                     refresh: bool = False) -> Any:
    """Build the F05 dispatch gate without importing heavy runner modules."""
    try:
        import quota as quota_module  # type: ignore
    except ImportError:  # pragma: no cover - package-relative fallback
        import sys
        from pathlib import Path as _Path

        sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
        import quota as quota_module  # type: ignore
    return quota_module.build_gate(
        root, tracker=tracker, candidates=candidates,
        reserve_pct=reserve_pct, strict=strict, refresh=refresh,
    )


def next_ready(graph: TaskGraph, completed: Iterable[str] = (), *,
               running: Iterable[str] = ()) -> list[str]:
    """Small functional helper for callers that do not need a scheduler."""
    try:
        return graph.ready(completed, running=running)
    except TaskGraphError as exc:  # pragma: no cover - defensive
        raise ScheduleError(str(exc)) from exc
