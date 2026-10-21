#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 6B - planner (goal -> plan.json) + notify webhook.

    python .agents/tests/test_planner.py
    python .agents/tests/test_planner.py --fast   # bo qua hoi quy
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import job                            # noqa: E402
import notify                         # noqa: E402
import planner                        # noqa: E402

PY = f'"{sys.executable}"'
TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "planner_tests"


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


def make_root(name: str) -> Path:
    root = TEMP_ROOT / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "a.py").write_text("def a():\n    return 0\n", encoding="utf-8")
    (root / "check_a.py").write_text(
        "from a import a\nassert a() == 1\nprint('OK')\n", encoding="utf-8")
    return root


def make_plan(targets=None, verify=None) -> dict:
    return {
        "job_id": "J1",
        "objective": "fix a",
        "subtasks": [{
            "id": "S1", "title": "fix a", "objective": "fix a",
            "verify_command": verify or f"{PY} check_a.py",
            "target_files": targets or ["a.py"],
            "sandbox_files": ["a.py", "check_a.py"],
            "level": 1, "max_rounds": 1, "depends_on": [],
        }],
    }


def test_prompt(checks: Checks) -> None:
    root = make_root("prompt")
    tree = planner.project_tree(root)
    checks.check("planner: tree co a.py", "a.py" in tree, tree)
    prompt = planner.build_prompt("them auth", root)
    checks.check("planner: prompt co goal", "them auth" in prompt)
    checks.check("planner: prompt co schema", '"subtasks"' in prompt and "depends_on" in prompt)
    checks.check("planner: gioi han 5 subtask", str(planner.MAX_SUBTASKS) in prompt)


def test_parse(checks: Checks) -> None:
    ok = planner.parse_plan('```json\n{"job_id":"J1","subtasks":[]}\n```')
    checks.check("planner: parse json trong code fence", ok["job_id"] == "J1", ok)
    try:
        planner.parse_plan("khong phai json")
        failed = False
    except Exception:
        failed = True
    checks.check("planner: parse rac -> loi", failed)


def test_generate(checks: Checks) -> None:
    root = make_root("generate")
    calls: list[str] = []

    def good(_model, _prompt):
        calls.append("ok")
        return json.dumps(make_plan())

    plan = planner.generate("fix a", root=root, runner=good, models=["stub"])
    checks.check("planner: sinh plan hop le", plan["job_id"] == "J1", plan)
    checks.check("planner: 1 lan goi", len(calls) == 1, calls)

    # lan 1 sai target -> retry lan 2 dung
    calls.clear()

    def flaky(_model, _prompt):
        calls.append("call")
        if len(calls) == 1:
            return json.dumps(make_plan(targets=["missing.py"]))
        return json.dumps(make_plan())

    plan = planner.generate("fix a", root=root, runner=flaky, models=["stub"])
    checks.check("planner: retry khi target sai", len(calls) == 2 and plan["subtasks"], calls)

    # ca 2 lan sai -> PlannerError
    def bad(_model, _prompt):
        return json.dumps(make_plan(targets=["missing.py"]))

    try:
        planner.generate("fix a", root=root, runner=bad, models=["stub"])
        raised = False
    except planner.PlannerError:
        raised = True
    checks.check("planner: 2 lan sai -> PlannerError", raised)


# --------------------------------------------------------------------------
class Hook(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):                              # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8")
        try:
            Hook.received.append(json.loads(body))
        except json.JSONDecodeError:
            Hook.received.append({"raw": body})
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):                   # im lang
        return


def test_notify(checks: Checks) -> None:
    Hook.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Hook)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/hook"
    try:
        result = notify.send("hello", config={"enabled": True, "kind": "generic", "url": url})
        checks.check("notify: gui ok", result.get("ok") is True, result)
        checks.check("notify: body generic", Hook.received[-1].get("text") == "hello",
                     Hook.received[-1])

        Hook.received = []
        notify.send("hi", config={"enabled": True, "kind": "discord", "url": url})
        checks.check("notify: body discord", Hook.received[-1].get("content") == "hi",
                     Hook.received[-1])

        skipped = notify.send("x", config={"enabled": False})
        checks.check("notify: tat -> skip", skipped.get("skipped") is True, skipped)

        messages = [notify.format_message(event, {"job_id": "J1", "subtasks": 2,
                                                  "status": "applied", "seconds": 1.5})
                    for event in ("plan.submitted", "plan.approved", "plan.rejected", "job.done")]
        checks.check("notify: format du 4 event",
                     all("J1" in message for message in messages), messages)

        # tich hop: submit_plan tu ban tin
        Hook.received = []
        root = make_root("notify")
        old = os.environ.get("SWARM_WEBHOOK")
        os.environ["SWARM_WEBHOOK"] = url
        os.environ["SWARM_WEBHOOK_KIND"] = "generic"
        try:
            job.submit_plan(make_plan(), root, token="tok")
        finally:
            if old is None:
                os.environ.pop("SWARM_WEBHOOK", None)
            else:
                os.environ["SWARM_WEBHOOK"] = old
            os.environ.pop("SWARM_WEBHOOK_KIND", None)
        checks.check("notify: submit_plan ban tin",
                     any("New plan" in str(item.get("text", "")) for item in Hook.received),
                     Hook.received)
    finally:
        server.shutdown()
        server.server_close()


def test_no_regression(checks: Checks, *, fast: bool = False) -> None:
    if fast:
        print("[skip] hoi quy Phase 6A (--fast)")
        return
    started = subprocess.run([sys.executable, str(AGENTS_DIR / "tests" / "test_phase6.py"),
                              "--fast"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=900)
    checks.check("khong hoi quy: Phase 6A", started.returncode == 0,
                 (started.stdout or "")[-300:])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Planner + notify tests.")
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args(argv)

    checks = Checks()
    print("[1/5] prompt/tree ...")
    test_prompt(checks)
    print("[2/5] parse ...")
    test_parse(checks)
    print("[3/5] generate/retry ...")
    test_generate(checks)
    print("[4/5] notify ...")
    test_notify(checks)
    print("[5/5] khong hoi quy ...")
    test_no_regression(checks, fast=args.fast)

    print("")
    print(f"PLANNER: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    sys.exit(main())
