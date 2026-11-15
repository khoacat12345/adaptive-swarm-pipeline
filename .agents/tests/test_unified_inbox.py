#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 6 (F03) inbox data-model and persistence tests.

Run directly:

    python .agents/tests/test_unified_inbox.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from unified.inbox import (  # noqa: E402
    DecisionInbox,
    DecisionMessage,
    InboxError,
    MessageNotFound,
)


class InboxModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="inbox-model-")
        self.run_dir = Path(self.temp.name)
        self.inbox = DecisionInbox(self.run_dir, run_id="run-1")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_ask_persists_the_required_schema(self) -> None:
        message = self.inbox.ask(
            "Approve the merge?",
            task_id="T1",
            options=["yes", "no"],
            default="no",
            blocking=True,
        )
        payload = message.as_dict()
        for key in (
            "decision_id", "run_id", "task_id", "question", "options",
            "default", "blocking", "status", "answer", "answered_at",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["decision_id"], message.decision_id)
        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(payload["task_id"], "T1")
        self.assertEqual(payload["options"], ["yes", "no"])
        self.assertEqual(payload["default"], "no")
        self.assertEqual(payload["status"], "pending")
        self.assertTrue(payload["blocking"])

    def test_pending_and_resolved_queries(self) -> None:
        first = self.inbox.ask("First?")
        second = self.inbox.ask("Second?")
        self.assertEqual([item.decision_id for item in self.inbox.pending()],
                         [first.decision_id, second.decision_id])
        self.inbox.resolve(first.decision_id, "yes")
        self.assertEqual(
            [item.decision_id for item in self.inbox.pending()],
            [second.decision_id],
        )
        self.assertEqual(
            [item.decision_id for item in self.inbox.resolved()],
            [first.decision_id],
        )

    def test_resolve_records_answer_and_timestamp(self) -> None:
        message = self.inbox.ask("Ship it?")
        resolved = self.inbox.resolve(message.decision_id, "yes")
        self.assertEqual(resolved.status, "resolved")
        self.assertEqual(resolved.answer, "yes")
        self.assertTrue(resolved.answered_at)
        self.assertFalse(resolved.open)
        self.assertFalse(resolved.blocks_run)

    def test_resolve_unknown_decision_raises(self) -> None:
        with self.assertRaises(MessageNotFound):
            self.inbox.resolve("MSG-9999", "yes")

    def test_resolve_twice_raises(self) -> None:
        message = self.inbox.ask("Ship it?")
        self.inbox.resolve(message.decision_id, "yes")
        with self.assertRaises(InboxError):
            self.inbox.resolve(message.decision_id, "again")

    def test_blocking_and_nonblocking_flags(self) -> None:
        blocking = self.inbox.ask("Blocking?", blocking=True)
        nonblocking = self.inbox.ask("FYI", blocking=False)
        self.assertTrue(blocking.blocks_run)
        self.assertFalse(nonblocking.blocks_run)
        self.assertEqual(self.inbox.summary()["blocking"], 1)
        self.assertTrue(self.inbox.needs_attention())

    def test_summary_counts_pending_resolved_and_items(self) -> None:
        first = self.inbox.ask("One?")
        self.inbox.ask("Two?", blocking=False)
        self.inbox.resolve(first.decision_id, "done")
        summary = self.inbox.summary()
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["blocking"], 0)
        self.assertFalse(summary["needs_attention"])
        self.assertEqual(len(summary["items"]), 2)
        self.assertIn("decision_id", summary["items"][0])

    def test_reload_uses_latest_snapshot(self) -> None:
        message = self.inbox.ask("Reload?", default="no")
        self.inbox.resolve(message.decision_id, "yes")
        reloaded = DecisionInbox(self.run_dir, run_id="run-1")
        latest = reloaded.get(message.decision_id)
        self.assertEqual(latest.status, "resolved")
        self.assertEqual(latest.answer, "yes")

    def test_corrupt_lines_are_skipped(self) -> None:
        message = self.inbox.ask("Survive?")
        with (self.run_dir / "inbox.ndjson").open("a", encoding="utf-8") as handle:
            handle.write("{not-json}\n")
            handle.write('{"decision_id": "", "question": "bad"}\n')
        reloaded = DecisionInbox(self.run_dir, run_id="run-1")
        self.assertEqual(reloaded.get(message.decision_id).question, "Survive?")

    def test_missing_journal_is_empty(self) -> None:
        self.assertEqual(self.inbox.load(), [])
        self.assertEqual(self.inbox.pending(), [])
        self.assertEqual(self.inbox.resolved(), [])

    def test_decision_message_round_trip_keeps_new_fields(self) -> None:
        message = DecisionMessage(
            id="MSG-0001",
            run_id="run-1",
            task_id="T1",
            question="Q",
            options=["a", "b"],
            default="a",
            blocking=True,
            status="pending",
        )
        restored = DecisionMessage.from_dict(message.as_dict())
        self.assertEqual(restored.decision_id, "MSG-0001")
        self.assertEqual(restored.task_id, "T1")
        self.assertEqual(restored.options, ["a", "b"])
        self.assertEqual(restored.default, "a")

    def test_expired_status_is_not_pending(self) -> None:
        message = DecisionMessage(
            id="MSG-0001",
            run_id="run-1",
            question="Expired?",
            status="expired",
        )
        self.assertFalse(message.open)
        self.assertFalse(message.blocks_run)


if __name__ == "__main__":
    unittest.main(verbosity=2)
