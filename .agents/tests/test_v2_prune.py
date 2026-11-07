#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2-04: prune stale v2 worktrees while preserving evidence."""

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

from v2 import isolation  # noqa: E402
from v2.prune import prune  # noqa: E402
from v2.runner import V2Runner  # noqa: E402
from test_v2_m3 import git, make_repo  # noqa: E402


def main() -> int:
    root = make_repo("p2-prune")
    wt_root = root.parent / "p2-prune-wt"
    shutil.rmtree(wt_root, ignore_errors=True)
    runner = V2Runner(root, worktree_root=wt_root)
    try:
        prepared = runner.prepare("prune test", phase_ids=["P1"])
        run_id = prepared["run_id"]
        workspace = runner.workspace(run_id)
        assert workspace.path.is_dir()
        store = runner.load(run_id)
        store.update(status="HANDOFF_READY")
        handoff = root / ".agents" / "tmp" / "v2" / "runs" / run_id / "handoff"
        handoff.mkdir(parents=True, exist_ok=True)
        (handoff / "handoff.patch").write_text("diff --git a/x b/x\n", encoding="utf-8")
        (handoff / "report.md").write_text("# report\n", encoding="utf-8")

        result = prune(root, days=7, force=False)
        assert result["ok"] is True, result
        assert any(item["run_id"] == run_id for item in result["removed"]), result
        assert not workspace.path.exists(), "worktree was not removed"
        branch = git(root, "branch", "--list", workspace.branch).stdout.strip()
        assert not branch, branch
        assert (handoff / "handoff.patch").exists(), "handoff patch must be retained"
        assert (handoff / "report.md").exists(), "handoff report must be retained"
        assert store.manifest_path.exists(), "checkpoint/run manifest must be retained"

        # Active runs require force, and force does not bypass active safety.
        runner2 = V2Runner(root, worktree_root=wt_root, require_clean=False)
        prepared2 = runner2.prepare("prune active test", phase_ids=["P1"])
        run2 = prepared2["run_id"]
        skipped = prune(root, days=0, force=False)
        assert any(item["run_id"] == run2 and "active" in item["reason"]
                   for item in skipped["skipped"]), skipped
        forced = prune(root, days=0, force=True)
        assert not any(item["run_id"] == run2 for item in forced["removed"]), forced
        runner.load(run2).update(status="HANDOFF_READY")
        cleaned = prune(root, days=0, force=True, dry_run=True)
        assert any(item["run_id"] == run2 for item in cleaned["removed"]), cleaned
        print("V2 PRUNE: 10/10 PASS")
        return 0
    finally:
        try:
            for run_dir in (root / ".agents" / "tmp" / "v2" / "runs").glob("*"):
                manifest = run_dir / "run_manifest.json"
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
        finally:
            shutil.rmtree(wt_root, ignore_errors=True)
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
