#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the unified state projection.

These tests cover the two v3.0.1 projection fixes:

* D01: credential redaction must not hide token-count metrics.
* D02: verify summary must aggregate checkpoints and merge verification.

Run directly or with unittest discovery:

    python .agents/tests/test_unified_state.py
    python -m unittest discover -s .agents/tests -p "test_unified_state.py"
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from unified import state as unified_state  # noqa: E402


class TestTokenRedaction(unittest.TestCase):
    def test_token_metrics_are_preserved(self) -> None:
        payload = {
            "estimated_tokens": 1500,
            "used_tokens": 200,
            "remaining_tokens": 1300,
            "prompt_tokens": 120,
            "completion_tokens": 80,
            "total_tokens": 200,
            "access_token": "secret_123",
            "signoff_token": "signoff_456",
        }
        redacted = unified_state.redact(payload)

        self.assertEqual(redacted["estimated_tokens"], 1500)
        self.assertEqual(redacted["used_tokens"], 200)
        self.assertEqual(redacted["remaining_tokens"], 1300)
        self.assertEqual(redacted["prompt_tokens"], 120)
        self.assertEqual(redacted["completion_tokens"], 80)
        self.assertEqual(redacted["total_tokens"], 200)
        self.assertEqual(redacted["access_token"], "[REDACTED_SECRET]")
        self.assertEqual(redacted["signoff_token"], "[REDACTED_SECRET]")

    def test_token_metrics_in_nested_phase_are_preserved(self) -> None:
        payload = {"phases": {"S1": {"used_tokens": 42, "token_budget": 500}}}
        redacted = unified_state.redact(payload)

        self.assertEqual(redacted["phases"]["S1"]["used_tokens"], 42)
        self.assertEqual(redacted["phases"]["S1"]["token_budget"], 500)

    def test_exact_token_key_is_still_a_credential(self) -> None:
        redacted = unified_state.redact({"token": "secret-token"})
        self.assertEqual(redacted["token"], "[REDACTED_SECRET]")


class TestVerifySummary(unittest.TestCase):
    def test_checkpoint_and_merge_verification_are_aggregated(self) -> None:
        plan = {
            "phases": [
                {"work_orders": [{"verify_command": "python check.py"}]}
            ]
        }
        events = [
            {
                "event_type": "checkpoint.recorded",
                "timestamp": "2026-11-12T00:00:01Z",
                "data": {"verify_exit": 0, "verify_command": "python check.py"},
            },
            {
                "event_type": "run.merged",
                "timestamp": "2026-11-12T00:00:02Z",
                "data": {
                    "verify": [
                        {
                            "command": "python check.py",
                            "exit_code": 0,
                            "output_tail": "OK\n",
                        }
                    ]
                },
            },
        ]

        summary = unified_state._verify_summary(plan, events)

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["last_exit"], 0)
        self.assertEqual(summary["results"][-1]["event_type"], "run.merged")
        self.assertEqual(summary["results"][-1]["command"], "python check.py")
        self.assertEqual(summary["results"][-1]["output"], "OK\n")

    def test_direct_verify_event_still_works(self) -> None:
        plan = {"phases": [{"work_orders": [{"verify_command": "pytest -q"}]}]}
        events = [
            {
                "event_type": "verify.passed",
                "timestamp": "2026-11-12T00:00:03Z",
                "data": {"exit_code": 0},
            }
        ]

        summary = unified_state._verify_summary(plan, events)

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["last_exit"], 0)
        self.assertEqual(summary["command"], "pytest -q")

    def test_latest_failure_wins(self) -> None:
        plan = {"phases": [{"work_orders": [{"verify_command": "python check.py"}]}]}
        events = [
            {
                "event_type": "checkpoint.recorded",
                "timestamp": "2026-11-12T00:00:04Z",
                "data": {"verify_exit": 0},
            },
            {
                "event_type": "run.merged",
                "timestamp": "2026-11-12T00:00:05Z",
                "data": {"verify": [{"command": "python check.py", "exit_code": 2}]},
            },
        ]

        summary = unified_state._verify_summary(plan, events)

        self.assertFalse(summary["ok"])
        self.assertEqual(summary["last_exit"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
