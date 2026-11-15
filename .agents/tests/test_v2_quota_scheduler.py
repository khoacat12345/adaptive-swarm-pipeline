#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 5 (F05) acceptance tests for quota-aware dispatch.

Run directly:

    python .agents/tests/test_v2_quota_scheduler.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2 import quota_gate as qg  # noqa: E402
from v2.scheduler import TASK_PAUSED, Scheduler  # noqa: E402
from v2.taskgraph import TaskGraph  # noqa: E402
from unified import state as unified_state  # noqa: E402


def paid(used: float, limit: float = 100.0, *, ok: bool = True) -> dict:
    return {
        "model": "paid",
        "ok": ok,
        "unlimited": False,
        "worst_pct": round(used / limit * 100.0, 1),
        "windows": {
            "today": {"used": used, "limit": limit},
            "week": {"used": used / 2, "limit": limit * 2},
            "month": {"used": used, "limit": limit},
        },
    }


UNLIMITED = {"model": "free", "ok": True, "unlimited": True, "windows": {}}
BLOCKED = paid(95.0)
UNKNOWN = {"model": "paid", "ok": False, "unknown": True, "windows": {}}


class FakeTracker:
    def __init__(self, rows: dict[str, dict]) -> None:
        self.rows = {name: dict(row) for name, row in rows.items()}
        self.caps = set(self.rows)

    def remaining(self, model: str) -> dict:
        return dict(self.rows.get(str(model), UNKNOWN))

    def snapshot(self) -> dict:
        return {"reset": {}, "available": True}

    def headroom(self, model: str, *, reserve_pct: float = 15.0) -> dict:
        return qg.evaluate_headroom(
            model, self.remaining(model), reserve_pct=reserve_pct,
        )


def gate(rows: dict[str, dict], *, candidates=(), strict=False) -> "qg.QuotaGate":
    return qg.QuotaGate(
        tracker=FakeTracker(rows),
        candidates=candidates,
        strict=strict,
    )


class HeadroomTests(unittest.TestCase):
    def test_unlimited_model_is_dispatchable(self) -> None:
        decision = qg.evaluate_headroom("free", UNLIMITED)
        self.assertTrue(decision["ok"])
        self.assertTrue(decision["unlimited"])

    def test_paid_model_near_limit_is_blocked_by_reserve(self) -> None:
        decision = qg.evaluate_headroom("paid", BLOCKED, reserve_pct=15.0)
        self.assertFalse(decision["ok"])
        self.assertIn("month", decision["blocked_windows"])

    def test_paid_model_with_headroom_is_available(self) -> None:
        decision = qg.evaluate_headroom("paid", paid(50.0), reserve_pct=15.0)
        self.assertTrue(decision["ok"])
        self.assertGreater(decision["windows"]["month"]["remaining"], 0)

    def test_shared_quota_block_blocks_the_model(self) -> None:
        row = paid(10.0)
        row["shared"] = {"ok": False, "worst_pct": 99.0}
        decision = qg.evaluate_headroom("paid", row)
        self.assertFalse(decision["ok"])
        self.assertIn("shared", decision["blocked_windows"])

    def test_choose_with_headroom_returns_first_available(self) -> None:
        chosen, decisions = qg.choose_with_headroom(
            ["paid", "free"], {"paid": BLOCKED, "free": UNLIMITED}
        )
        self.assertEqual(chosen, "free")
        self.assertFalse(decisions["paid"]["ok"])
        self.assertTrue(decisions["free"]["ok"])


