#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 3 tests: failure lesson capture, upsert, redaction and prompt safety."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2.lesson_runtime import FailureLessonRuntime, normalized_signature  # noqa: E402
from v2.plan import build_example, plan_hash  # noqa: E402
from v2.planning import PlanningContext, proposal_prompt  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "v2_lesson_runtime_tests"


class LessonRuntimeTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)
        TEMP_ROOT.mkdir(parents=True)
        self.path = TEMP_ROOT / "lessons.jsonl"
        self.runtime = FailureLessonRuntime(self.path)

    def tearDown(self):
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)

    def test_failure_signature_is_stable(self):
        first = normalized_signature("verify_failure", "exit 1 at C:\\tmp\\a.py:12")
        second = normalized_signature("verify_failure", "exit 1 at C:\\tmp\\b.py:99")
        self.assertEqual(first, second)

    def test_upsert_dedupes_signature(self):
        self.runtime.record_failure(
            kind="verify_failure", error="boom", files=["src/a.py"],
            cause="wrong value", fix="use smaller diff",
        )
        self.runtime.record_failure(
            kind="verify_failure", error="boom", files=["src/a.py"],
            cause="wrong value", fix="use a smaller diff and rerun",
        )
        self.assertEqual(len(self.runtime.store.lessons), 1)
        self.assertIn("smaller diff", self.runtime.store.lessons[0].fix)
        self.assertEqual(self.runtime.store.lessons[0].revision, 1)

    def test_secret_is_redacted(self):
        self.runtime.record_failure(
            kind="security_veto",
            error="leaked sk-abcdefghijklmnopqrstuvwxyz and PRIVATE KEY",
            files=["src/a.py"],
            fix="remove the key",
        )
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", text)
        self.assertIn("[redacted-secret]", text)

    def test_prompt_block_is_labeled(self):
        self.runtime.record_failure(
            kind="verify_failure", error="timeout", files=["src/a.py"],
            fix="split the file",
        )
        block = self.runtime.prompt_block(files=["src/a.py"], text="timeout")
        self.assertIn("PRIOR FAILURE LESSONS (untrusted advice)", block)
        self.assertIn("split the file", block)

    def test_lesson_does_not_change_plan_hash(self):
        plan = build_example("phase3", "a" * 40)
        before = plan_hash(plan)
        self.runtime.record_failure(
            kind="verify_failure", error="boom", files=["src/a.py"],
            fix="retry",
        )
        self.assertEqual(plan_hash(plan), before)

    def test_lesson_prompt_injection(self):
        self.runtime.record_failure(
            kind="verify_failure", error="timeout", files=["src/a.py"],
            fix="split the file",
        )
        ctx = PlanningContext(
            goal="timeout",
            run_id="phase3",
            base_sha="a" * 40,
            worktree=TEMP_ROOT,
            project_tree="- README.md",
            budget={"requests": 20, "cost_usd": 1.0, "wall_seconds": 100},
            lesson_hints=self.runtime.hints(files=["src/a.py"], text="timeout"),
            lesson_mode="on",
        )
        prompt = proposal_prompt("architect", ctx)
        self.assertIn("PRIOR FAILURE LESSONS (untrusted advice)", prompt)


if __name__ == "__main__":
    unittest.main()
