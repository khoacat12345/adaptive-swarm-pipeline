#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 1 (F01) acceptance tests for the task graph and scheduler.

Run directly:

    python .agents/tests/test_v2_taskgraph.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2.scheduler import (  # noqa: E402
    TASK_BLOCKED,
    TASK_PAUSED,
    TASK_READY,
    TASK_RUNNING,
    ScheduleError,
    Scheduler,
)
from v2.taskgraph import (  # noqa: E402
    KIND_PHASE,
    KIND_WORK_ORDER,
    Task,
    TaskGraph,
    TaskGraphError,
    build_task_graph,
)


def make_graph(rows: list[dict], *, priorities: dict[str, int] | None = None) -> TaskGraph:
    """Build a graph from ``{id, depends_on}`` rows in a stable order."""
    graph = TaskGraph()
    for index, row in enumerate(rows):
        payload = dict(row)
        task_id = str(payload.pop("id"))
        payload.setdefault("priority", (priorities or {}).get(task_id, index))
        graph.add(Task(id=task_id, **payload))
    return graph


class TaskModelTests(unittest.TestCase):
    def test_task_requires_non_empty_id(self) -> None:
        with self.assertRaises(TaskGraphError):
            Task(id="")
        with self.assertRaises(TaskGraphError):
            Task(id="   ")

    def test_dependencies_are_trimmed_and_deduplicated(self) -> None:
        task = Task(id="A", depends_on=[" B ", "B", "", None, "C", "C"])
        self.assertEqual(task.depends_on, ("B", "C"))

    def test_mapping_accepts_deps_alias_and_default_priority(self) -> None:
        task = Task.from_mapping(
            {"id": "A", "deps": ["B"], "title": "alpha"}, index=7
        )
        self.assertEqual(task.id, "A")
        self.assertEqual(task.depends_on, ("B",))
        self.assertEqual(task.priority, 7)
        self.assertEqual(task.as_dict()["title"], "alpha")

    def test_task_as_dict_contains_identity_and_dependencies(self) -> None:
        task = Task(
            id="A",
            title="Alpha",
            depends_on=["B"],
            kind=KIND_WORK_ORDER,
            phase_id="P1",
            work_order_id="W1",
            priority=3,
        )
        row = task.as_dict()
        self.assertEqual(row["id"], "A")
        self.assertEqual(row["depends_on"], ["B"])
        self.assertEqual(row["kind"], KIND_WORK_ORDER)
        self.assertEqual(row["phase_id"], "P1")
        self.assertEqual(row["work_order_id"], "W1")
        self.assertEqual(row["priority"], 3)

    def test_bad_mapping_is_rejected(self) -> None:
        with self.assertRaises(TaskGraphError):
            Task.from_mapping("not-a-mapping")  # type: ignore[arg-type]


