#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 0 tests: adaptive flags and byte-stable planner prompt."""

from __future__ import annotations

import hashlib
import os
import sys
import unittest
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2.adaptive_flags import (  # noqa: E402
    AdaptiveFlagError,
    AdaptiveFlags,
    FLAG_NAMES,
    load_flags,
)
from v2.planning import PlanningContext, proposal_prompt  # noqa: E402

# Golden hash of the architect prompt with all adaptive flags off.
# Update only with an intentional prompt change and a reviewed commit.
BASELINE_ARCHITECT_PROMPT_SHA256 = (
    "8653c923b3ab065d171469737c422d3de80b56bdd4f0cc711b83ee804965b43b"
)


def baseline_context() -> PlanningContext:
    return PlanningContext(
        goal="baseline task",
        run_id="baseline-run",
        base_sha="a" * 40,
        worktree=Path(".").resolve(),
        project_tree="- README.md\n- .agents/v2/planning.py",
        budget={"requests": 120, "cost_usd": 3.5, "wall_seconds": 36000},
        model_snapshot={"coder": "mock"},
        max_rounds=1,
    )


class AdaptiveFlagTests(unittest.TestCase):
    def test_defaults_are_all_off(self) -> None:
        flags = AdaptiveFlags()
        self.assertTrue(flags.all_off)
        for name in FLAG_NAMES:
            self.assertEqual(flags.get(name), "off")
            self.assertFalse(flags.enabled(name))
            self.assertFalse(flags.shadow(name))
            self.assertFalse(flags.active(name))

    def test_modes_are_parsed(self) -> None:
        flags = AdaptiveFlags.from_env({
            "SWARM_ADAPTIVE_REPO_MAP": "shadow",
            "SWARM_ADAPTIVE_ACI": "ON",
            "SWARM_ADAPTIVE_LESSONS": "off",
            "SWARM_ADAPTIVE_MEMORY_BUDGET": "shadow",
            "SWARM_ADAPTIVE_EVENTS": "on",
        })
        self.assertTrue(flags.shadow("REPO_MAP"))
        self.assertTrue(flags.enabled("ACI"))
        self.assertFalse(flags.active("LESSONS"))
        self.assertEqual(flags.as_dict()["EVENTS"], "on")

    def test_invalid_value_fails_closed(self) -> None:
        with self.assertRaises(AdaptiveFlagError):
            AdaptiveFlags.from_env({"SWARM_ADAPTIVE_REPO_MAP": "enabled"})
        with self.assertRaises(AdaptiveFlagError):
            AdaptiveFlags.from_env({"SWARM_ADAPTIVE_ACI": "1"})

    def test_invalid_value_can_be_disabled_in_non_strict_mode(self) -> None:
        flags = load_flags({"SWARM_ADAPTIVE_EVENTS": "typo"}, strict=False)
        self.assertTrue(flags.all_off)
        self.assertFalse(flags.enabled("EVENTS"))

    def test_unknown_flag_name_rejected(self) -> None:
        with self.assertRaises(AdaptiveFlagError):
            AdaptiveFlags().get("NOT_A_FLAG")

    def test_prompt_is_byte_stable_when_flags_are_off(self) -> None:
        prompt = proposal_prompt("architect", baseline_context())
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        self.assertEqual(digest, BASELINE_ARCHITECT_PROMPT_SHA256)

    def test_flags_do_not_add_prompt_content_when_off(self) -> None:
        flags = AdaptiveFlags()
        prompt = proposal_prompt("architect", baseline_context())
        self.assertNotIn("TASK-AWARE REPO MAP", prompt)
        self.assertNotIn("PRIOR FAILURE LESSONS", prompt)
        self.assertTrue(flags.all_off)


if __name__ == "__main__":
    unittest.main()
