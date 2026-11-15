#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 1 (F01) scheduler tests.

Run directly:

    python .agents/tests/test_v2_scheduler.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2.scheduler import (  # noqa: E402
    TASK_BLOCKED,
    TASK_DONE,
    TASK_FAILED,
    TASK_PAUSED,
    TASK_PENDING,
    TASK_READY,
    TASK_RUNNING,
    ScheduleError,
    Scheduler,
    next_ready,
    schedule_plan,
)
from v2.taskgraph import Task, TaskGraph  # noqa: E402


def graph(rows: list[dict]) -> TaskGraph:
    result = TaskGraph()
    for index, row in enumerate(rows):
        payload = dict(row)
        task_id = str(payload.pop("id"))
        payload.setdefault("priority", index)
        result.add(Task(id=task_id, **payload))
    return result


def gate(**kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


class SchedulerDispatchTests(unittest.TestCase):
    def test_waves_and_initial_states(self) -> None:
        scheduler = Scheduler(
            graph(
                [
                    {"id": "A"},
                    {"id": "B", "depends_on": ["A"]},
                    {"id": "C", "depends_on": ["A"]},
                    {"id": "D", "depends_on": ["B", "C"]},
                ]
            )
        )
        self.assertEqual(scheduler.waves(), [["A"], ["B", "C"], ["D"]])
        self.assertEqual(scheduler.state_of("A"), TASK_READY)
        self.assertEqual(scheduler.state_of("B"), TASK_PENDING)
        self.assertEqual(scheduler.ready(), ["A"])

    def test_max_parallel_bounds_dispatch(self) -> None:
        scheduler = Scheduler(
            graph([{"id": "A"}, {"id": "B"}, {"id": "C"}]),
            max_parallel=2,
        )
        first = scheduler.next_batch()
        self.assertEqual([item.task_id for item in first], ["A", "B"])
        self.assertEqual(scheduler.next_batch(), [])
        scheduler.mark_done("A")
        third = scheduler.next_batch()
        self.assertEqual([item.task_id for item in third], ["C"])
        self.assertEqual(scheduler.running(), ["B", "C"])

    def test_dependencies_unblock_only_after_all_parents_done(self) -> None:
        scheduler = Scheduler(
            graph(
                [
                    {"id": "A"},
                    {"id": "B", "depends_on": ["A"]},
                    {"id": "C", "depends_on": ["A"]},
                    {"id": "D", "depends_on": ["B", "C"]},
                ]
            ),
            max_parallel=4,
        )
        scheduler.next_batch()
        scheduler.mark_done("A")
        batch = scheduler.next_batch()
        self.assertEqual([item.task_id for item in batch], ["B", "C"])
        scheduler.mark_done("B")
        self.assertEqual(scheduler.next_batch(), [])
        scheduler.mark_done("C")
        self.assertEqual(
            [item.task_id for item in scheduler.next_batch()], ["D"]
        )

    def test_mark_running_increments_attempts(self) -> None:
        scheduler = Scheduler(graph([{"id": "A"}]))
        row = scheduler.mark_running("A", model="model-a")
        self.assertEqual(row["state"], TASK_RUNNING)
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["model"], "model-a")
        row = scheduler.mark_running("A")
        self.assertEqual(row["attempts"], 2)

    def test_mark_failed_blocks_descendants(self) -> None:
        scheduler = Scheduler(
            graph(
                [
                    {"id": "A"},
                    {"id": "B", "depends_on": ["A"]},
                    {"id": "C", "depends_on": ["B"]},
                ]
            )
        )
        scheduler.mark_failed("A", reason="bad")
        self.assertEqual(scheduler.state_of("B"), TASK_BLOCKED)
        self.assertEqual(scheduler.state_of("C"), TASK_BLOCKED)
        self.assertEqual(scheduler.blocked(), ["B", "C"])
        self.assertEqual(scheduler.next_batch(), [])

    def test_mark_done_unverified_marks_failed(self) -> None:
        scheduler = Scheduler(
            graph(
                [
                    {"id": "A"},
                    {"id": "B", "depends_on": ["A"]},
                ]
            )
        )
        scheduler.mark_done("A", verified=False)
        self.assertEqual(scheduler.state_of("A"), TASK_FAILED)
        self.assertEqual(scheduler.state_of("B"), TASK_BLOCKED)

    def test_pause_resume_and_terminal_guard(self) -> None:
        scheduler = Scheduler(graph([{"id": "A"}]))
        scheduler.pause("A", reason="needs reply")
        self.assertEqual(scheduler.state_of("A"), TASK_PAUSED)
        self.assertEqual(scheduler.next_batch(), [])
        scheduler.resume("A")
        self.assertEqual(scheduler.state_of("A"), TASK_READY)
        scheduler.mark_done("A")
        with self.assertRaises(ScheduleError):
            scheduler.pause("A")

    def test_resume_rejects_non_paused_task(self) -> None:
        scheduler = Scheduler(graph([{"id": "A"}]))
        with self.assertRaises(ScheduleError):
            scheduler.resume("A")

    def test_unknown_task_operations_raise_schedule_error(self) -> None:
        scheduler = Scheduler(graph([{"id": "A"}]))
        with self.assertRaises(ScheduleError):
            scheduler.state_of("missing")
        with self.assertRaises(ScheduleError):
            scheduler.mark_running("missing")

    def test_as_dict_counts_and_plan_shape(self) -> None:
        scheduler = Scheduler(
            graph(
                [
                    {"id": "A"},
                    {"id": "B", "depends_on": ["A"]},
                ]
            )
        )
        scheduler.next_batch()
        payload = scheduler.as_dict()
        self.assertEqual(payload["schema_version"], "2.1")
        self.assertEqual(payload["counts"][TASK_RUNNING], 1)
        self.assertEqual(payload["counts"][TASK_PENDING], 1)
        self.assertEqual(payload["plan"][0]["wave"], 1)
        self.assertEqual(payload["tasks"][0]["task_id"], "A")
        self.assertIn("quota", payload)