class TaskGraphValidationTests(unittest.TestCase):
    def test_duplicate_ids_are_rejected(self) -> None:
        graph = TaskGraph([{"id": "A"}])
        with self.assertRaises(TaskGraphError):
            graph.add_task("A")

    def test_replace_existing_task_is_allowed(self) -> None:
        graph = TaskGraph([{"id": "A", "title": "old"}])
        graph.add(Task(id="A", title="new"), replace=True)
        self.assertEqual(graph.get("A").title, "new")

    def test_unknown_dependency_is_rejected(self) -> None:
        graph = make_graph([{"id": "A", "depends_on": ["missing"]}])
        with self.assertRaises(TaskGraphError):
            graph.validate()

    def test_self_dependency_is_rejected(self) -> None:
        graph = make_graph([{"id": "A", "depends_on": ["A"]}])
        with self.assertRaises(TaskGraphError):
            graph.validate()

    def test_cycle_is_found_and_rejected(self) -> None:
        graph = make_graph(
            [
                {"id": "A", "depends_on": ["B"]},
                {"id": "B", "depends_on": ["C"]},
                {"id": "C", "depends_on": ["A"]},
            ]
        )
        cycle = graph.find_cycle()
        self.assertIsNotNone(cycle)
        self.assertEqual(cycle[0], cycle[-1])  # closed path
        self.assertEqual(set(cycle), {"A", "B", "C"})
        with self.assertRaises(TaskGraphError):
            graph.topological_order()

    def test_topological_order_is_deterministic_by_priority_then_id(self) -> None:
        graph = make_graph(
            [
                {"id": "C", "depends_on": ["A"]},
                {"id": "B", "depends_on": ["A"]},
                {"id": "A"},
            ],
            priorities={"A": 0, "B": 2, "C": 1},
        )
        self.assertEqual(graph.topological_ids(), ["A", "C", "B"])

    def test_levels_form_fan_out_and_fan_in_waves(self) -> None:
        graph = make_graph(
            [
                {"id": "A"},
                {"id": "B", "depends_on": ["A"]},
                {"id": "C", "depends_on": ["A"]},
                {"id": "D", "depends_on": ["B", "C"]},
            ]
        )
        self.assertEqual(graph.levels(), [["A"], ["B", "C"], ["D"]])

    def test_roots_and_leaves(self) -> None:
        graph = make_graph(
            [
                {"id": "A"},
                {"id": "B", "depends_on": ["A"]},
                {"id": "C", "depends_on": ["A"]},
            ]
        )
        self.assertEqual(graph.roots(), ["A"])
        self.assertEqual(graph.leaves(), ["B", "C"])

    def test_parents_children_ancestors_descendants(self) -> None:
        graph = make_graph(
            [
                {"id": "A"},
                {"id": "B", "depends_on": ["A"]},
                {"id": "C", "depends_on": ["A"]},
                {"id": "D", "depends_on": ["B", "C"]},
            ]
        )
        self.assertEqual(graph.parents("D"), ["B", "C"])
        self.assertEqual(graph.children("A"), ["B", "C"])
        self.assertEqual(graph.ancestors("D"), ["A", "B", "C"])
        self.assertEqual(graph.descendants("A"), ["B", "C", "D"])

    def test_fanout_and_join_points(self) -> None:
        graph = make_graph(
            [
                {"id": "A"},
                {"id": "B", "depends_on": ["A"]},
                {"id": "C", "depends_on": ["A"]},
                {"id": "D", "depends_on": ["B", "C"]},
            ]
        )
        self.assertEqual(
            graph.fanout_points(), [{"id": "A", "children": ["B", "C"]}]
        )
        self.assertEqual(
            graph.join_points(), [{"id": "D", "parents": ["B", "C"]}]
        )

    def test_ready_respects_completed_and_running_tasks(self) -> None:
        graph = make_graph(
            [
                {"id": "A"},
                {"id": "B", "depends_on": ["A"]},
                {"id": "C", "depends_on": ["A"]},
                {"id": "D", "depends_on": ["B", "C"]},
            ]
        )
        self.assertEqual(graph.ready(), ["A"])
        self.assertEqual(graph.ready(completed={"A"}), ["B", "C"])
        self.assertEqual(graph.ready(completed={"A"}, running={"B"}), ["C"])
        self.assertEqual(
            graph.ready(completed={"A", "B", "C"}), ["D"]
        )

    def test_failed_task_blocks_all_descendants(self) -> None:
        graph = make_graph(
            [
                {"id": "A"},
                {"id": "B", "depends_on": ["A"]},
                {"id": "C", "depends_on": ["B"]},
            ]
        )
        self.assertEqual(graph.blocked({"B"}), ["B", "C"])

    def test_blocked_rejects_unknown_task(self) -> None:
        graph = make_graph([{"id": "A"}])
        with self.assertRaises(TaskGraphError):
            graph.blocked({"missing"})

    def test_remove_drops_node_and_incoming_edges(self) -> None:
        graph = make_graph([{"id": "A"}, {"id": "B", "depends_on": ["A"]}])
        graph.remove("A")
        self.assertNotIn("A", graph)
        self.assertEqual(graph.get("B").depends_on, ())
        self.assertEqual(graph.roots(), ["B"])

    def test_summary_and_as_dict_are_json_ready(self) -> None:
        graph = make_graph(
            [
                {"id": "A"},
                {"id": "B", "depends_on": ["A"]},
            ]
        )
        summary = graph.summary()
        self.assertEqual(summary["size"], 2)
        self.assertEqual(summary["waves"], 2)
        self.assertEqual(summary["roots"], 1)
        self.assertEqual(summary["leaves"], 1)
        payload = graph.as_dict()
        self.assertEqual(payload["schema_version"], "2.1")
        self.assertEqual(payload["topological_order"], ["A", "B"])
        self.assertIsNone(payload["cycle"])

    def test_summary_reports_cycle_instead_of_raising(self) -> None:
        graph = make_graph(
            [
                {"id": "A", "depends_on": ["B"]},
                {"id": "B", "depends_on": ["A"]},
            ]
        )
        summary = graph.summary()
        self.assertIsNotNone(summary["cycle"])
        self.assertEqual(summary["waves"], 0)


