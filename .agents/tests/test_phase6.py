#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 6A - job plan + duyet tren dashboard + chay tuan tu.

    python .agents/tests/test_phase6.py
    python .agents/tests/test_phase6.py --fast   # bo qua hoi quy Phase 5

DoD:
  1. Plan validation: thieu verify/id trung/dep sai/vong lap/qua nhieu.
  2. topo_order ton trong depends_on.
  3. submit -> pending -> approve/reject/timeout (token bat buoc).
  4. run_job chay tuan tu, dung khi 1 buoc fail.
  5. dashboard: GET /api/plan, POST decision (403 sai token, 200 dung).
  6. khong hoi quy Phase 5.
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
import urllib.error
import urllib.request
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import dashboard                      # noqa: E402
import job                            # noqa: E402
import work_order as work_order_module  # noqa: E402
import workers                        # noqa: E402

PY = f'"{sys.executable}"'
TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "phase6_tests"


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


def make_plan(**overrides) -> dict:
    plan = {
        "job_id": "J1",
        "objective": "Sua hai file dong bo",
        "subtasks": [
            {"id": "S1", "title": "sua a", "objective": "Sua a.py tra ve 1",
             "verify_command": f"{PY} check_a.py", "target_files": ["a.py"],
             "sandbox_files": ["a.py", "check_a.py"], "level": 1,
             "max_rounds": 1, "depends_on": []},
            {"id": "S2", "title": "sua b", "objective": "Sua b.py tra ve 2",
             "verify_command": f"{PY} check_b.py", "target_files": ["b.py"],
             "sandbox_files": ["b.py", "check_b.py"], "level": 1,
             "max_rounds": 1, "depends_on": ["S1"]},
        ],
    }
    plan.update(overrides)
    return plan


def make_root(name: str) -> Path:
    root = TEMP_ROOT / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "a.py").write_text("def a():\n    return 0\n", encoding="utf-8")
    (root / "check_a.py").write_text(
        "from a import a\nassert a() == 1\nprint('OK a')\n", encoding="utf-8")
    (root / "b.py").write_text("def b():\n    return 0\n", encoding="utf-8")
    (root / "check_b.py").write_text(
        "from b import b\nassert b() == 2\nprint('OK b')\n", encoding="utf-8")
    return root


FIXES = {
    "S1": "def a():\n    return 1\n",
    "S2": "def b():\n    return 2\n",
}


def test_validate(checks: Checks) -> None:
    checks.check("plan: hop le", job.validate_plan(make_plan()) == [],
                 job.validate_plan(make_plan()))

    checks.check("plan: thieu job_id", any("job_id" in p for p in
                 job.validate_plan(make_plan(job_id=""))))
    checks.check("plan: thieu objective", any("objective" in p for p in
                 job.validate_plan(make_plan(objective=""))))

    duplicate = make_plan()
    duplicate["subtasks"][1]["id"] = "S1"
    checks.check("plan: id trung", any("trung lap" in p for p in
                 job.validate_plan(duplicate)))

    no_verify = make_plan()
    no_verify["subtasks"][0]["verify_command"] = ""
    checks.check("plan: thieu verify", any("verify_command" in p for p in
                 job.validate_plan(no_verify)))

    bad_dep = make_plan()
    bad_dep["subtasks"][1]["depends_on"] = ["S9"]
    checks.check("plan: dep khong ton tai", any("S9" in p for p in
                 job.validate_plan(bad_dep)))

    cycle = make_plan()
    cycle["subtasks"][0]["depends_on"] = ["S2"]
    checks.check("plan: vong lap", any("vong lap" in p for p in
                 job.validate_plan(cycle)))

    too_many = make_plan()
    too_many["subtasks"] = [dict(too_many["subtasks"][0], id=f"S{i}")
                            for i in range(job.MAX_SUBTASKS + 1)]
    checks.check("plan: qua nhieu subtask", any("qua nhieu" in p for p in
                 job.validate_plan(too_many)))

    level3 = make_plan()
    level3["subtasks"][0]["level"] = 3
    checks.check("plan: level 3 can >= 2 file", job.validate_plan(level3) != [])


def test_topo(checks: Checks) -> None:
    plan = make_plan()
    ordered = job.topo_order(plan["subtasks"])
    checks.check("topo: phu thuoc truoc", [i["id"] for i in ordered] == ["S1", "S2"],
                 [i["id"] for i in ordered])

    plan = make_plan()
    plan["subtasks"][0]["depends_on"] = ["S2"]
    plan["subtasks"][1]["depends_on"] = []
    ordered = job.topo_order(plan["subtasks"])
    checks.check("topo: dao thu tu theo dep", [i["id"] for i in ordered] == ["S2", "S1"],
                 [i["id"] for i in ordered])


