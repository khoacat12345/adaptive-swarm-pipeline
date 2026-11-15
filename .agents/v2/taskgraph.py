#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dependency graph for v2 tasks (F01).

The plan is a document; the :class:`TaskGraph` is the executable view of that
document.  It owns the rules that are easy to get wrong in the runner:

* ids are unique and every dependency must exist;
* there is no self dependency and no cycle;
* ordering is deterministic (plan order first, then id) so two identical plans
  always produce the same dispatch order and the same evidence;
* phases can be expanded into their work orders so the scheduler can fan out
  independent work and fan in at the phase boundary.

The module is read-only: it never calls a model, a shell or Git.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .errors import V2Error

KIND_PHASE = "phase"
KIND_WORK_ORDER = "work_order"
KIND_TASK = "task"


class TaskGraphError(V2Error):
    """The graph itself is inconsistent and must not be dispatched."""

    code = "TASK_GRAPH_INVALID"


def _string_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, Iterable):
        return ()
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


@dataclass
class Task:
    """One node in the executable graph.

    ``priority`` is the plan-order rank: lower values are dispatched first when
    two nodes are otherwise ready at the same time.  ``id`` is the final
    tie-break, which keeps the scheduler deterministic across processes.
    """

    id: str
    title: str = ""
    depends_on: tuple[str, ...] = ()
    kind: str = KIND_TASK
    phase_id: str | None = None
    work_order_id: str | None = None
    priority: int = 0
    payload: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.id = str(self.id or "").strip()
        if not self.id:
            raise TaskGraphError("task id is required")
        self.depends_on = _string_list(self.depends_on)
        self.title = str(self.title or "")
        self.kind = str(self.kind or KIND_TASK)
        if self.phase_id is not None:
            self.phase_id = str(self.phase_id)
        if self.work_order_id is not None:
            self.work_order_id = str(self.work_order_id)
        self.priority = int(self.priority or 0)
        if not isinstance(self.payload, dict):
            self.payload = dict(self.payload or {})

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, index: int = 0) -> "Task":
        if not isinstance(data, Mapping):
            raise TaskGraphError(f"task {index} must be an object")
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            depends_on=tuple(_string_list(data.get("depends_on") or data.get("deps"))),
            kind=str(data.get("kind") or KIND_TASK),
            phase_id=data.get("phase_id"),
            work_order_id=data.get("work_order_id") or data.get("work_id"),
            priority=int(data.get("priority", index) or 0),
            payload=dict(data.get("payload") or {}),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "depends_on": list(self.depends_on),
            "kind": self.kind,
            "phase_id": self.phase_id,
            "work_order_id": self.work_order_id,
            "priority": self.priority,
        }