class PlanBridgeTests(unittest.TestCase):
    @staticmethod
    def phase_plan() -> dict:
        return {
            "phases": [
                {
                    "id": "P1",
                    "title": "One",
                    "work_orders": [
                        {"id": "W1", "objective": "first"},
                        {"id": "W2", "objective": "second"},
                    ],
                },
                {
                    "id": "P2",
                    "title": "Two",
                    "depends_on": ["P1"],
                    "work_orders": [{"id": "W3", "objective": "third"}],
                },
            ]
        }

    def test_phase_graph_keeps_phase_dependencies(self) -> None:
        graph = TaskGraph.from_plan(self.phase_plan())
        self.assertEqual(graph.topological_ids(), ["P1", "P2"])
        self.assertEqual(graph.get("P2").depends_on, ("P1",))
        self.assertEqual(graph.get("P1").kind, KIND_PHASE)

    def test_work_order_graph_fans_in_from_upstream_leaves(self) -> None:
        graph = TaskGraph.from_plan(self.phase_plan(), include_work_orders=True)
        self.assertIn("P1/W1", graph)
        self.assertIn("P1/W2", graph)
        self.assertIn("P2/W3", graph)
        self.assertEqual(set(graph.get("P2/W3").depends_on), {"P1/W1", "P1/W2"})
        self.assertEqual(graph.get("P2/W3").kind, KIND_WORK_ORDER)

    def test_internal_work_order_dependency_is_preserved(self) -> None:
        plan = self.phase_plan()
        plan["phases"][0]["work_orders"][1]["depends_on"] = ["W1"]
        graph = TaskGraph.from_plan(plan, include_work_orders=True)
        self.assertEqual(graph.get("P1/W2").depends_on, ("P1/W1",))
        # P1/W1 is no longer a leaf, so P2/W3 waits for P1/W2 instead.
        self.assertEqual(graph.get("P2/W3").depends_on, ("P1/W2",))

    def test_phase_without_work_orders_is_still_a_node(self) -> None:
        plan = {
            "phases": [
                {
                    "id": "P1",
                    "work_orders": [{"id": "W1"}],
                },
                {"id": "P2", "depends_on": ["P1"]},
                {"id": "P3", "depends_on": ["P2"]},
            ]
        }
        graph = TaskGraph.from_plan(plan, include_work_orders=True)
        self.assertIn("P2", graph)
        self.assertIn("P3", graph)
        self.assertEqual(graph.get("P2").depends_on, ("P1/W1",))
        self.assertEqual(graph.get("P3").depends_on, ("P2",))

    def test_unknown_phase_dependency_is_rejected(self) -> None:
        plan = {
            "phases": [
                {"id": "P1", "work_orders": [{"id": "W1"}]},
                {"id": "P2", "depends_on": ["P9"], "work_orders": [{"id": "W2"}]},
            ]
        }
        with self.assertRaises(TaskGraphError):
            TaskGraph.from_plan(plan, include_work_orders=True)

    def test_duplicate_phase_id_is_rejected(self) -> None:
        plan = {
            "phases": [
                {"id": "P1", "work_orders": [{"id": "W1"}]},
                {"id": "P1", "work_orders": [{"id": "W2"}]},
            ]
        }
        with self.assertRaises(TaskGraphError):
            TaskGraph.from_plan(plan, include_work_orders=True)

    def test_unknown_work_order_dependency_is_rejected(self) -> None:
        plan = {
            "phases": [
                {
                    "id": "P1",
                    "work_orders": [{"id": "W1", "depends_on": ["W9"]}],
                }
            ]
        }
        with self.assertRaises(TaskGraphError):
            TaskGraph.from_plan(plan, include_work_orders=True)

    def test_empty_plan_is_rejected(self) -> None:
        with self.assertRaises(TaskGraphError):
            TaskGraph.from_plan({"phases": []})

    def test_build_task_graph_delegates(self) -> None:
        graph = build_task_graph(self.phase_plan(), include_work_orders=True)
        self.assertIn("P2/W3", graph)