class SchedulerGateTests(unittest.TestCase):
    def test_gate_pause_keeps_task_out_of_queue(self) -> None:
        scheduler = Scheduler(
            graph([{"id": "A"}]),
            gate=SimpleNamespace(
                decide=lambda *args, **kwargs: gate(
                    action="pause",
                    ok=False,
                    model=None,
                    reason="quota exhausted",
                    evidence={"window": "month"},
                )
            ),
        )
        decisions = scheduler.next_batch()
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].action, "pause")
        self.assertFalse(decisions[0].ok)
        self.assertEqual(scheduler.state_of("A"), TASK_PAUSED)
        self.assertEqual(scheduler.next_batch(), [])
        evidence = scheduler.quota_evidence()
        self.assertEqual(evidence[0]["action"], "pause")
        self.assertEqual(evidence[0]["evidence"]["window"], "month")

    def test_gate_reassign_updates_model(self) -> None:
        def decide(model, **kwargs):
            return gate(
                action="reassign",
                ok=True,
                model="model-b",
                reason="model-a exhausted",
                evidence={"from": model},
            )

        scheduler = Scheduler(
            graph([{"id": "A"}]),
            model_for={"A": "model-a"},
            gate=SimpleNamespace(decide=decide),
        )
        decision = scheduler.next_batch()[0]
        self.assertEqual(decision.action, "reassign")
        self.assertEqual(decision.model, "model-b")
        self.assertEqual(scheduler.state_of("A"), TASK_RUNNING)
        self.assertEqual(scheduler.quota_evidence()[0]["model"], "model-b")

    def test_gate_skip_does_not_dispatch_or_pause(self) -> None:
        scheduler = Scheduler(
            graph([{"id": "A"}]),
            gate=SimpleNamespace(
                decide=lambda *args, **kwargs: gate(
                    action="skip", ok=True, model=None, reason="not now", evidence={}
                )
            ),
        )
        decisions = scheduler.next_batch()
        self.assertEqual(decisions[0].action, "skip")
        self.assertEqual(scheduler.state_of("A"), TASK_READY)
        self.assertEqual(scheduler.running(), [])

    def test_model_resolver_prefers_task_then_work_order_then_phase(self) -> None:
        scheduler = Scheduler(
            graph(
                [
                    {
                        "id": "A",
                        "work_order_id": "W1",
                        "phase_id": "P1",
                    },
                    {"id": "B", "work_order_id": "W2"},
                    {"id": "C"},
                ]
            ),
            models={"A": "by-task", "W1": "by-work", "P1": "by-phase"},
            default_model="fallback",
        )
        decisions = {item.task_id: item.model for item in scheduler.next_batch()}
        self.assertEqual(decisions["A"], "by-task")
        self.assertEqual(decisions["B"], "fallback")
        self.assertEqual(decisions["C"], "fallback")

    def test_model_resolver_can_be_callable(self) -> None:
        scheduler = Scheduler(
            graph([{"id": "A"}]),
            model_for=lambda task: f"model-{task.id.lower()}",
        )
        self.assertEqual(scheduler.next_batch()[0].model, "model-a")


class SchedulerHelpersTests(unittest.TestCase):
    def test_schedule_plan_builds_phase_scheduler(self) -> None:
        plan = {
            "phases": [
                {"id": "P1", "work_orders": [{"id": "W1"}]},
                {"id": "P2", "depends_on": ["P1"], "work_orders": [{"id": "W2"}]},
            ]
        }
        scheduler = schedule_plan(plan)
        self.assertEqual(scheduler.waves(), [["P1"], ["P2"]])
        self.assertEqual(scheduler.next_batch()[0].task_id, "P1")

    def test_next_ready_is_deterministic(self) -> None:
        task_graph = graph(
            [
                {"id": "A"},
                {"id": "B", "depends_on": ["A"]},
                {"id": "C", "depends_on": ["A"]},
            ]
        )
        self.assertEqual(next_ready(task_graph), ["A"])
        self.assertEqual(next_ready(task_graph, completed={"A"}), ["B", "C"])
        self.assertEqual(
            next_ready(task_graph, completed={"A"}, running={"B"}), ["C"]
        )

    def test_scheduler_rejects_non_graph(self) -> None:
        with self.assertRaises(ScheduleError):
            Scheduler({"not": "a graph"})  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main(verbosity=2)
