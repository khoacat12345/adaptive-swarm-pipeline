#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 6 (F03) inbox integration tests: CLI, API, pause/resume, projection.

Run directly:

    python .agents/tests/test_v2_inbox_integration.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from unified import api  # noqa: E402
from unified import state as unified_state  # noqa: E402
from unified.inbox import DecisionInbox  # noqa: E402
from v2 import swarm_cli  # noqa: E402


class FakeStore:
    def __init__(self, status: str) -> None:
        self.status = status
        self.events: list[tuple[str, object]] = []

    def load(self) -> dict:
        return {"status": self.status}

    def append_event(self, name: str, payload: object) -> None:
        self.events.append((name, payload))


class FakeRunner:
    def __init__(self, status: str) -> None:
        self.store = FakeStore(status)
        self.paused: list[tuple[str, str]] = []
        self.resumed: list[str] = []

    def load(self, run_id: str) -> FakeStore:
        return self.store

    def pause(self, run_id: str, *, reason: str = "") -> dict:
        self.paused.append((run_id, reason))
        self.store.status = "PAUSED"
        return {"status": "PAUSED"}

    def resume(self, run_id: str) -> dict:
        self.resumed.append(run_id)
        self.store.status = "PHASE_EXECUTING"
        return {"status": "PHASE_EXECUTING"}


class InboxIntegrationFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="inbox-integration-")
        self.root = Path(self.temp.name)
        self.state_root = self.root / "state"
        self.run_id = "run-1"
        self.run_dir = self.state_root / "runs" / self.run_id
        self.inbox = DecisionInbox(self.run_dir, run_id=self.run_id)

    def tearDown(self) -> None:
        self.temp.cleanup()


class StateProjectionTests(InboxIntegrationFixture):
    def test_missing_journal_has_stable_shape(self) -> None:
        payload = unified_state._inbox_projection(self.run_dir, self.run_id)
        for key in ("available", "total", "pending", "blocking",
                    "needs_attention", "items"):
            self.assertIn(key, payload)
        self.assertEqual(payload["total"], 0)
        self.assertEqual(payload["items"], [])

    def test_valid_journal_projects_new_schema(self) -> None:
        self.run_dir.mkdir(parents=True)
        row = {
            "decision_id": "MSG-0001",
            "run_id": self.run_id,
            "task_id": "T1",
            "question": "Approve?",
            "options": ["yes", "no"],
            "default": "no",
            "blocking": True,
            "status": "pending",
            "answer": None,
            "answered_at": None,
        }
        (self.run_dir / "inbox.ndjson").write_text(
            json.dumps(row) + "\n", encoding="utf-8"
        )
        payload = unified_state._inbox_projection(self.run_dir, self.run_id)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["pending"], 1)
        self.assertEqual(payload["items"][0]["decision_id"], "MSG-0001")


class PauseResumeTests(InboxIntegrationFixture):
    def test_blocking_message_pauses_active_run(self) -> None:
        message = self.inbox.ask("Approve?", blocking=True)
        runner = FakeRunner("PHASE_EXECUTING")
        with mock.patch("unified.api._runner", return_value=runner):
            paused = api._pause_for_blocking(
                self.root, self.run_id, str(self.state_root), message
            )
        self.assertTrue(paused)
        self.assertEqual(len(runner.paused), 1)
        self.assertIn("decision", runner.paused[0][1])

    def test_nonblocking_message_does_not_pause(self) -> None:
        message = self.inbox.ask("FYI", blocking=False)
        runner = FakeRunner("PHASE_EXECUTING")
        with mock.patch("unified.api._runner", return_value=runner):
            paused = api._pause_for_blocking(
                self.root, self.run_id, str(self.state_root), message
            )
        self.assertFalse(paused)
        self.assertEqual(runner.paused, [])

    def test_resolved_message_resumes_paused_run(self) -> None:
        message = self.inbox.ask("Approve?", blocking=True)
        self.inbox.resolve(message.decision_id, "yes")
        runner = FakeRunner("PAUSED")
        with mock.patch("unified.api._runner", return_value=runner):
            resumed = api._resume_if_unblocked(
                self.root, self.run_id, str(self.state_root), self.inbox
            )
        self.assertTrue(resumed)
        self.assertEqual(runner.resumed, [self.run_id])

    def test_ask_decision_pauses_and_reply_resumes(self) -> None:
        runner = FakeRunner("PHASE_EXECUTING")
        with mock.patch("unified.api._runner", return_value=runner):
            asked = api.ask_decision(
                self.root, self.run_id, "Approve?", state_root=str(self.state_root)
            )
            self.assertTrue(asked["paused"])
            self.assertEqual(len(runner.paused), 1)
            message_id = asked["message"]["decision_id"]
            replied = api.reply_decision(
                self.root, message_id, "yes", run_id=self.run_id,
                state_root=str(self.state_root),
            )
        self.assertTrue(replied["ok"])
        self.assertEqual(runner.resumed, [self.run_id])


class CliParserTests(unittest.TestCase):
    def test_inbox_run_id_is_optional(self) -> None:
        parser = swarm_cli._build_parser()
        args = parser.parse_args(["inbox"])
        self.assertIsNone(getattr(args, "run_id", None))

    def test_ask_accepts_question_and_blocking_flag(self) -> None:
        parser = swarm_cli._build_parser()
        args = parser.parse_args(["ask", "Approve the merge?", "--blocking"])
        self.assertEqual(args.question, "Approve the merge?")
        self.assertTrue(args.blocking)

    def test_reply_accepts_decision_id_and_answer(self) -> None:
        parser = swarm_cli._build_parser()
        args = parser.parse_args(["reply", "MSG-0001", "yes"])
        self.assertEqual(args.message_id, "MSG-0001")
        self.assertEqual(args.answer, "yes")


class ApiRouteTests(InboxIntegrationFixture):
    def test_get_inbox_route_returns_projection(self) -> None:
        self.inbox.ask("Approve?", blocking=True)
        status, payload = api.handle_get(
            self.root,
            f"/api/unified/runs/{self.run_id}/inbox",
            {"state_root": str(self.state_root)},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 1)
        self.assertTrue(payload["needs_attention"])

    def test_post_reply_route_resolves_decision(self) -> None:
        message = self.inbox.ask("Approve?", blocking=True)
        runner = FakeRunner("PAUSED")
        with mock.patch("unified.api._runner", return_value=runner):
            status, payload = api.handle_post(
                self.root,
                f"/api/unified/runs/{self.run_id}/inbox/{message.decision_id}/reply",
                {"answer": "yes", "state_root": str(self.state_root)},
            )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.inbox.get(message.decision_id).status, "resolved")


if __name__ == "__main__":
    unittest.main(verbosity=2)