class QuotaGateDecisionTests(unittest.TestCase):
    def test_dispatch_when_requested_model_has_headroom(self) -> None:
        decision = gate({"paid": paid(10.0)}).decide("paid")
        self.assertTrue(decision.ok)
        self.assertEqual(decision.action, "dispatch")
        self.assertEqual(decision.model, "paid")

    def test_reassign_to_free_model_when_paid_is_exhausted(self) -> None:
        decision = gate(
            {"paid": BLOCKED, "free": UNLIMITED}, candidates=["free"]
        ).decide("paid")
        self.assertTrue(decision.ok)
        self.assertEqual(decision.action, "reassign")
        self.assertEqual(decision.model, "free")
        self.assertIn("reassigned", decision.reason)

    def test_pause_when_every_candidate_is_exhausted(self) -> None:
        decision = gate(
            {"paid": BLOCKED, "paid2": paid(99.0)}, candidates=["paid2"]
        ).decide("paid")
        self.assertFalse(decision.ok)
        self.assertEqual(decision.action, "pause")
        self.assertIn("quota reserve exhausted", decision.reason)

    def test_unknown_quota_dispatches_in_non_strict_mode(self) -> None:
        decision = gate({"paid": UNKNOWN}).decide("paid")
        self.assertTrue(decision.ok)
        self.assertEqual(decision.action, "dispatch")
        self.assertTrue(decision.evidence.get("ungated"))

    def test_unknown_quota_pauses_in_strict_mode(self) -> None:
        decision = gate({"paid": UNKNOWN}, strict=True).decide("paid")
        self.assertFalse(decision.ok)
        self.assertEqual(decision.action, "pause")

    def test_no_model_binding_is_not_gated(self) -> None:
        decision = gate({}).decide(None)
        self.assertTrue(decision.ok)
        self.assertEqual(decision.action, "dispatch")
        self.assertIn("not applicable", decision.reason)

    def test_decision_evidence_contains_windows_and_reserve(self) -> None:
        decision = gate({"paid": paid(10.0)}).decide("paid")
        evidence = decision.evidence
        self.assertIn("windows", evidence)
        self.assertIn("paid", evidence["windows"])
        self.assertIn("reserve_pct", evidence)
        self.assertEqual(evidence["reserve_pct"], 15.0)

    def test_build_gate_returns_configured_gate(self) -> None:
        built = qg.build_gate(
            tracker=FakeTracker({"paid": paid(10.0)}),
            candidates=("free",),
            reserve_pct=20.0,
        )
        self.assertIsInstance(built, qg.QuotaGate)
        self.assertEqual(built.candidates, ["free"])
        self.assertEqual(built.reserve_pct, 20.0)

    def test_quota_decision_serializes(self) -> None:
        payload = qg.QuotaDecision(
            model="free", ok=True, action="reassign", reason="fallback",
            evidence={"window": "month"},
        ).as_dict()
        self.assertEqual(payload["model"], "free")
        self.assertEqual(payload["action"], "reassign")
        self.assertEqual(payload["evidence"]["window"], "month")


class SchedulerQuotaTests(unittest.TestCase):
    @staticmethod
    def graph() -> TaskGraph:
        return TaskGraph([{"id": "A", "priority": 0}])

    def test_scheduler_applies_reassignment_model(self) -> None:
        scheduler = Scheduler(
            self.graph(),
            models={"A": "paid"},
            gate=gate({"paid": BLOCKED, "free": UNLIMITED}, candidates=["free"]),
        )
        decision = scheduler.next_batch()[0]
        self.assertEqual(decision.model, "free")
        self.assertEqual(decision.action, "reassign")
        self.assertTrue(decision.ok)

    def test_scheduler_pauses_task_on_quota_exhaustion(self) -> None:
        scheduler = Scheduler(
            self.graph(),
            models={"A": "paid"},
            gate=gate({"paid": BLOCKED}, candidates=[]),
        )
        decision = scheduler.next_batch()[0]
        self.assertEqual(decision.action, "pause")
        self.assertFalse(decision.ok)
        self.assertEqual(scheduler.state_of("A"), TASK_PAUSED)
        self.assertEqual(scheduler.next_batch(), [])


class QuotaProjectionTests(unittest.TestCase):
    def test_projection_returns_latest_quota_event(self) -> None:
        events = [
            {
                "event_type": "quota.decision",
                "timestamp": "2026-01-01T00:00:00Z",
                "data": {"ok": False, "action": "pause", "reason": "old"},
            },
            {
                "event_type": "quota.snapshot",
                "timestamp": "2026-01-01T00:00:01Z",
                "data": {"ok": True, "headroom_pct": 42.0},
            },
        ]
        payload = unified_state._quota_projection(events)
        self.assertTrue(payload["available"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["headroom_pct"], 42.0)
        self.assertEqual(payload["at"], "2026-01-01T00:00:01Z")

    def test_projection_missing_event_is_unavailable(self) -> None:
        self.assertEqual(
            unified_state._quota_projection([]), {"available": False}
        )

    def test_projection_redacts_secret_values(self) -> None:
        events = [{
            "event_type": "quota.decision",
            "timestamp": "2026-01-01T00:00:00Z",
            "data": {"ok": True, "access_token": "super-secret"},
        }]
        payload = unified_state._quota_projection(events)
        self.assertNotIn("super-secret", str(payload))


if __name__ == "__main__":
    unittest.main(verbosity=2)