class SchedulerSmokeTests(unittest.TestCase):
    """Small cross-check so the Wave 1 verify command covers the scheduler."""

    def test_scheduler_dispatches_roots_and_unblocks_dependents(self) -> None:
        graph = make_graph(
            [
                {"id": "A"},
                {"id": "B", "depends_on": ["A"]},
            ]
        )
        scheduler = Scheduler(graph, max_parallel=1)
        self.assertEqual(scheduler.waves(), [["A"], ["B"]])
        first = scheduler.next_batch()
        self.assertEqual([item.task_id for item in first], ["A"])
        self.assertEqual(scheduler.state_of("A"), TASK_RUNNING)
        scheduler.mark_done("A")
        second = scheduler.next_batch()
        self.assertEqual([item.task_id for item in second], ["B"])
        self.assertEqual(scheduler.state_of("B"), TASK_RUNNING)

    def test_scheduler_unknown_task_raises_schedule_error(self) -> None:
        scheduler = Scheduler(make_graph([{"id": "A"}]))
        with self.assertRaises(ScheduleError):
            scheduler.state_of("missing")

    def test_failure_blocks_downstream(self) -> None:
        graph = make_graph(
            [
                {"id": "A"},
                {"id": "B", "depends_on": ["A"]},
            ]
        )
        scheduler = Scheduler(graph)
        scheduler.mark_failed("A", reason="boom")
        self.assertEqual(scheduler.state_of("B"), TASK_BLOCKED)
        self.assertEqual(scheduler.blocked(), ["B"])
        self.assertEqual(scheduler.next_batch(), [])

    def test_pause_and_resume(self) -> None:
        scheduler = Scheduler(make_graph([{"id": "A"}]))
        scheduler.pause("A", reason="waiting for input")
        self.assertEqual(scheduler.state_of("A"), TASK_PAUSED)
        self.assertEqual(scheduler.next_batch(), [])
        scheduler.resume("A")
        self.assertEqual(scheduler.state_of("A"), TASK_READY)
        self.assertEqual(len(scheduler.next_batch()), 1)

    def test_mark_done_unverified_marks_failed(self) -> None:
        graph = make_graph(
            [
                {"id": "A"},
                {"id": "B", "depends_on": ["A"]},
            ]
        )
        scheduler = Scheduler(graph)
        scheduler.mark_done("A", verified=False)
        self.assertEqual(scheduler.state_of("A"), "FAILED")
        self.assertEqual(scheduler.state_of("B"), TASK_BLOCKED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
