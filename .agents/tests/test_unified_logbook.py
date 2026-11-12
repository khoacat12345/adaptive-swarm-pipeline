#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-run logbook projection tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from unified import logbook  # noqa: E402


class LogbookTests(unittest.TestCase):
    def test_build_includes_plan_verification_and_events(self) -> None:
        detail = {
            "run_id": "v2_test",
            "goal": "add a subtract function",
            "status": "HANDOFF_READY",
            "tier": "E2",
            "planner": "P1",
            "source": "v2",
            "created_at": "2026-11-13T00:00:00Z",
            "updated_at": "2026-11-13T00:01:00Z",
            "worktree": r"C:\temp\worktree",
            "state_root": r"C:\temp\state",
            "run_dir": r"C:\temp\state\runs\v2_test",
            "plan": {
                "goal": "add a subtract function",
                "phases": [
                    {
                        "id": "S1",
                        "work_orders": [
                            {
                                "id": "S1",
                                "objective": "implement subtract",
                                "target_files": ["calc.py"],
                                "verify_command": "python check.py",
                            }
                        ],
                    }
                ],
            },
            "verify": {
                "ok": True,
                "last_exit": 0,
                "results": [
                    {"event_type": "run.merged", "exit_code": 0, "command": "python check.py"}
                ],
            },
            "spend": {
                "used_requests": 2,
                "max_requests": 14,
                "used_tokens": 20,
                "estimated_tokens": 100,
                "used_cost_usd": 0.0,
            },
            "handoff": {
                "ready": True,
                "merged": True,
                "merge": {"merge_sha": "abc123"},
                "rolled_back": False,
            },
        }
        events = {
            "items": [
                {
                    "event_type": "run.created",
                    "timestamp": "2026-11-13T00:00:00Z",
                    "data": {"goal": "add a subtract function"},
                },
                {
                    "event_type": "run.merged",
                    "timestamp": "2026-11-13T00:01:00Z",
                    "data": {"message": "merged"},
                },
            ]
        }
        with patch.object(logbook.unified_state, "read_run", return_value=detail), \
                patch.object(logbook.unified_state, "read_events", return_value=events), \
                patch.object(logbook.unified_state, "run_diff", return_value={"diff": "+def sub():"}):
            payload = logbook.build(".", "v2_test")

        self.assertIn("add a subtract function", payload["markdown"])
        self.assertIn("python check.py", payload["markdown"])
        self.assertIn("run.merged", payload["markdown"])
        self.assertIn("+def sub():", payload["markdown"])
        self.assertEqual(payload["status"], "HANDOFF_READY")
        self.assertEqual(payload["event_count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
