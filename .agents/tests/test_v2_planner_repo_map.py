#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1 tests: repo-map context, safety filters and planner integration."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2.plan import build_example  # noqa: E402
from v2.planning import (  # noqa: E402
    PLANNER_ROLES,
    PlanningContext,
    PlanningCouncil,
    fake_planner,
    proposal_prompt,
)
from v2.repo_context import build_repo_context  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "v2_repo_context_tests"


def rmtree(path):
    def onerror(func, target, exc_info):
        try:
            Path(target).chmod(0o700)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=onerror)


class RepoContextTests(unittest.TestCase):
    def setUp(self):
        rmtree(TEMP_ROOT)
        (TEMP_ROOT / "src").mkdir(parents=True)
        (TEMP_ROOT / "README.md").write_text("demo\n", encoding="utf-8")
        (TEMP_ROOT / "src" / "core.py").write_text(
            "def important():\n    return 1\n", encoding="utf-8"
        )
        (TEMP_ROOT / "src" / "feature.py").write_text(
            "from src.core import important\n\ndef feature():\n    return important()\n",
            encoding="utf-8",
        )
        (TEMP_ROOT / "src" / "noise.py").write_text(
            "def noise():\n    return 0\n", encoding="utf-8"
        )
        (TEMP_ROOT / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        (TEMP_ROOT / "secret.py").write_text(
            "def deadly_secret():\n    return 1\n", encoding="utf-8"
        )

    def tearDown(self):
        rmtree(TEMP_ROOT)

    def test_filters_secret_paths(self):
        context = build_repo_context(TEMP_ROOT, "feature", mode="on")
        self.assertNotIn(".env", context.text)
        self.assertNotIn("secret.py", context.text)
        self.assertNotIn("TOKEN", context.text)

    def test_token_cap(self):
        context = build_repo_context(TEMP_ROOT, "feature", mode="on", max_tokens=200)
        self.assertLessEqual(context.tokens, 220)

    def test_empty_map_falls_back(self):
        empty = TEMP_ROOT / "empty"
        empty.mkdir()
        context = build_repo_context(empty, "anything", mode="on")
        self.assertTrue(context.fallback)
        self.assertEqual(context.mode, "on")
        self.assertEqual(context.text, "")

    def test_shadow_does_not_inject_prompt(self):
        context_text = build_repo_context(TEMP_ROOT, "feature", mode="shadow").text
        ctx = PlanningContext(
            goal="feature",
            run_id="phase1-shadow",
            base_sha="a" * 40,
            worktree=TEMP_ROOT,
            project_tree="- README.md",
            budget={"requests": 20, "cost_usd": 1.0, "wall_seconds": 100},
            repo_map=context_text,
            repo_map_mode="shadow",
        )
        prompt = proposal_prompt("architect", ctx)
        self.assertNotIn("TASK-AWARE REPO MAP", prompt)
        ctx.repo_map_mode = "on"
        self.assertIn("TASK-AWARE REPO MAP", proposal_prompt("architect", ctx))

    def test_shadow_event_is_emitted(self):
        context_text = build_repo_context(TEMP_ROOT, "feature", mode="shadow").text
        plan = build_example("phase1-shadow", "a" * 40)
        events = []
        ctx = PlanningContext(
            goal="feature",
            run_id="phase1-shadow",
            base_sha="a" * 40,
            worktree=TEMP_ROOT,
            project_tree="- README.md",
            budget={"requests": 20, "cost_usd": 1.0, "wall_seconds": 100},
            max_rounds=1,
            repo_map=context_text,
            repo_map_mode="shadow",
            event_sink=lambda event_type, data: events.append((event_type, data)),
        )
        council = PlanningCouncil(
            {role: fake_planner(lambda role, prompt: plan) for role in PLANNER_ROLES}
        )
        council.run(ctx)
        self.assertTrue(any(item[0] == "adaptive.repo_map" for item in events))
        self.assertEqual(events[0][1]["mode"], "shadow")


if __name__ == "__main__":
    unittest.main()
