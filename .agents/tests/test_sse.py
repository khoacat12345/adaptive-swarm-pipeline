#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 4 acceptance - SSE stream and meeting dashboard view model.

    python .agents/tests/test_sse.py
    python .agents/tests/test_meeting_ui.py
"""

from __future__ import annotations

import http.client
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

import dashboard  # noqa: E402


class Checks:
    def __init__(self) -> None:
        self.total = 0
        self.failures: list[str] = []

    def check(self, name: str, condition, detail="") -> bool:
        self.total += 1
        ok = bool(condition)
        if not ok:
            self.failures.append(f"{name}: {detail}" if detail else name)
        return ok

    @property
    def passed(self) -> int:
        return self.total - len(self.failures)


def _temp_root(name: str) -> Path:
    root = Path(tempfile.gettempdir()) / "opencode" / f"phase4_{name}"
    shutil.rmtree(root, ignore_errors=True)
    (root / ".agents" / "tmp").mkdir(parents=True, exist_ok=True)
    (root / ".agents" / "tmp" / "plan_token.txt").write_text("test-token", encoding="utf-8")
    return root


def _event(run_id: str, event_type: str, data: dict | None = None,
           result: dict | None = None) -> dict:
    return {
        "run_id": run_id,
        "timestamp": "2026-10-24T00:00:00Z",
        "event_type": event_type,
        "data": data or {},
        "result": result or {},
    }


def _append(path: Path, event: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _start_server(root: Path, ledger_path: Path):
    server = dashboard.make_server(0, root=root, ledger=ledger_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _get_status(port: int, path: str) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        return response.status, body
    finally:
        conn.close()


def _open_stream(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    conn.request("GET", path)
    response = conn.getresponse()
    return conn, response


def _read_sse_event(response, *, deadline=6.0):
    """Read one ledger frame; return (id, payload)."""
    end = time.time() + deadline
    event_id = None
    event_type = None
    data_lines = []
    while time.time() < end:
        line = response.readline().decode("utf-8", errors="replace")
        if not line:
            break
        line = line.rstrip("\r\n")
        if line.startswith("id: "):
            event_id = line[4:]
        elif line.startswith("event: "):
            event_type = line[7:]
        elif line.startswith("data: "):
            data_lines.append(line[6:])
        elif line == "" and event_type == "ledger" and data_lines:
            return event_id, json.loads("\n".join(data_lines))
    return event_id, None


def test_sse(checks: Checks) -> None:
    root = _temp_root("sse")
    ledger_path = root / ".agents" / "ledger.jsonl"
    _append(ledger_path, _event("r1", "recipe.start", {"task_id": "T1"}))
    server, thread = _start_server(root, ledger_path)
    port = server.server_address[1]
    try:
        status, _ = _get_status(port, "/api/stream?token=bad&run_id=r1")
        checks.check("sse: bad token 403", status == 403, status)

        conn, response = _open_stream(port, "/api/stream?token=test-token&run_id=r1&offset=0")
        try:
            checks.check("sse: 200", response.status == 200, response.status)
            ctype = response.getheader("Content-Type") or ""
            checks.check("sse: content type", ctype.startswith("text/event-stream"), ctype)
            checks.check("sse: no-cache", (response.getheader("Cache-Control") or "") == "no-cache",
                         response.getheader("Cache-Control"))
            event_id, payload = _read_sse_event(response)
            checks.check("sse: first event", payload is not None and payload.get("event_type") == "recipe.start",
                         payload)
            checks.check("sse: event id", bool(event_id), event_id)
        finally:
            response.close()
            conn.close()

        # Reconnect after the first byte offset: the first event must not repeat.
        _append(ledger_path, _event("r1", "recipe.coder", {"task_id": "T1", "round": 1}))
        conn, response = _open_stream(
            port, f"/api/stream?token=test-token&run_id=r1&offset={event_id}")
        try:
            _id, payload = _read_sse_event(response)
            checks.check("sse: reconnect no duplicate",
                         payload is not None and payload.get("event_type") == "recipe.coder",
                         payload)
        finally:
            response.close()
            conn.close()

        # A filtered stream must not send another run's events.
        _append(ledger_path, _event("other", "recipe.start", {"task_id": "OTHER"}))
        _append(ledger_path, _event("r1", "recipe.done", {"task_id": "T1", "status": "applied"}))
        conn, response = _open_stream(
            port, "/api/stream?token=test-token&run_id=r1&offset=0")
        try:
            seen = []
            for _ in range(4):
                _id, payload = _read_sse_event(response, deadline=2.0)
                if payload is None:
                    break
                seen.append(payload.get("run_id"))
            checks.check("sse: run filter", all(item == "r1" for item in seen) and seen,
                         seen)
        finally:
            response.close()
            conn.close()

        # HTTP handler shutdown is asynchronous. Poll briefly instead of
        # assuming macOS schedules the close within 400 ms.
        deadline = time.time() + 3.0
        alive = []
        while time.time() < deadline:
            alive = [item for item in threading.enumerate()
                     if item is not thread and item.is_alive() and "Thread-" in item.name]
            if not alive:
                break
            time.sleep(0.1)
        checks.check("sse: handler threads cleaned", not alive, alive)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def main() -> int:
    checks = Checks()
    print("[sse] ...")
    test_sse(checks)
    print("")
    print(f"SSE: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