def test_approval(checks: Checks) -> None:
    root = make_root("approval")
    info = job.submit_plan(make_plan(), root, token="tok123")
    checks.check("approval: submit tra token", info["token"] == "tok123")
    checks.check("approval: decision pending",
                 job.read_plan_state(root)["decision"]["status"] == "pending")
    checks.check("approval: khong lo token trong state",
                 "token" not in json.dumps(job.read_plan_state(root)))

    try:
        job.decide(root, "approve", token="sai")
        blocked = False
    except PermissionError:
        blocked = True
    checks.check("approval: sai token bi chan", blocked)

    job.decide(root, "approve", token="tok123")
    checks.check("approval: approve ghi decision",
                 job.read_plan_state(root)["decision"]["status"] == "approved")
    checks.check("approval: wait thay approved",
                 job.wait_for_decision(root, timeout=1, poll=0.05) == "approved")

    job.submit_plan(make_plan(), root, token="tok456")
    job.decide(root, "reject", token="tok456")
    checks.check("approval: reject",
                 job.read_plan_state(root)["decision"]["status"] == "rejected")

    job.submit_plan(make_plan(), root, token="tok789")
    checks.check("approval: timeout",
                 job.wait_for_decision(root, timeout=0.3, poll=0.05) == "timeout")


def test_run_job(checks: Checks) -> None:
    root = make_root("run")
    captured: list[str] = []

    def factory(order, chain):
        captured.append(order.input_context)
        sub = order.task_id.split("-")[-1]
        return workers.ScriptedCoder({order.task_id: [
            {"format": "whole", "content": FIXES[sub]}]})

    job.write_control(root, {"paused": False, "stop": False, "opinion": "use pandas"})
    report = job.run_job(make_plan(), root=root, coder_factory=factory,
                         use_router=False, log=True)
    checks.check("run: opinion injected",
                 bool(captured) and all("USER DIRECTION" in item for item in captured),
                 captured)
    checks.check("run: state done", job.read_state(root).get("phase") == "done",
                 job.read_state(root))
    checks.check("run: applied", report["status"] == "applied", report)
    checks.check("run: 2 subtask", len(report["subtasks"]) == 2, report["subtasks"])
    checks.check("run: ca 2 file doi",
                 (root / "a.py").read_text(encoding="utf-8") == FIXES["S1"]
                 and (root / "b.py").read_text(encoding="utf-8") == FIXES["S2"])
    checks.check("run: ledger co job.start + job.done",
                 "job.start" in (root / ".agents" / "ledger.jsonl").read_text(encoding="utf-8")
                 and "job.done" in (root / ".agents" / "ledger.jsonl").read_text(encoding="utf-8"))

    # Buoc 2 fail -> dung, khong chay tiep
    root2 = make_root("run_fail")

    def bad_factory(order, chain):
        sub = order.task_id.split("-")[-1]
        content = "def b():\n    return 999\n" if sub == "S2" else FIXES[sub]
        return workers.ScriptedCoder({order.task_id: [
            {"format": "whole", "content": content}]})

    report = job.run_job(make_plan(), root=root2, coder_factory=bad_factory,
                         use_router=False, log=True)
    checks.check("run: dung khi buoc fail", report["status"] == "stopped", report)
    checks.check("run: buoc 1 van applied",
                 report["subtasks"][0]["status"] == "applied", report["subtasks"])
    checks.check("run: buoc 2 khong applied",
                 report["subtasks"][1]["status"] != "applied", report["subtasks"])


def test_state_control(checks: Checks) -> None:
    root = make_root("state")
    job.submit_plan(make_plan(), root, token="tokstate", source="plan.json")
    state = job.read_state(root)
    checks.check("state: awaiting_approval", state.get("phase") == "awaiting_approval", state)
    checks.check("state: 2 subtasks pending", len(state.get("subtasks") or []) == 2, state)

    job.decide(root, "approve", token="tokstate")
    checks.check("state: approved", job.read_state(root).get("phase") == "approved",
                 job.read_state(root))

    try:
        job.set_control(root, "pause", token="bad")
        blocked = False
    except PermissionError:
        blocked = True
    checks.check("control: bad token blocked", blocked)

    job.set_control(root, "pause", token="tokstate")
    checks.check("control: paused", job.read_control(root)["paused"] is True)
    job.set_control(root, "opinion", token="tokstate", value="use pandas")
    checks.check("control: opinion", job.read_control(root)["opinion"] == "use pandas")
    job.set_control(root, "resume", token="tokstate")
    checks.check("control: resumed", job.read_control(root)["paused"] is False)
    checks.check("control: wait_for_resume",
                 job.wait_for_resume(root, timeout=1, poll=0.05) == "resumed")
    job.set_control(root, "stop", token="tokstate")
    checks.check("control: stop", job.wait_for_resume(root, timeout=1, poll=0.05) == "stop")


def test_drafts(checks: Checks) -> None:
    root = make_root("drafts")
    path = root / ".agents" / "draft" / "J1-S1" / "a.py"
    path.parent.mkdir(parents=True)
    path.write_text("x = 1\n", encoding="utf-8")
    items = job.list_drafts(root, "J1")
    checks.check("drafts: list", any(item["path"].endswith("a.py") for item in items), items)
    checks.check("drafts: read", job.read_draft(root, "J1-S1/a.py") == "x = 1\n")
    try:
        job.read_draft(root, "../../../etc/passwd")
        raised = False
    except (ValueError, FileNotFoundError):
        raised = True
    checks.check("drafts: chan path escape", raised)


