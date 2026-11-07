#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2-01: safe Telegram/v2 event notifications."""

from __future__ import annotations

import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from v2 import alerts  # noqa: E402
from v2.runner import V2Runner  # noqa: E402
from test_v2_m3 import cleanup, make_plan, make_repo  # noqa: E402


def main() -> int:
    sent: list[str] = []

    def fake_send(text: str, **_kwargs):
        sent.append(text)
        return {"ok": True, "status": 200}

    root = Path(__file__).resolve().parents[1]
    original = alerts.notify.send
    alerts.notify.send = fake_send
    try:
        for event in ("HANDOFF_READY", "USAGE_WAITING", "BUDGET_OVERRUN", "CRASH"):
            result = alerts.notify_event(
                root, event,
                {
                    "run_id": "v2_demo",
                    "status": event,
                    "phase_id": "P1",
                    "reason": "api_key='sk-ant-abcdefghijklmnopqrstuvwxyz' "
                              r"at C:\Users\tuank\secret folder\id_rsa",
                },
                config={"enabled": True},
            )
            assert result["ok"] is True, result
    finally:
        alerts.notify.send = original

    assert len(sent) == 4, sent
    combined = "\n".join(sent)
    assert "HANDOFF_READY" in combined and "USAGE_WAITING" in combined
    assert "BUDGET_OVERRUN" in combined and "CRASH" in combined
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz" not in combined
    assert "C:\\Users\\tuank\\secret" not in combined
    assert "[REDACTED_SECRET]" in combined or "[REDACTED_PATH]" in combined
    assert "run=v2_demo" in combined

    # Disabled config is a clean no-op, never a crash.
    disabled = alerts.notify_event(root, "CRASH", {"run_id": "x"}, config={"enabled": False})
    assert disabled["skipped"] is True

    # No configured webhook: fail closed by skipping, never raising.
    no_config = alerts.notify_event(root, "CRASH", {"run_id": "x"}, config={})
    assert no_config["ok"] is False and no_config["skipped"] is True

    # v2 plan submission must emit USAGE_WAITING through the safe bridge.
    hook_root = make_repo("p2-alert-hook")
    hook_wt = hook_root.parent / "p2-alert-hook-wt"
    runner = V2Runner(hook_root, worktree_root=hook_wt)
    events: list[tuple[str, dict]] = []
    original_event = alerts.notify_event
    alerts.notify_event = lambda _root, event, payload=None, **kw: (
        events.append((event, dict(payload or {}))) or {"ok": True}
    )
    try:
        prepared = runner.prepare("alert hook", phase_ids=["P1"])
        plan = make_plan(prepared["run_id"], prepared["workspace"]["base_sha"])
        runner.submit_plan(prepared["run_id"], plan)
        assert any(event == "USAGE_WAITING" for event, _payload in events), events
    finally:
        alerts.notify_event = original_event
        cleanup(runner, hook_root, hook_wt)

    print("V2 ALERTS: 5/5 PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
