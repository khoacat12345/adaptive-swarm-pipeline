#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 2 (F11) acceptance tests for the unified completion report.

Run directly:

    python .agents/tests/test_unified_report.py
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
from unified import report as unified_report  # noqa: E402


class ReportFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="unified-report-")
        self.root = Path(self.temp.name)
        self.run_id = "run-report-1"
        self.run_dir = self.root / "runs" / self.run_id
        (self.run_dir / "handoff").mkdir(parents=True)
        (self.run_dir / "handoff" / "handoff.patch").write_text(
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n"
            "+++ b/src/a.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/src/b.py b/src/b.py\n"
            "--- a/src/b.py\n"
            "+++ b/src/b.py\n"
            "@@ -1 +1 @@\n"
            "-gone\n"
            "+kept\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_events(self, rows: list[dict]) -> None:
        text = "\n".join(json.dumps(row) for row in rows)
        (self.run_dir / "events.ndjson").write_text(text + "\n", encoding="utf-8")

    def detail(self, **overrides: object) -> dict:
        detail = {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "status": "HANDOFF_READY",
            "goal": "Ship the report",
            "tier": "E3",
            "planner": "rules",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:12Z",
            "base_sha": "base123",
            "plan_hash": "plan456",
            "worktree": "wt-1",
            "state_root": str(self.root),
            "spend": {
                "used_requests": 4,
                "max_requests": 10,
                "used_cost_usd": 0.25,
                "max_cost_usd": 1.0,
                "used_tokens": 1200,
                "estimated_tokens": 5000,
                "access_token": "super-secret-token",
            },
            "verify": {
                "commands": ["python check.py"],
                "ok": True,
                "last_exit": 0,
                "results": [],
            },
            "handoff": {
                "ready": True,
                "patch": "handoff/handoff.patch",
                "report": "handoff/report.md",
                "patch_bytes": 123,
            },
            "warnings": ["handoff is ready"],
        }
        detail.update(overrides)
        return detail

    def build(self, **overrides: object) -> dict:
        return unified_report.build_report(
            self.root, self.run_id, detail=self.detail(**overrides)
        )


class DiffstatTests(unittest.TestCase):
    def test_diffstat_counts_additions_deletions_and_files(self) -> None:
        patch = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -1 +1 @@\n"
            "-gone\n"
            "+kept\n"
        )
        result = unified_report.diffstat(patch)
        self.assertEqual(result["files_changed"], 2)
        self.assertEqual(result["additions"], 2)
        self.assertEqual(result["deletions"], 2)
        self.assertEqual([row["path"] for row in result["files"]], ["a.py", "b.py"])

    def test_diffstat_marks_binary_files(self) -> None:
        patch = (
            "diff --git a/image.png b/image.png\n"
            "Binary files a/image.png and b/image.png differ\n"
        )
        result = unified_report.diffstat(patch)
        self.assertEqual(result["files"][0]["binary"], True)
        self.assertEqual(result["binary"], 1)


