#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0-06: direct recipe calls must not bypass the Usage gate."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

import approval_policy  # noqa: E402
import recipe_c  # noqa: E402
import usage_request  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "usage_gate_tests"


def make_order(root: Path) -> Path:
    order = {
        "task_id": "T1",
        "title": "test",
        "objective": "change one file",
        "boundaries": "only a.py",
        "input_context": "a.py",
        "acceptance_criteria": "verify exits 0",
        "verify_command": f'"{sys.executable}" check.py',
        "target_files": ["a.py"],
        "sandbox_files": ["a.py", "check.py"],
        "level": 1,
        "max_rounds": 1,
    }
    path = root / "order.json"
    path.write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "check.py").write_text("assert True\n", encoding="utf-8")
    return path


def test_direct_request(root: Path, order_path: Path) -> bool:
    data = json.loads(order_path.read_text(encoding="utf-8"))
    order = recipe_c.work_order_module.WorkOrder.from_dict(data)
    plan = recipe_c._usage_plan(order, data)
    decision = approval_policy.evaluate(
        plan, mode="panel", reviewer="wave", models=[], root=root,
    )
    request = usage_request.write_request(
        root, plan=plan, mode="panel", reviewer="wave", models=[],
        decision=decision, label="test direct",
    )
    first = usage_request.status(root)
    wrong = False
    try:
        usage_request.approve(root, "wrong")
    except PermissionError:
        wrong = True
    approved = usage_request.approve(root, request["token"])
    final = usage_request.status(root)
    return (
        first.get("status", {}).get("status") == "awaiting"
        and wrong
        and approved.get("status") == "approved"
        and final.get("status", {}).get("status") == "approved"
    )


def main() -> int:
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    TEMP_ROOT.mkdir(parents=True)
    order = make_order(TEMP_ROOT)
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        code = recipe_c.main([
            "--order", str(order),
            "--root", str(TEMP_ROOT),
            "--reviewer", "wave",
        ])
    text = stderr.getvalue()
    gate_ok = code == 3 and "Usage approval required" in text
    request_ok = test_direct_request(TEMP_ROOT, order)
    ok = gate_ok and request_ok
    print(f"USAGE GATE: {'2/2 PASS' if ok else '0/2 PASS'}")
    if not ok:
        print(f"  gate={gate_ok} request={request_ok} code={code} stderr={text[:500]}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
