#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-14: every execution tier receives the same adaptive capability bundle."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from v2.capability_runtime import build_context  # noqa: E402
from v2.event_runtime import EventStore, make_event  # noqa: E402
from v2.execution import ExecutionEngine  # noqa: E402
from v2.lesson_runtime import FailureLessonRuntime  # noqa: E402
from test_v2_m3 import (  # noqa: E402
    Checks,
    StaticCoder,
    cleanup,
    setup_run,
)

ROOT = Path(tempfile.gettempdir()) / "opencode" / "capability_uniform_tests"


class CaptureCoder(StaticCoder):
    def __init__(self, contents, seen):
        super().__init__(contents)
        self.seen = seen

    def propose(self, work_order, **kwargs):
        self.seen.append(str(work_order.get("input_context") or ""))
        return super().propose(work_order, **kwargs)


def main() -> int:
    shutil.rmtree(ROOT, ignore_errors=True)
    ROOT.mkdir(parents=True)
    checks = Checks()
    (ROOT / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
    (ROOT / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    lessons = FailureLessonRuntime.for_root(ROOT)
    lessons.record_failure(kind="test", error="previous verify failed",
                           files=["src.py"], fix="inspect src.py")
    events = EventStore(ROOT / ".agents" / "tmp" / "v2" / "events.ndjson")
    events.append(make_event(run_id="r1", event_type="demo.event",
                             data={"ok": True}))

    for tier in ("E1", "E2", "E3", "E4"):
        bundle = build_context(
            ROOT, ROOT, "update src.py", tier, target_files=["src.py"],
        )
        checks.check(f"{tier}: repo map loaded", "repo_map" in bundle.components,
                     bundle.as_dict())
        checks.check(f"{tier}: ACI loaded", "aci" in bundle.components,
                     bundle.as_dict())
        checks.check(f"{tier}: lessons loaded", "lessons" in bundle.components,
                     bundle.as_dict())
        checks.check(f"{tier}: events loaded", "events" in bundle.components,
                     bundle.as_dict())
        checks.check(f"{tier}: bounded memory text", "## working" in bundle.text,
                     bundle.text[:200])

    seen: list[str] = []
    coder = CaptureCoder([{"src.py": "VALUE = 2\n"}], seen)
    runner, root, wt_root, run_id, coder_seat, reviewers, plan = setup_run(
        "capability-exec", coder)
    try:
        changed = dict(plan)
        changed["execution_tier"] = "E2"
        runner.submit_plan(run_id, changed)
        runner.signoff(run_id, approved_by="capability")
        coder_seat = coder_seat.__class__(
            "coder", "mock/deepseek", "deepseek", coder)
        result = ExecutionEngine(runner).execute(
            run_id, coder=coder_seat, reviewers=reviewers,
        )
        checks.check("execution: E2 completes", result.status == "HANDOFF_READY",
                     result.as_dict())
        checks.check("execution: coder received capability context",
                     bool(seen) and "TASK-AWARE REPO MAP" in seen[0], seen)
        events = runner.load(run_id).read_events()
        checks.check("execution: capability event recorded",
                     any(item.get("event_type") == "capability.context"
                         for item in events), [item.get("event_type") for item in events])
    finally:
        cleanup(runner, root, wt_root)
        shutil.rmtree(ROOT, ignore_errors=True)

    print(f"CAPABILITIES UNIFORM: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
