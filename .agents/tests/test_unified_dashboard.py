#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 3 acceptance: unified state reader, War Room API and loopback safety."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import dashboard  # noqa: E402
from unified import api as unified_api  # noqa: E402
from unified import state as unified_state  # noqa: E402
from v2 import isolation  # noqa: E402
from v2.runner import V2Runner  # noqa: E402
from test_v2_m3 import make_plan, make_repo  # noqa: E402


def main() -> int:
    checks = 0
    root = make_repo("phase3-dashboard")
    runner = V2Runner(root)
    run_id = None
    hroot = None
    try:
        # A real v2 run supplies a token and a manifest for API tests.
        prepared = runner.prepare("dashboard acceptance", phase_ids=["P1"])
        run_id = prepared["run_id"]
        token = prepared["signoff_token"]
        plan = make_plan(run_id, prepared["workspace"]["base_sha"])
        (runner.load(run_id).run_dir / "plan.json").write_text(
            json.dumps(plan), encoding="utf-8")
        store = runner.load(run_id)
        store.update(status="AWAITING_SIGNOFF")
        checks += 1; assert runner.load(run_id).load()["status"] == "AWAITING_SIGNOFF"

        # Unified state projection and public API.
        listing = unified_api.list_runs(root)
        checks += 1; assert any(item["run_id"] == run_id for item in listing["runs"])
        detail = unified_api.run_detail(root, run_id)
        checks += 1; assert detail["run_id"] == run_id
        checks += 1; assert "spend" in detail and "verify" in detail and "handoff" in detail
        checks += 1; assert detail["status"] == "AWAITING_SIGNOFF"
        checks += 1; assert detail["can_control"]["usage_approve"] is True

        status, payload = unified_api.handle_get(
            root, "/api/unified/runs/" + run_id)
        checks += 1; assert status == 200 and payload["run_id"] == run_id
        status, payload = unified_api.handle_get(root, "/api/unified/runs")
        checks += 1; assert status == 200 and "runs" in payload

        # Approval endpoint is token-bound and changes the real state.
        status, approved = unified_api.handle_post(
            root, "/api/unified/usage/approve",
            {"run_id": run_id, "token": token},
        )
        checks += 1; assert status == 200 and approved["ok"] is True
        checks += 1; assert runner.load(run_id).load()["status"] == "PROVISIONING"

        # Invalid token is rejected, never accidentally approved.
        status, denied = unified_api.handle_post(
            root, "/api/unified/usage/approve",
            {"run_id": run_id, "token": "wrong-token"},
        )
        checks += 1; assert status == 403 and "error" in denied

        # Handoff diff and merge/rollback API use a disposable temp state.
        hroot = make_repo("phase3-merge")
        hrunner = V2Runner(hroot)
        hprepared = hrunner.prepare("dashboard merge", phase_ids=["P1"])
        hrid = hprepared["run_id"]
        htoken = hprepared["signoff_token"]
        hplan = make_plan(hrid, hprepared["workspace"]["base_sha"])
        hrun_dir = hrunner.load(hrid).run_dir
        (hrun_dir / "plan.json").write_text(json.dumps(hplan), encoding="utf-8")
        handoff = hrun_dir / "handoff"
        handoff.mkdir()
        patch = (
            "diff --git a/src.py b/src.py\n"
            "--- a/src.py\n"
            "+++ b/src.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 1\n"
            "+VALUE = 2\n"
        )
        (handoff / "handoff.patch").write_text(patch, encoding="utf-8")
        (handoff / "report.md").write_text("# report\n", encoding="utf-8")
        hrunner.load(hrid).update(status="HANDOFF_READY")

        diff = unified_api.run_diff(hroot, hrid)
        checks += 1; assert "+VALUE = 2" in diff["diff"]
        checks += 1; assert diff["truncated"] is False

        status, merged = unified_api.handle_post(
            hroot, "/api/unified/merge",
            {"run_id": hrid, "token": htoken, "verify_timeout": 60},
        )
        checks += 1; assert status == 200 and merged["ok"] is True
        checks += 1; assert "VALUE = 2" in (hroot / "src.py").read_text(encoding="utf-8")

        status, rolled = unified_api.handle_post(
            hroot, "/api/unified/rollback",
            {"run_id": hrid, "token": htoken},
        )
        checks += 1; assert status == 200 and rolled["ok"] is True
        checks += 1; assert "VALUE = 1" in (hroot / "src.py").read_text(encoding="utf-8")

        # No remote bind: the dashboard only advertises loopback.
        checks += 1; assert dashboard.DEFAULT_PORT == 8501
        server = dashboard.make_server(0, root=root)
        try:
            checks += 1; assert server.server_address[0] in {"127.0.0.1", "::1"}
        finally:
            server.server_close()

        # Event and size-cap surface.
        events = unified_api.run_events(root, run_id, limit=5)
        checks += 1; assert "items" in events and "next_cursor" in events
        capped = unified_api.run_diff(hroot, hrid, max_bytes=32)
        checks += 1; assert capped["truncated"] is True

        # UI is present and includes all seven panel labels plus the v2 plan
        # context.  The latter prevents the legacy v1 plan from masking an
        # active v2 run in the Plan tab.
        ui = (AGENTS_DIR / "dashboard_ui.html").read_text(encoding="utf-8")
        for marker in ("Run timeline", "Tier", "Spend", "Usage approval",
                       "Blackboard", "Handoff", "Run history",
                       "LIVE V2 PLAN", "planUnified", "toggleLegacyPlan"):
            checks += 1; assert marker.lower() in ui.lower(), marker

        # The user-facing surface is now a single-run viewer. The old SPA is
        # retained only as the legacy/test surface and is not served by default.
        checks += 1; assert "Run Viewer" in dashboard.RUN_HTML
        checks += 1; assert "Logbook" in dashboard.RUN_HTML
        checks += 1; assert "War Room" not in dashboard.RUN_HTML
        checks += 1; assert "Run history" not in dashboard.RUN_HTML
        checks += 1; assert "single-run only" in dashboard.REQUIRED_HTML

        print(f"UNIFIED DASHBOARD: {checks}/{checks} PASS")
        return 0
    finally:
        if run_id:
            try:
                isolation.remove_worktree(runner.workspace(run_id), keep_branch=False)
            except Exception:
                pass
        shutil.rmtree(root, ignore_errors=True)
        hroot = root.parent / "phase3-merge"
        if hroot.exists():
            try:
                hrunner = V2Runner(hroot)
                for item in (hroot / ".agents" / "tmp" / "v2" / "runs").glob("*"):
                    manifest = item / "run_manifest.json"
                    if not manifest.exists():
                        continue
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                    if payload.get("workspace"):
                        try:
                            isolation.remove_worktree(
                                isolation.Workspace.from_dict(payload["workspace"]),
                                keep_branch=False,
                            )
                        except Exception:
                            pass
            except Exception:
                pass
            shutil.rmtree(hroot, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
