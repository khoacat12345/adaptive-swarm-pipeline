#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2-02/P2-08: v2 navigation, spend table and handoff diff preview."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from v2 import web  # noqa: E402
from test_v2_m3 import make_repo  # noqa: E402


def main() -> int:
    root = make_repo("p2-web")
    checks = 0
    try:
        run_dir = root / ".agents" / "tmp" / "v2" / "runs" / "run-demo"
        run_dir.mkdir(parents=True)
        manifest = {
            "schema_version": "2.1",
            "run_id": "run-demo",
            "goal": "UI spend test",
            "status": "HANDOFF_READY",
            "created_at": "2026-11-07T00:00:00Z",
            "updated_at": "2026-11-07T00:00:00Z",
            "plan_hash": "abc",
            "workspace": {},
            "checkpoints": [],
        }
        (run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        plan = {
            "schema_version": "2.1",
            "run_id": "run-demo",
            "goal": "UI spend test",
            "phases": [
                {"id": "P1", "budget": {"requests": 5, "cost_usd": 0.1, "wall_seconds": 60},
                 "work_orders": []}
            ],
        }
        (run_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        budget = {
            "config": {"global": {"max_requests": 10, "max_cost_usd": 0.5}},
            "global": {"used_requests": 3, "used_cost_usd": 0.02, "used_tokens": 4096,
                       "reserved_requests": 1, "reserved_cost_usd": 0.01},
            "phases": {"P1": {"used_requests": 3, "used_cost_usd": 0.02}},
        }
        (run_dir / "budget.json").write_text(json.dumps(budget), encoding="utf-8")
        handoff = run_dir / "handoff"
        handoff.mkdir()
        (handoff / "handoff.patch").write_text("diff --git a/x b/x\n+hello\n", encoding="utf-8")
        (handoff / "report.md").write_text("# handoff\n", encoding="utf-8")

        detail = web.run_detail(root, "run-demo")
        checks += 1; assert detail["spend"]["estimated_requests"] == 5
        checks += 1; assert detail["spend"]["used_requests"] == 3
        checks += 1; assert detail["spend"]["remaining_requests"] == 6
        checks += 1; assert detail["spend"]["remaining_cost_usd"] == 0.47
        checks += 1; assert detail["spend"]["used_tokens"] == 4096
        checks += 1; assert detail["spend"]["estimated_tokens"] > 0
        checks += 1; assert detail["spend"]["remaining_tokens"] >= 0
        checks += 1; assert detail["spend"]["phases"]["P1"]["used_cost_usd"] == 0.02
        checks += 1; assert detail["handoff"]["ready"] is True

        runs = web.list_runs(root)
        checks += 1; assert runs["runs"][0]["handoff"] is True

        diff = web.handoff_diff(root, "run-demo")
        checks += 1; assert "+hello" in diff["diff"]
        checks += 1; assert "handoff" in diff["report"]

        page = web.page()
        for marker in ("Back to Dashboard", "Spend reconciliation", "Recent handoff runs",
                       "Preview handoff diff", "Tokens (estimated vs actual)", "Estimated", "Actual / used"):
            checks += 1; assert marker in page, marker
        checks += 1; assert "/api/v2/runs/" in page and "/diff" in page

        dashboard = (AGENTS_DIR / "dashboard_ui.html").read_text(encoding="utf-8")
        checks += 1; assert "href='/v2'" in dashboard or 'href="/v2"' in dashboard

        node = shutil.which("node")
        if node:
            for label, script_text in (("v2", re.search(r"<script>(.*)</script>", page, re.DOTALL).group(1)),
                                       ("dashboard", re.search(r"<script[^>]*>(.*)</script>", dashboard, re.DOTALL).group(1))):
                script_path = root / f"{label}_page.js"
                script_path.write_text(script_text, encoding="utf-8")
                result = subprocess.run([node, "--check", str(script_path)],
                                        capture_output=True, text=True, timeout=30)
                checks += 1; assert result.returncode == 0, result.stderr
        print(f"V2 WEB UI: {checks}/{checks} PASS")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
