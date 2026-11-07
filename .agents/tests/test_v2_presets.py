#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2-09: free-only and strict-security preset behavior."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from v2 import isolation, presets  # noqa: E402
from v2.runner import V2Runner  # noqa: E402
from test_v2_m3 import make_plan, make_repo  # noqa: E402


def main() -> int:
    checks = 0
    checks += 1; assert presets.normalize("free-only") == "free-only"
    checks += 1; assert presets.config_for("free-only")["paid_approval"] == "never"
    checks += 1; assert presets.strict_thresholds("strict").request_threshold == 1
    checks += 1; assert presets.strict_thresholds("strict").cost_threshold_usd == 0.01
    checks += 1; assert presets.strict_thresholds(None) is None
    try:
        presets.normalize("unknown")
        raise AssertionError("unknown preset accepted")
    except Exception:
        checks += 1

    root = make_repo("p2-presets")
    runner = V2Runner(root)
    run_ids: list[str] = []
    try:
        prepared = runner.prepare("free preset", phase_ids=["P1"], preset="free-only")
        free_run = prepared["run_id"]; run_ids.append(free_run)
        manifest = runner.load(free_run).load()
        checks += 1; assert prepared["preset"] == "free-only"
        checks += 1; assert manifest["preset_config"]["paid_approval"] == "never"
        checks += 1; assert manifest["preset_config"]["reviewers"] == 4
        plan = make_plan(free_run, prepared["workspace"]["base_sha"])
        (runner.load(free_run).run_dir / "plan.json").write_text(
            __import__("json").dumps(plan), encoding="utf-8")
        decision = runner.usage_decision(free_run, plan)
        checks += 1; assert "paid_model" not in decision.reasons

        strict_runner = V2Runner(root, require_clean=False)
        strict = strict_runner.prepare("strict preset", phase_ids=["P1"], preset="strict")
        strict_run = strict["run_id"]; run_ids.append(strict_run)
        manifest = runner.load(strict_run).load()
        strict_plan = make_plan(strict_run, strict["workspace"]["base_sha"])
        (runner.load(strict_run).run_dir / "plan.json").write_text(
            __import__("json").dumps(strict_plan), encoding="utf-8")
        strict_decision = runner.usage_decision(strict_run, strict_plan)
        checks += 1; assert strict_decision.required is True
        checks += 1; assert strict_decision.thresholds.request_threshold == 1
        checks += 1; assert manifest["preset_config"]["secret_scan"] is True
        checks += 1; assert manifest["preset_config"]["reviewers"] == 4
        print(f"V2 PRESETS: {checks}/{checks} PASS")
        return 0
    finally:
        for run_id in run_ids:
            try:
                isolation.remove_worktree(runner.workspace(run_id), keep_branch=False)
            except Exception:
                pass
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
