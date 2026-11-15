#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 3 (F02) acceptance tests for the attempt lifecycle.

Run directly:

    python .agents/tests/test_v2_lifecycle.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2 import attempts as attempt_mod  # noqa: E402
from v2.attempts import (  # noqa: E402
    ACTIVE,
    DISPATCHED,
    FAILED,
    IDLE,
    LOST,
    SETTLED,
    VERIFYING,
    WORKING,
    Attempt,
    AttemptError,
    AttemptHistory,
    RetryPolicy,
    assert_transition,
)
from unified import state as unified_state  # noqa: E402


def iso(seconds: float) -> str:
    return (
        datetime.fromtimestamp(seconds, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class AttemptModelTests(unittest.TestCase):
    def test_attempt_requires_task_id(self) -> None:
        with self.assertRaises(AttemptError):
            Attempt(attempt_id="att_x", task_id="")

    def test_unknown_state_is_rejected(self) -> None:
        with self.assertRaises(AttemptError):
            Attempt(attempt_id="att_x", task_id="T1", state="NOPE")
        with self.assertRaises(AttemptError):
            assert_transition(IDLE, "NOPE")

    def test_legal_transition_chain_to_settled(self) -> None:
        attempt = Attempt(attempt_id="att_x", task_id="T1")
        attempt.transition(DISPATCHED)
        attempt.transition(WORKING)
        attempt.transition(VERIFYING)
        attempt.transition(SETTLED)
        self.assertEqual(attempt.state, SETTLED)
        self.assertTrue(attempt.terminal)
        self.assertEqual(
            [row["state"] for row in attempt.history],
            [IDLE, DISPATCHED, WORKING, VERIFYING, SETTLED],
        )

    def test_illegal_skip_transitions_are_rejected(self) -> None:
        attempt = Attempt(attempt_id="att_x", task_id="T1")
        with self.assertRaises(AttemptError):
            attempt.transition(WORKING)  # IDLE -> WORKING
        attempt.transition(DISPATCHED)
        with self.assertRaises(AttemptError):
            attempt.transition(VERIFYING)  # DISPATCHED -> VERIFYING
        attempt.transition(WORKING)
        with self.assertRaises(AttemptError):
            attempt.transition(SETTLED)  # WORKING -> SETTLED

    def test_idle_cannot_fail_or_be_lost(self) -> None:
        """Only active states may fail/lose; an undispatched attempt is not active."""
        attempt = Attempt(attempt_id="att_x", task_id="T1")
        with self.assertRaises(AttemptError):
            attempt.transition(FAILED)
        with self.assertRaises(AttemptError):
            attempt.transition(LOST)

    def test_active_states_can_fail(self) -> None:
        for state in (DISPATCHED, WORKING, VERIFYING):
            attempt = Attempt(attempt_id=f"att_{state}", task_id="T1", state=state)
            attempt.transition(FAILED, reason="boom")
            self.assertEqual(attempt.state, FAILED)
            self.assertTrue(attempt.terminal)
            self.assertEqual(attempt.reason, "boom")

    def test_active_states_can_be_lost(self) -> None:
        for state in (DISPATCHED, WORKING, VERIFYING):
            attempt = Attempt(attempt_id=f"att_{state}", task_id="T1", state=state)
            attempt.transition(LOST, reason="worker gone")
            self.assertEqual(attempt.state, LOST)
            self.assertTrue(attempt.terminal)

    def test_terminal_states_are_final(self) -> None:
        for terminal in (SETTLED, FAILED, LOST):
            attempt = Attempt(attempt_id=f"att_{terminal}", task_id="T1", state=terminal)
            for target in (DISPATCHED, WORKING, VERIFYING, SETTLED, FAILED, LOST):
                with self.assertRaises(AttemptError):
                    attempt.transition(target)

    def test_heartbeat_only_valid_for_active_attempts(self) -> None:
        attempt = Attempt(attempt_id="att_x", task_id="T1")
        with self.assertRaises(AttemptError):
            attempt.heartbeat()
        attempt.transition(DISPATCHED)
        stamp = iso(1_700_000_000)
        attempt.heartbeat(at=stamp)
        self.assertEqual(attempt.heartbeat_at, stamp)
        attempt.transition(WORKING)
        attempt.transition(VERIFYING)
        attempt.transition(SETTLED)
        with self.assertRaises(AttemptError):
            attempt.heartbeat()

    def test_attempt_round_trip_preserves_metadata(self) -> None:
        attempt = Attempt(
            attempt_id="att_x",
            task_id="T1",
            run_id="run1",
            phase_id="P1",
            worker="coder",
            model="model-a",
            state=DISPATCHED,
            number=2,
            metadata={"escalate_to": "model-b"},
        )
        restored = Attempt.from_dict(attempt.as_dict())
        self.assertEqual(restored.attempt_id, "att_x")
        self.assertEqual(restored.task_id, "T1")
        self.assertEqual(restored.state, DISPATCHED)
        self.assertEqual(restored.number, 2)
        self.assertEqual(restored.metadata["escalate_to"], "model-b")


class AttemptHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="attempts-")
        self.run_dir = Path(self.temp.name)
        self.history = AttemptHistory(self.run_dir)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_start_persists_and_is_append_only(self) -> None:
        attempt = self.history.start("T1", model="model-a", metadata={"source": "test"})
        self.assertEqual(attempt.state, DISPATCHED)
        self.assertEqual(attempt.task_id, "T1")
        self.assertEqual(attempt.model, "model-a")
        self.assertTrue((self.run_dir / "attempts.ndjson").exists())
        self.assertEqual(self.history.latest("T1").attempt_id, attempt.attempt_id)
        lines = [
            line for line in (self.run_dir / "attempts.ndjson").read_text(
                encoding="utf-8"
            ).splitlines() if line.strip()
        ]
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["state"], DISPATCHED)

    def test_start_without_dispatch_stays_idle(self) -> None:
        attempt = self.history.start("T1", dispatch=False)
        self.assertEqual(attempt.state, IDLE)
        self.assertEqual(attempt.history[0]["state"], IDLE)

    def test_persisted_history_survives_reload_and_skips_corrupt_lines(self) -> None:
        attempt = self.history.start("T1", model="model-a")
        self.history.transition(attempt.attempt_id, WORKING)
        with (self.run_dir / "attempts.ndjson").open("a", encoding="utf-8") as handle:
            handle.write("{not-json}\n")
            handle.write('{"attempt_id": "bad", "task_id": "T1", "state": "NOPE"}\n')

        reloaded = AttemptHistory(self.run_dir)
        latest = reloaded.get(attempt.attempt_id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.state, WORKING)
        self.assertEqual(reloaded.summary()["total"], 1)

    def test_illegal_transition_is_rejected_and_not_persisted(self) -> None:
        attempt = self.history.start("T1")
        with self.assertRaises(AttemptError):
            self.history.transition(attempt.attempt_id, VERIFYING)
        self.assertEqual(self.history.get(attempt.attempt_id).state, DISPATCHED)

    def test_heartbeat_updates_timestamp(self) -> None:
        attempt = self.history.start("T1")
        stamp = iso(1_700_000_000)
        self.history.heartbeat(attempt.attempt_id)
        before = self.history.get(attempt.attempt_id).heartbeat_at
        reloaded = AttemptHistory(self.run_dir)
        self.assertEqual(reloaded.get(attempt.attempt_id).heartbeat_at, before)
        self.assertNotEqual(before, stamp)

    def test_mark_lost_marks_only_stale_active_attempts(self) -> None:
        old = self.history.start("T-old", model="model-a")
        self.history.transition(old.attempt_id, WORKING)
        self.history.transition(old.attempt_id, VERIFYING)
        # Rewind the heartbeat by writing a controlled timestamp.
        old_record = self.history.get(old.attempt_id)
        old_record.heartbeat_at = iso(1_700_000_000)
        old_record.updated_at = old_record.heartbeat_at

        fresh = self.history.start("T-fresh", model="model-a")
        fresh_record = self.history.get(fresh.attempt_id)
        fresh_record.heartbeat_at = iso(1_700_000_080)
        fresh_record.updated_at = fresh_record.heartbeat_at

        lost = self.history.mark_lost(timeout_seconds=30, now=1_700_000_090)
        self.assertEqual([item.attempt_id for item in lost], [old.attempt_id])
        self.assertEqual(self.history.get(old.attempt_id).state, LOST)
        self.assertEqual(self.history.get(fresh.attempt_id).state, DISPATCHED)

    def test_mark_lost_does_not_touch_terminal_attempts(self) -> None:
        settled = self.history.start("T1")
        self.history.transition(settled.attempt_id, WORKING)
        self.history.transition(settled.attempt_id, VERIFYING)
        self.history.settle(settled.attempt_id)
        self.assertEqual(self.history.mark_lost(timeout_seconds=0, now=1_700_000_000), [])
        self.assertEqual(self.history.get(settled.attempt_id).state, SETTLED)

    def test_summary_counts_retries_states_and_items(self) -> None:
        first = self.history.start("T1", model="m1")
        self.history.transition(first.attempt_id, WORKING)
        self.history.transition(first.attempt_id, VERIFYING)
        self.history.settle(first.attempt_id)
        self.history.start("T1", model="m2")
        self.history.start("T2", model="m1")

        summary = self.history.summary()
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["retries"], 1)
        self.assertEqual(summary["by_state"][SETTLED], 1)
        self.assertEqual(summary["by_state"][DISPATCHED], 2)
        self.assertEqual(summary["active"], 2)
        self.assertEqual(summary["lost"], 0)
        self.assertEqual(len(summary["tasks"]), 2)
        self.assertEqual(len(self.history.as_list(limit=2)), 2)

    def test_retry_policy_escalates_and_caps_delay(self) -> None:
        policy = RetryPolicy(
            max_attempts=3,
            escalation=("model-b", "model-c"),
            base_delay_seconds=2,
            max_delay_seconds=3,
        )
        self.assertTrue(policy.should_retry(1))
        self.assertTrue(policy.should_retry(2))
        self.assertFalse(policy.should_retry(3))
        self.assertEqual(policy.next_model("model-a", 1), "model-a")
        self.assertEqual(policy.next_model("model-a", 2), "model-b")
        self.assertEqual(policy.next_model("model-a", 3), "model-c")
        self.assertEqual(policy.escalated_model("model-a", 2), "model-b")
        self.assertEqual(policy.escalated_model("model-a", 1), "")
        self.assertEqual(policy.delay(1), 0.0)
        self.assertEqual(policy.delay(2), 2.0)
        self.assertEqual(policy.delay(3), 3.0)  # capped


class StateProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="attempt-state-")
        self.run_dir = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_missing_journal_returns_stable_empty_summary(self) -> None:
        payload = unified_state._attempts_projection(self.run_dir)
        for key in ("total", "by_state", "retries", "tasks", "active", "lost", "items"):
            self.assertIn(key, payload)
        self.assertEqual(payload["total"], 0)
        self.assertEqual(payload["by_state"], {})
        self.assertEqual(payload["items"], [])

    def test_valid_journal_projects_summary_and_items(self) -> None:
        history = AttemptHistory(self.run_dir)
        attempt = history.start("T1", model="model-a")
        history.transition(attempt.attempt_id, WORKING)
        payload = unified_state._attempts_projection(self.run_dir)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["by_state"][WORKING], 1)
        self.assertEqual(payload["active"], 1)
        self.assertEqual(payload["items"][0]["attempt_id"], attempt.attempt_id)

    def test_corrupt_journal_does_not_raise(self) -> None:
        (self.run_dir / "attempts.ndjson").write_text(
            "not json\n{\"attempt_id\": \"bad\", \"task_id\": \"T1\", \"state\": \"NOPE\"}\n",
            encoding="utf-8",
        )
        payload = unified_state._attempts_projection(self.run_dir)
        self.assertEqual(payload["total"], 0)
        self.assertEqual(payload["items"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