def test_dashboard_api(checks: Checks) -> None:
    root = make_root("dashboard")
    job.submit_plan(make_plan(), root, token="dash-token", source="plan.json")
    ledger = root / ".agents" / "ledger.jsonl"
    server = dashboard.make_server(0, root=root, ledger=ledger)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(base + "/api/plan", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
        checks.check("dashboard: GET /api/plan co plan",
                     (data.get("plan") or {}).get("job_id") == "J1", data)
        checks.check("dashboard: pending", (data.get("decision") or {}).get("status") == "pending")
        checks.check("dashboard: co root + source",
                     bool(data.get("root")) and (data.get("plan") or {}).get("_source") == "plan.json",
                     data)

        with urllib.request.urlopen(base + "/api/quota", timeout=30) as response:
            quota_data = json.loads(response.read().decode("utf-8"))
        checks.check("dashboard: quota endpoint",
                     "shared" in quota_data and "models" in quota_data, quota_data)

        with urllib.request.urlopen(base + "/", timeout=5) as response:
            html = response.read().decode("utf-8")
        checks.check("dashboard: HTML co planPanel", 'id="planPanel"' in html)

        with urllib.request.urlopen(base + "/api/job", timeout=5) as response:
            job_data = json.loads(response.read().decode("utf-8"))
        checks.check("dashboard: /api/job state",
                     (job_data.get("state") or {}).get("phase") == "awaiting_approval",
                     job_data.get("state"))
        checks.check("dashboard: /api/job control",
                     "paused" in (job_data.get("control") or {}), job_data.get("control"))

        draft = root / ".agents" / "draft" / "J1-S1" / "a.py"
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("draft\n", encoding="utf-8")
        with urllib.request.urlopen(base + "/api/draft?job_id=J1", timeout=5) as response:
            drafts = json.loads(response.read().decode("utf-8"))
        checks.check("dashboard: draft list",
                     any(item["path"].endswith("a.py") for item in drafts.get("drafts") or []),
                     drafts)

        def post(payload, path="/api/plan/decision"):
            request = urllib.request.Request(
                base + path, method="POST",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))

        status, _ = post({"decision": "approve", "token": "sai"})
        checks.check("dashboard: sai token -> 403", status == 403, status)
        status, body = post({"decision": "approve", "token": "dash-token"})
        checks.check("dashboard: dung token -> 200", status == 200, body)
        checks.check("dashboard: decision approved",
                     job.read_plan_state(root)["decision"]["status"] == "approved")

        status, _ = post({"action": "pause", "token": "sai"}, "/api/control")
        checks.check("dashboard: control sai token -> 403", status == 403, status)
        status, body = post({"action": "pause", "token": "dash-token"}, "/api/control")
        checks.check("dashboard: control pause -> 200", status == 200, body)
        checks.check("dashboard: paused", job.read_control(root)["paused"] is True)

        import planner
        original_generate = planner.generate
        planner.generate = lambda goal, root: make_plan()
        try:
            status, body = post({"goal": "fix a"}, "/api/plan/generate")
        finally:
            planner.generate = original_generate
        checks.check("dashboard: generate plan",
                     status == 200 and bool(body.get("token")) and body.get("job_id") == "J1",
                     body)
    finally:
        server.shutdown()
        server.server_close()


def test_auto_dashboard(checks: Checks) -> None:
    root = make_root("autodash")
    server, started = job.ensure_dashboard(root, 0)
    checks.check("auto-dashboard: khoi dong duoc", started and server is not None)
    if server is None:
        return
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
        checks.check("auto-dashboard: health ok", data.get("ok") is True, data)
    finally:
        server.shutdown()
        server.server_close()


def test_no_regression(checks: Checks, *, fast: bool = False) -> None:
    if fast:
        print("[skip] hoi quy Phase 5 (--fast)")
        return
    started = subprocess.run([sys.executable, str(AGENTS_DIR / "tests" / "test_phase5.py"),
                              "--fast"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=900)
    checks.check("khong hoi quy: Phase 5", started.returncode == 0,
                 (started.stdout or "")[-300:])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Phase 6A tests.")
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args(argv)

    checks = Checks()
    print("[1/9] validate plan ...")
    test_validate(checks)
    print("[2/9] topo order ...")
    test_topo(checks)
    print("[3/9] approval flow ...")
    test_approval(checks)
    print("[4/9] state + control ...")
    test_state_control(checks)
    print("[5/9] drafts ...")
    test_drafts(checks)
    print("[6/9] run job ...")
    test_run_job(checks)
    print("[7/9] dashboard API ...")
    test_dashboard_api(checks)
    print("[8/9] auto-open dashboard ...")
    test_auto_dashboard(checks)
    print("[9/9] khong hoi quy ...")
    test_no_regression(checks, fast=args.fast)

    print("")
    print(f"PHASE 6A: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    sys.exit(main())
