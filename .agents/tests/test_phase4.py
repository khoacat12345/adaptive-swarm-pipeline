#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 4 - schema / replay / dashboard / memory / khong hoi quy.

    python .agents/tests/test_phase4.py
    python .agents/tests/test_phase4.py --fast     # bo qua 4 suite hoi quy

DoD:
  1. replay.py --last in dung timeline cua run gan nhat.
  2. dashboard: GET / = 200, /api/runs tra JSON.
  3. memory.py: rut duoc it nhat 1 quy luat loi tu ledger.
  4. khong hoi quy: Phase 0, 0.5, 1, 3 van PASS.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import dashboard                       # noqa: E402
import memory                          # noqa: E402
import recipe_c                        # noqa: E402
import replay                          # noqa: E402
import schema                          # noqa: E402
import work_order as work_order_module  # noqa: E402
import workers                         # noqa: E402

PY = f'"{sys.executable}"'
TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "phase4_tests"

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
BROKEN = "def add(a, b)\n    return a + b\n"          # thieu dau ':' -> SyntaxError
CHECK_PY = "from calc import add\nassert add(2, 3) == 5\nprint('OK')\n"


class Checks:
    def __init__(self) -> None:
        self.total = 0
        self.failures: list[str] = []

    def check(self, name: str, condition, detail: str = "") -> bool:
        self.total += 1
        ok = bool(condition)
        if not ok:
            self.failures.append(f"{name}: {detail}" if detail else name)
        return ok

    @property
    def passed(self) -> int:
        return self.total - len(self.failures)


class StaticCoder(workers.Coder):
    def __init__(self, content: str) -> None:
        super().__init__()
        self.content = content
        self.name = "static"
        self.model = "stub/static"

    def patch(self, work_order, *, current_content, feedback, round_index):
        self.calls += 1
        return {"format": "whole", "content": self.content}


def make_project(name: str) -> Path:
    root = TEMP_ROOT / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    (root / "calc.py").write_text(BUGGY, encoding="utf-8")
    (root / "check.py").write_text(CHECK_PY, encoding="utf-8")
    return root


def make_order(**overrides) -> work_order_module.WorkOrder:
    data = {
        "task_id": "P4",
        "title": "phase 4",
        "objective": "Sua ham add trong calc.py de tra ve tong a + b",
        "boundaries": "Chi duoc sua calc.py",
        "input_context": "calc.py chua ham add sai dau",
        "acceptance_criteria": "check.py exit 0 va add(2,3)==5",
        "verify_command": f"{PY} check.py",
        "target_files": ["calc.py"],
        "sandbox_files": ["calc.py", "check.py"],
        "level": 1,
        "max_rounds": 2,
    }
    data.update(overrides)
    return work_order_module.WorkOrder(**data)


# --------------------------------------------------------------------------
# 1. Schema
# --------------------------------------------------------------------------
def test_schema(checks: Checks) -> None:
    legacy = {
        "schema_version": 1,
        "run_id": "r1",
        "timestamp": "2026-10-20T00:00:00Z",
        "event_type": "recipe.draft_verify",
        "duration_ms": 12,
        "usage": {"in_tokens": 10, "out_tokens": 2},
        "result": {"status": "fail", "error": "exit_code=1"},
        "data": {"round": 1, "exit_code": 1},
    }
    norm = schema.normalize(legacy)
    checks.check("schema: event cu -> 9 truong chuan",
                 all(field in norm for field in schema.CANONICAL_FIELDS))
    checks.check("schema: suy ra node systembot",
                 norm["node"] == "systembot", norm["node"])
    checks.check("schema: giu usage", norm["usage"]["in_tokens"] == 10)
    checks.check("schema: details gop data + error",
                 norm["details"].get("exit_code") == 1 and "error" in norm["details"])
    checks.check("schema: validate event cu", not schema.validate(legacy))

    newer = dict(legacy, future_field={"x": 1}, model="opencode/x")
    checks.check("schema: truong moi khong lam gay parser", not schema.validate(newer))
    checks.check("schema: truong la duoc giu trong extra",
                 schema.normalize(newer)["extra"].get("future_field") == {"x": 1})
    checks.check("schema: model doc tu top-level", schema.normalize(newer)["model"] == "opencode/x")

    checks.check("schema: thieu run_id la loi that", bool(schema.validate({"event_type": "x"})))
    checks.check("schema: status la -> info", schema.normalize(dict(legacy, result={"status": "weird"}))["status"] == "info")