class BuildReportTests(ReportFixture):
    def test_report_has_stable_schema_and_handoff_data(self) -> None:
        payload = self.build()
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["run_id"], self.run_id)
        self.assertEqual(payload["status"], "HANDOFF_READY")
        self.assertEqual(payload["goal"], "Ship the report")
        self.assertEqual(payload["duration_seconds"], 12.0)
        self.assertEqual(payload["changes"]["files_changed"], 2)
        self.assertEqual(payload["changes"]["additions"], 2)
        self.assertEqual(payload["changes"]["deletions"], 2)
        self.assertTrue(payload["handoff"]["ready"])
        self.assertEqual(payload["handoff"]["patch_bytes"], 123)

    def test_secret_values_are_redacted(self) -> None:
        payload = self.build()
        text = json.dumps(payload)
        self.assertNotIn("super-secret-token", text)
        self.assertEqual(payload["spend"]["used_tokens"], 1200)
        self.assertEqual(payload["spend"]["estimated_tokens"], 5000)

    def test_verify_falls_back_to_checkpoint_and_merge_events(self) -> None:
        self.write_events(
            [
                {
                    "event_type": "checkpoint.recorded",
                    "timestamp": "2026-01-01T00:00:03Z",
                    "data": {
                        "verify_command": "python check.py",
                        "verify_exit": 0,
                        "verify_output": "checkpoint ok",
                    },
                },
                {
                    "event_type": "run.merged",
                    "timestamp": "2026-01-01T00:00:04Z",
                    "data": {
                        "verify": [
                            {"command": "python merge_check.py", "exit_code": 2}
                        ]
                    },
                },
            ]
        )
        payload = self.build(
            verify={"commands": [], "ok": None, "last_exit": None, "results": []}
        )
        rows = payload["verify"]["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["command"] for row in rows], [
            "python check.py", "python merge_check.py",
        ])
        self.assertEqual([row["exit_code"] for row in rows], [0, 2])
        self.assertFalse(payload["verify"]["ok"])
        self.assertEqual(payload["verify"]["last_exit"], 2)
        self.assertEqual(
            payload["verify"]["commands"], ["python check.py", "python merge_check.py"]
        )

    def test_merge_and_rollback_records_are_exposed(self) -> None:
        (self.run_dir / "merge.json").write_text(
            json.dumps({
                "merge_sha": "merge123",
                "pre_merge_sha": "pre456",
                "message": "merge report",
            }),
            encoding="utf-8",
        )
        (self.run_dir / "rollback.json").write_text(
            json.dumps({"rollback_sha": "rollback789", "reason": "regression"}),
            encoding="utf-8",
        )
        payload = self.build()
        self.assertTrue(payload["merge"]["merged"])
        self.assertEqual(payload["merge"]["merge_sha"], "merge123")
        self.assertTrue(payload["merge"]["rolled_back"])
        self.assertEqual(payload["merge"]["rollback_sha"], "rollback789")

    def test_e4_meeting_is_only_exposed_for_e4(self) -> None:
        detail = self.detail(
            tier="E3",
            blackboard={"messages": [{"text": "secret"}], "rounds": [{"round": 1}]},
        )
        payload = unified_report.build_report(self.root, self.run_id, detail=detail)
        self.assertIsNone(payload["meeting"])

        detail = self.detail(
            tier="E4",
            blackboard={
                "messages": [{"text": "meeting"}],
                "rounds": [{"round": 1, "messages": [{}], "decision": {"verdict": "pass"}}],
            },
        )
        payload = unified_report.build_report(self.root, self.run_id, detail=detail)
        self.assertIsNotNone(payload["meeting"])
        self.assertEqual(len(payload["meeting"]["rounds"]), 1)

    def test_next_action_reflects_handoff_state(self) -> None:
        payload = self.build(status="HANDOFF_READY")
        self.assertIn("merge explicitly", payload["next_action"])

        payload = self.build(status="FAILED")
        self.assertIn("quarantine", payload["next_action"])


class MarkdownTests(ReportFixture):
    def test_markdown_contains_core_sections(self) -> None:
        payload = self.build()
        markdown = unified_report.render_markdown(payload)
        self.assertIn("# Run report", markdown)
        self.assertIn("**Status:** `HANDOFF_READY`", markdown)
        self.assertIn("## Spend", markdown)
        self.assertIn("## Changes", markdown)
        self.assertIn("## Verify", markdown)
        self.assertIn("## Delivery", markdown)
        self.assertIn("a.py", markdown)

    def test_markdown_does_not_leak_secret(self) -> None:
        markdown = unified_report.render_markdown(self.build())
        self.assertNotIn("super-secret-token", markdown)


class ApiReportTests(ReportFixture):
    def test_run_report_endpoint_returns_json(self) -> None:
        with mock.patch(
            "unified.state.read_run", return_value=self.detail()
        ):
            status, payload = api.handle_get(
                self.root, f"/api/unified/runs/{self.run_id}/report"
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["run_id"], self.run_id)
        self.assertEqual(payload["status"], "HANDOFF_READY")
        self.assertIn("verify", payload)

    def test_run_report_markdown_endpoint_returns_markdown(self) -> None:
        with mock.patch(
            "unified.state.read_run", return_value=self.detail()
        ):
            status, payload = api.handle_get(
                self.root, f"/api/unified/runs/{self.run_id}/report.md"
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["filename"], f"run-report-{self.run_id}.md")
        self.assertIsInstance(payload["markdown"], str)
        self.assertIn("# Run report", payload["markdown"])

    def test_missing_run_returns_404(self) -> None:
        with mock.patch(
            "unified.state.read_run", side_effect=FileNotFoundError("missing run")
        ):
            status, payload = api.handle_get(
                self.root, "/api/unified/runs/missing/report"
            )
        self.assertEqual(status, 404)
        self.assertIn("missing run", payload["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