class TaskGraph:
    """A deterministic DAG of :class:`Task` nodes."""

    def __init__(self, tasks: Iterable[Any] | None = None):
        self._tasks: dict[str, Task] = {}
        if tasks:
            for index, item in enumerate(tasks):
                if isinstance(item, Task):
                    self.add(item)
                else:
                    self.add(Task.from_mapping(item, index=index))

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def add(self, task: Task, *, replace: bool = False) -> Task:
        if not isinstance(task, Task):
            raise TaskGraphError("add() expects a Task")
        if task.id in self._tasks and not replace:
            raise TaskGraphError(f"duplicate task id: {task.id}")
        self._tasks[task.id] = task
        return task

    def add_task(self, task_id: str, *, depends_on: Iterable[str] = (),
                 **kwargs: Any) -> Task:
        return self.add(Task(id=str(task_id), depends_on=_string_list(depends_on),
                             **kwargs))

    def remove(self, task_id: str) -> None:
        task_id = str(task_id)
        self._tasks.pop(task_id, None)
        for task in self._tasks.values():
            if task_id in task.depends_on:
                task.depends_on = tuple(
                    dep for dep in task.depends_on if dep != task_id
                )

    def get(self, task_id: str) -> Task:
        try:
            return self._tasks[str(task_id)]
        except KeyError as exc:
            raise TaskGraphError(f"unknown task: {task_id}") from exc

    def __contains__(self, task_id: object) -> bool:
        return str(task_id) in self._tasks

    def __iter__(self):
        return iter(self.topological_order())

    def __len__(self) -> int:
        return len(self._tasks)

    @property
    def size(self) -> int:
        return len(self._tasks)

    @property
    def ids(self) -> list[str]:
        return list(self._tasks)

    def tasks(self) -> list[Task]:
        return list(self._tasks.values())

    # ------------------------------------------------------------------
    # Deterministic ordering
    # ------------------------------------------------------------------
    @staticmethod
    def _key(task: Task) -> tuple[int, str]:
        return (int(task.priority), task.id)

    def _children(self) -> dict[str, list[str]]:
        children: dict[str, list[str]] = {task_id: [] for task_id in self._tasks}
        for task in self._tasks.values():
            for dep in task.depends_on:
                if dep in children:
                    children[dep].append(task.id)
        for values in children.values():
            values.sort(key=lambda task_id: self._key(self._tasks[task_id]))
        return children

    def validate(self) -> "TaskGraph":
        problems: list[str] = []
        for task in self._tasks.values():
            for dep in task.depends_on:
                if dep == task.id:
                    problems.append(f"{task.id}: cannot depend on itself")
                elif dep not in self._tasks:
                    problems.append(f"{task.id}: unknown dependency {dep}")
        if not problems:
            cycle = self.find_cycle()
            if cycle:
                problems.append("dependency cycle: " + " -> ".join(cycle))
        if problems:
            raise TaskGraphError("; ".join(problems[:12]))
        return self

    def find_cycle(self) -> list[str] | None:
        """Return one concrete cycle as a closed path, or ``None``."""
        visiting: list[str] = []
        visited: set[str] = set()
        index: dict[str, int] = {}

        def visit(node: str) -> list[str] | None:
            if node in visited:
                return None
            if node in index:
                position = index[node]
                return visiting[position:] + [node]
            index[node] = len(visiting)
            visiting.append(node)
            for dep in self._tasks[node].depends_on:
                if dep not in self._tasks:
                    continue
                found = visit(dep)
                if found:
                    return found
            visiting.pop()
            index.pop(node, None)
            visited.add(node)
            return None

        for task_id in sorted(self._tasks, key=lambda tid: self._key(self._tasks[tid])):
            found = visit(task_id)
            if found:
                return found
        return None

    def topological_order(self) -> list[Task]:
        """Kahn's algorithm with a deterministic priority queue."""
        self.validate()
        indegree = {
            task_id: len([
                dep for dep in task.depends_on if dep in self._tasks
            ])
            for task_id, task in self._tasks.items()
        }
        children = self._children()
        ready: list[tuple[int, str]] = [
            self._key(self._tasks[task_id]) for task_id, degree in indegree.items()
            if degree == 0
        ]
        heapq.heapify(ready)
        order: list[Task] = []
        while ready:
            _, task_id = heapq.heappop(ready)
            order.append(self._tasks[task_id])
            for child in children[task_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    heapq.heappush(ready, self._key(self._tasks[child]))
        if len(order) != len(self._tasks):
            cycle = self.find_cycle()
            raise TaskGraphError(
                "dependency cycle: " + (" -> ".join(cycle) if cycle else "unknown")
            )
        return order

    def levels(self) -> list[list[str]]:
        """Fan-out waves: every task in wave *n* depends only on waves < *n*."""
        self.validate()
        level_of: dict[str, int] = {}

        def level(task_id: str) -> int:
            if task_id in level_of:
                return level_of[task_id]
            deps = [dep for dep in self._tasks[task_id].depends_on if dep in self._tasks]
            value = 0 if not deps else 1 + max(level(dep) for dep in deps)
            level_of[task_id] = value
            return value

        for task_id in self._tasks:
            level(task_id)
        waves: dict[int, list[str]] = {}
        for task_id, value in level_of.items():
            waves.setdefault(value, []).append(task_id)
        return [
            sorted(waves[index], key=lambda tid: self._key(self._tasks[tid]))
            for index in sorted(waves)
        ]

    def topological_ids(self) -> list[str]:
        return [task.id for task in self.topological_order()]

    # ------------------------------------------------------------------
    # Graph queries used by the scheduler
    # ------------------------------------------------------------------
    def roots(self) -> list[str]:
        return sorted(
            (task.id for task in self._tasks.values() if not task.depends_on),
            key=lambda tid: self._key(self._tasks[tid]),
        )

    def leaves(self) -> list[str]:
        parents = {dep for task in self._tasks.values() for dep in task.depends_on}
        return sorted(
            (task_id for task_id in self._tasks if task_id not in parents),
            key=lambda tid: self._key(self._tasks[tid]),
        )

    def parents(self, task_id: str) -> list[str]:
        return list(self.get(task_id).depends_on)

    def children(self, task_id: str) -> list[str]:
        return list(self._children().get(str(task_id), []))

    def ancestors(self, task_id: str) -> list[str]:
        found: set[str] = set()
        stack = list(self.get(task_id).depends_on)
        while stack:
            current = stack.pop()
            if current in found or current not in self._tasks:
                continue
            found.add(current)
            stack.extend(self._tasks[current].depends_on)
        return sorted(found, key=lambda tid: self._key(self._tasks[tid]))

    def descendants(self, task_id: str) -> list[str]:
        children = self._children()
        found: set[str] = set()
        stack = list(children.get(str(task_id), []))
        while stack:
            current = stack.pop()
            if current in found:
                continue
            found.add(current)
            stack.extend(children.get(current, []))
        return sorted(found, key=lambda tid: self._key(self._tasks[tid]))

    def fanout_points(self) -> list[dict[str, Any]]:
        """Tasks whose completion unblocks more than one task."""
        children = self._children()
        rows = []
        for task_id in sorted(self._tasks, key=lambda tid: self._key(self._tasks[tid])):
            if len(children[task_id]) > 1:
                rows.append({"id": task_id, "children": list(children[task_id])})
        return rows

    def join_points(self) -> list[dict[str, Any]]:
        """Tasks that wait for more than one dependency (fan-in)."""
        rows = []
        for task_id in sorted(self._tasks, key=lambda tid: self._key(self._tasks[tid])):
            deps = self._tasks[task_id].depends_on
            if len(deps) > 1:
                rows.append({"id": task_id, "parents": list(deps)})
        return rows

    def ready(self, completed: Iterable[str] = (), *,
              running: Iterable[str] = ()) -> list[str]:
        """Tasks that may be dispatched now, in deterministic order."""
        done = {str(item) for item in completed}
        busy = {str(item) for item in running}
        result = []
        for task in self.topological_order():
            if task.id in done or task.id in busy:
                continue
            if all(dep in done for dep in task.depends_on):
                result.append(task.id)
        return result

    def blocked(self, failed: Iterable[str] = ()) -> list[str]:
        """Every task downstream of a failed task can never run."""
        blocked: set[str] = set()
        for task_id in failed:
            tid = str(task_id)
            if tid not in self._tasks:
                raise TaskGraphError(f"unknown task: {tid}")
            blocked.update(self.descendants(tid))
            blocked.add(tid)
        return sorted(blocked, key=lambda tid: self._key(self._tasks[tid]))

    # ------------------------------------------------------------------
    # Plan bridge
    # ------------------------------------------------------------------
    @classmethod
    def from_plan(cls, plan: Mapping[str, Any], *,
                  include_work_orders: bool = False) -> "TaskGraph":
        """Build a phase graph, or a fine-grained work-order graph."""
        if not isinstance(plan, Mapping):
            raise TaskGraphError("plan must be an object")
        phases = [phase for phase in (plan.get("phases") or [])
                  if isinstance(phase, Mapping)]
        if not phases:
            raise TaskGraphError("plan has no phases")
        graph = cls()
        phase_ids: list[str] = []
        for phase_index, phase in enumerate(phases):
            phase_id = str(phase.get("id") or "").strip()
            if not phase_id:
                raise TaskGraphError(f"phase {phase_index} has no id")
            if phase_id in phase_ids:
                raise TaskGraphError(f"duplicate phase id: {phase_id}")
            phase_ids.append(phase_id)
            graph.add_task(
                phase_id,
                title=str(phase.get("title") or phase_id),
                depends_on=phase.get("depends_on") or (),
                kind=KIND_PHASE,
                phase_id=phase_id,
                priority=phase_index,
                payload=dict(phase),
            )
        known_phases = set(phase_ids)
        for phase in phases:
            for dep in _string_list(phase.get("depends_on")):
                if dep not in known_phases:
                    raise TaskGraphError(
                        f"{phase.get('id')}: unknown phase dependency {dep}"
                    )
        if not include_work_orders:
            return graph.validate()

        graph = cls()
        phase_nodes: dict[str, list[str]] = {}
        for phase_index, phase in enumerate(phases):
            phase_id = str(phase["id"])
            orders = [order for order in (phase.get("work_orders") or [])
                      if isinstance(order, Mapping)]
            local: list[str] = []
            for order_index, order in enumerate(orders):
                work_id = str(order.get("id") or f"W{order_index + 1}")
                node_id = _work_node_id(phase_id, work_id)
                if node_id in graph:
                    raise TaskGraphError(f"duplicate work order id: {node_id}")
                deps = []
                for dep in _string_list(order.get("depends_on")):
                    deps.append(_resolve_work_dependency(phase_id, dep, orders))
                graph.add_task(
                    node_id,
                    title=str(order.get("objective") or work_id),
                    depends_on=deps,
                    kind=KIND_WORK_ORDER,
                    phase_id=phase_id,
                    work_order_id=work_id,
                    priority=phase_index * 1000 + order_index,
                    payload=dict(order),
                )
                local.append(node_id)
            if not local:
                # A phase with no work order is still a schedulable node so
                # downstream phases do not silently lose their dependency.
                # Phase-to-phase edges are wired below from the upstream
                # phases' leaf work orders; keeping this node dependency-free
                # here avoids referencing phase ids that are not graph nodes
                # in work-order mode.
                graph.add_task(
                    phase_id,
                    title=str(phase.get("title") or phase_id),
                    depends_on=(),
                    kind=KIND_PHASE,
                    phase_id=phase_id,
                    priority=phase_index,
                    payload=dict(phase),
                )
                local = [phase_id]
            phase_nodes[phase_id] = local

        # Fan-in: a phase's entry work orders wait for every leaf work order of
        # the phases it depends on.
        for phase in phases:
            phase_id = str(phase["id"])
            parents = _string_list(phase.get("depends_on"))
            if not parents:
                continue
            graph.validate()
            upstream: list[str] = []
            for parent in parents:
                nodes = phase_nodes.get(parent) or []
                upstream.extend([
                    node for node in nodes if not graph.children(node)
                ])
            for node in phase_nodes[phase_id]:
                task = graph.get(node)
                if task.depends_on:
                    continue
                graph.add(
                    Task(
                        id=task.id,
                        title=task.title,
                        depends_on=tuple(sorted(set(upstream))),
                        kind=task.kind,
                        phase_id=task.phase_id,
                        work_order_id=task.work_order_id,
                        priority=task.priority,
                        payload=task.payload,
                    ),
                    replace=True,
                )
        return graph.validate()

    def summary(self) -> dict[str, Any]:
        """Small read-only counts for status/report projections."""
        try:
            waves = self.levels()
            cycle = None
        except TaskGraphError as exc:
            waves = []
            cycle = str(exc)
        kinds: dict[str, int] = {}
        for task in self._tasks.values():
            kinds[task.kind] = kinds.get(task.kind, 0) + 1
        return {
            "size": len(self._tasks),
            "kinds": kinds,
            "waves": len(waves),
            "roots": len(self.roots()) if not cycle else 0,
            "leaves": len(self.leaves()) if not cycle else 0,
            "join_points": len(self.join_points()) if not cycle else 0,
            "fanout_points": len(self.fanout_points()) if not cycle else 0,
            "cycle": cycle,
        }

    # ------------------------------------------------------------------
    def as_dict(self) -> dict[str, Any]:
        try:
            waves = self.levels()
            order = self.topological_ids()
            cycle = None
        except TaskGraphError as exc:
            waves = []
            order = []
            cycle = str(exc)
        return {
            "schema_version": "2.1",
            "size": len(self._tasks),
            "tasks": [task.as_dict() for task in self.topological_order()]
            if not cycle else [task.as_dict() for task in self._tasks.values()],
            "waves": waves,
            "topological_order": order,
            "roots": self.roots(),
            "leaves": self.leaves(),
            "fanout_points": self.fanout_points(),
            "join_points": self.join_points(),
            "cycle": cycle,
        }


def _work_node_id(phase_id: str, work_id: str) -> str:
    cleaned = str(work_id or "").strip() or "work"
    return f"{phase_id}/{cleaned}"


def _resolve_work_dependency(phase_id: str, dep: str, orders: list[Mapping[str, Any]]) -> str:
    text = str(dep or "").strip()
    if not text:
        raise TaskGraphError("empty work order dependency")
    known = {str(order.get("id") or f"W{index + 1}")
             for index, order in enumerate(orders)}
    if text in known:
        return _work_node_id(phase_id, text)
    if "/" in text and text.split("/", 1)[0] == phase_id:
        return text
    raise TaskGraphError(f"{phase_id}: unknown work order dependency {text}")


def build_task_graph(plan: Mapping[str, Any], *,
                     include_work_orders: bool = False) -> TaskGraph:
    return TaskGraph.from_plan(plan, include_work_orders=include_work_orders)