# --------------------------------------------------------------------------
# 2. Replay
# --------------------------------------------------------------------------
def test_replay(checks: Checks) -> None:
    root = make_project("replay")
    report = recipe_c.run(make_order(), coder=StaticCoder(FIXED), reviewer=None,
                          root=root, run_id="p4-replay-1", use_memory=False)
    checks.check("replay: task applied", report["status"] == "applied", report["status"])

    ledger_path = root / ".agents" / "ledger.jsonl"
    checks.check("replay: ledger co recipe.done",
                 ledger_path.exists() and "recipe.done" in ledger_path.read_text(encoding="utf-8"))

    started = subprocess.run(
        [sys.executable, str(AGENTS_DIR / "replay.py"), "--last",
         "--ledger", str(ledger_path), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    checks.check("replay: CLI exit 0", started.returncode == 0, started.stderr[-300:])
    payload = json.loads(started.stdout or "{}")
    checks.check("replay: chon dung run gan nhat", payload.get("run_id") == "p4-replay-1",
                 str(payload.get("run_id")))
    timeline = payload.get("timeline") or []
    nodes = {item["node"] for item in timeline}
    checks.check("replay: co du Lead/Coder/SystemBot",
                 {"lead", "coder", "systembot"} <= nodes, str(nodes))
    checks.check("replay: event cuoi la ket luan", timeline and timeline[-1]["event_type"] == "recipe.done",
                 str(timeline[-1] if timeline else None))
    checks.check("replay: 0 token (khong goi model)",
                 all(item.get("model") is None or isinstance(item["model"], str) for item in timeline))

    human = subprocess.run(
        [sys.executable, str(AGENTS_DIR / "replay.py"), "--last", "--ledger", str(ledger_path),
         "--no-color"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    checks.check("replay: human output has CONCLUSION",
                 human.returncode == 0 and "CONCLUSION" in human.stdout, human.stdout[-200:])


# --------------------------------------------------------------------------
# 3. Dashboard
# --------------------------------------------------------------------------
def test_dashboard(checks: Checks) -> None:
    root = make_project("dashboard")
    recipe_c.run(make_order(), coder=StaticCoder(FIXED), reviewer=None,
                 root=root, run_id="p4-dash-1", use_memory=False)
    server = dashboard.make_server(0, root=root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def get(path: str):
        with urllib.request.urlopen(base + path, timeout=10) as response:
            return response.status, response.read().decode("utf-8", errors="replace")

    try:
        status, html = get("/")
        checks.check("dashboard: GET / = 200", status == 200, str(status))
        checks.check("dashboard: tra ve SPA", "Adaptive Swarm" in html and "<main>" in html)

        status, body = get("/api/runs")
        runs = json.loads(body).get("runs") or []
        checks.check("dashboard: /api/runs = JSON co run",
                     status == 200 and any(run["run_id"] == "p4-dash-1" for run in runs),
                     body[:200])

        status, body = get("/api/runs/p4-dash-1")
        data = json.loads(body)
        checks.check("dashboard: /api/runs/<id> co timeline",
                     status == 200 and data.get("timeline"), body[:200])
        checks.check("dashboard: inspector co verify_command",
                     data["run"].get("verify_command") == f"{PY} check.py")

        status, body = get("/api/diff?run_id=p4-dash-1")
        diff = json.loads(body)
        checks.check("dashboard: diff thay thay doi",
                     status == 200 and diff.get("changed_lines", 0) >= 2, body[:200])

        status, body = get("/api/health")
        checks.check("dashboard: /api/health ok",
                     status == 200 and json.loads(body).get("ok") is True)
    finally:
        server.shutdown()
        server.server_close()

    # Chon run gan nhat khi khong truyen id (replay.pick_run)
    events = schema.read_events(root / ".agents" / "ledger.jsonl")
    checks.check("replay: pick_run chon run cuoi", replay.pick_run(events) == "p4-dash-1")


# --------------------------------------------------------------------------
# 4. Memory
# --------------------------------------------------------------------------
def test_memory(checks: Checks) -> None:
    root = make_project("memory")
    order = make_order(max_rounds=1)
    for index in (1, 2):
        coder = StaticCoder(BROKEN)
        recipe_c.run(order, coder=coder, reviewer=None, root=root,
                     run_id=f"p4-mem-{index}", use_memory=(index == 2))
        if index == 2:
            checks.check("memory: hints duoc chen vao coder",
                         bool(coder.hints), str(coder.hints))

    ledger_path = root / ".agents" / "ledger.jsonl"
    events = schema.read_events(ledger_path)
    report = memory.analyze(events)
    checks.check("memory: thay verify that bai", report["verify_failures"] >= 2,
                 str(report["verify_failures"]))
    checks.check("memory: nhan dien loi cu phap",
                 any(item["pattern"] == "syntax" for item in report["error_patterns"]),
                 str(report["error_patterns"]))
    suggestions = memory.get_suggestions(3, ledger=ledger_path)
    checks.check("memory: get_suggestions tra ve loi khuyen", len(suggestions) >= 1,
                 str(suggestions))

    started = subprocess.run(
        [sys.executable, str(AGENTS_DIR / "memory.py"), "--ledger", str(ledger_path), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    payload = json.loads(started.stdout or "{}")
    checks.check("memory: CLI exit 0 + JSON",
                 started.returncode == 0 and payload.get("runs") == 2, started.stderr[-200:])

    # Ledger rong -> khong bia loi khuyen
    empty = memory.analyze([])
    checks.check("memory: khong du lieu -> khong bia", empty["suggestions"] == [])


# --------------------------------------------------------------------------
# 5. Khong hoi quy
# --------------------------------------------------------------------------
def test_no_regression(checks: Checks, *, fast: bool = False) -> None:
    if fast:
        print("[skip] 4 suite hoi quy (--fast)")
        return
    suites = [
        ("Phase 0", [sys.executable, str(AGENTS_DIR / "run_phase0_acceptance.py")]),
        ("Phase 0.5", [sys.executable, str(AGENTS_DIR / "run_phase05_acceptance.py")]),
        ("Phase 1", [sys.executable, str(AGENTS_DIR / "tests" / "smoke_suite.py")]),
        ("Phase 3", [sys.executable, str(AGENTS_DIR / "tests" / "test_phase3.py"), "--fast"]),
    ]
    for name, command in suites:
        started = subprocess.run(command, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=900)
        tail = (started.stdout or "")[-300:]
        checks.check(f"khong hoi quy: {name} PASS", started.returncode == 0,
                     f"exit={started.returncode} tail={tail}")


# --------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Phase 4 acceptance tests.")
    parser.add_argument("--fast", action="store_true", help="bo qua 4 suite hoi quy")
    args = parser.parse_args(argv)

    checks = Checks()
    print("[1/5] schema v1.0.0 ...")
    test_schema(checks)
    print("[2/5] replay timeline ...")
    test_replay(checks)
    print("[3/5] web dashboard ...")
    test_dashboard(checks)
    print("[4/5] memory engine ...")
    test_memory(checks)
    print("[5/5] khong hoi quy (Phase 0 / 0.5 / 1 / 3) ...")
    test_no_regression(checks, fast=args.fast)

    print("")
    print(f"PHASE 4: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    return 0 if checks.passed == checks.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
