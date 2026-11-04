#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 5 tests: event IDs/pagination and rate-limit/user-confirmation states."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2.event_runtime import (  # noqa: E402
    EventStore,
    action_event,
    observation_event,
)
from v2.state import RunStore  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "v2_event_state_tests"


class EventRuntimeTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)
        TEMP_ROOT.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)

    def test_action_and_observation_ids(self):
        store = EventStore(TEMP_ROOT / "events.ndjson")
        action = action_event(run_id="r1", action="open", phase_id="P1")
        observation = observation_event(
            run_id="r1", action_id=action.action_id, status="ok",
            observation="line window", phase_id="P1",
        )
        store.append(action)
        store.append(observation)
        page = store.read(limit=10)
        self.assertEqual(len(page.items), 2)
        self.assertEqual(page.items[1].action_id, action.action_id)
        self.assertTrue(page.items[0].event_id.startswith("evt_"))

    def test_pagination_reconstructs_without_duplicates(self):
        store = EventStore(TEMP_ROOT / "events.ndjson")
        for index in range(5):
            store.append(action_event(run_id="r1", action=f"a{index}"))
        seen = []
        cursor = None
        while True:
            page = store.read(cursor=cursor, limit=2)
            seen.extend(item.event_id for item in page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        self.assertEqual(len(seen), 5)
        self.assertEqual(len(set(seen)), 5)

    def test_legacy_event_remains_readable(self):
        path = TEMP_ROOT / "events.ndjson"
        path.write_text(json.dumps({
            "schema_version": 1, "event_id": "old-1", "run_id": "old",
            "event_type": "legacy", "timestamp": "2026-01-01T00:00:00.000Z",
            "duration_ms": 0, "usage": {}, "result": {"status": "pass"},
        }) + "\n", encoding="utf-8")
        page = EventStore(path).read(limit=10)
        self.assertEqual(page.items[0].event_id, "old-1")
        self.assertTrue(path.read_text(encoding="utf-8").startswith("{"))

    def test_rate_limited_and_confirmation_states(self):
        store = RunStore.create(TEMP_ROOT, goal="state", base_sha="a" * 40)
        store.transition("preflight_ok")
        store.transition("start_planning")
        store.transition("plan_valid")
        store.transition("sign_off")
        store.transition("worktree_ready")
        store.transition("rate_limit")
        self.assertEqual(store.load()["status"], "RATE_LIMITED")
        store.transition("resume")
        self.assertEqual(store.load()["status"], "PHASE_EXECUTING")
        store.transition("request_confirmation")
        self.assertEqual(store.load()["status"], "AWAITING_USER_CONFIRMATION")
        store.transition("user_confirms")
        self.assertEqual(store.load()["status"], "USER_CONFIRMED")
        store.transition("resume")
        self.assertEqual(store.load()["status"], "PHASE_EXECUTING")
        store.transition("request_confirmation")
        store.transition("user_rejects")
        self.assertEqual(store.load()["status"], "USER_REJECTED")


if __name__ == "__main__":
    unittest.main()
