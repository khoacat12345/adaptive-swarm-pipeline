#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 5 - Level 3 (multi-file) + giao dich nguyen tu da file.

    python .agents/tests/test_level3.py
    python .agents/tests/test_level3.py --fast    # bo qua suite hoi quy

DoD:
  1. Sua da file thanh cong: lib.py + app.py doi ten ham dong bo, verify exit 0.
  2. Rollback toan dien: 1 file lam verify fail -> CA HAI file ve byte goc.
  3. Khong hoi quy: Phase 0 / 0.5 / 1 / 3 / 4 van PASS.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import fallback                         # noqa: E402
import ledger                           # noqa: E402
import recipe_c                         # noqa: E402
import safe_writer                      # noqa: E402
import work_order as work_order_module  # noqa: E402
import workers                          # noqa: E402

PY = f'"{sys.executable}"'
TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "level3_tests"

BUG_LIB = (
    "def add(a, b):\n"
    "    return a - b\n"
)
FIX_LIB = (
    "def sum_values(a, b):\n"
    "    return a + b\n"
)
BUG_APP = (
    "from lib import add\n"
    "\n"
    "\n"
    "def total(values):\n"
    "    result = 0\n"
    "    for value in values:\n"
    "        result = add(result, value)\n"
    "    return result\n"
)
FIX_APP = (
    "from lib import sum_values\n"
    "\n"
    "\n"
    "def total(values):\n"
    "    result = 0\n"
    "    for value in values:\n"
    "        result = sum_values(result, value)\n"
    "    return result\n"
)
CHECK_OK = (
    "import app\n"
    "assert app.sum_values(2, 3) == 5\n"
    "assert app.total([2, 3, 4]) == 9\n"
    "print('OK')\n"
)
VERIFY_MARKER = (
    "from pathlib import Path\n"
    "import sys\n"
    "if Path('.project_marker').exists():\n"
    "    print('FAIL: marker co mat (chi co o du an that)')\n"
    "    sys.exit(1)\n"
    "print('OK')\n"
)


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


# --------------------------------------------------------------------------
def make_project(name: str) -> Path:
    root = TEMP_ROOT / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    (root / "lib.py").write_text(BUG_LIB, encoding="utf-8")
    (root / "app.py").write_text(BUG_APP, encoding="utf-8")
    (root / "check.py").write_text(CHECK_OK, encoding="utf-8")
    return root


def make_order(task_id="L3", **overrides) -> work_order_module.WorkOrder:
    data = {
        "task_id": task_id,
        "title": "level 3",
        "objective": ("Doi ten ham add thanh sum_values trong lib.py va cap nhat "
                      "toan bo noi goi trong app.py cho dong bo"),
        "boundaries": "Chi sua lib.py va app.py, khong sua check.py",
        "input_context": "lib.py dinh nghia add; app.py import va goi add",
        "acceptance_criteria": "check.py exit 0: app.sum_values(2,3)==5 va app.total([2,3,4])==9",
        "verify_command": f"{PY} check.py",
        "target_files": ["lib.py", "app.py"],
        "sandbox_files": ["lib.py", "app.py", "check.py"],
        "level": 3,
        "max_rounds": 1,
    }
    data.update(overrides)
    return work_order_module.WorkOrder(**data)


def run_check(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "check.py"], cwd=str(root),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)


def ledger_text(root: Path) -> str:
    path = root / ".agents" / "ledger.jsonl"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --------------------------------------------------------------------------
# 1. Multi-file apply (happy path)
# --------------------------------------------------------------------------
def test_multi_apply(checks: Checks) -> None:
    root = make_project("apply")
    original = {name: safe_writer.sha256_file(root / name) for name in ("lib.py", "app.py")}

    script = {"L3": [{
        "lib.py": {"format": "whole", "content": FIX_LIB},
        "app.py": {"format": "whole", "content": FIX_APP},
    }]}
    coder = workers.ScriptedCoder(script, name="scripted-multi")
    reviewer = workers.HeuristicReviewer()
    report = recipe_c.run(make_order(), coder=coder, reviewer=reviewer, root=root,
                          run_id="l3-apply-1", use_memory=False)

    checks.check("apply: status applied", report["status"] == "applied",
                 f"{report['status']} err={report['error']}")
    checks.check("apply: 1 vong, 1 request coder + 1 reviewer",
                 report["rounds"] == 1 and report["coder_calls"] == 1
                 and report["requests"] == 2,
                 f"coder={report['coder_calls']} req={report['requests']}")
    checks.check("apply: lib.py da doi", (root / "lib.py").read_text(encoding="utf-8") == FIX_LIB)
    checks.check("apply: app.py da doi", (root / "app.py").read_text(encoding="utf-8") == FIX_APP)
    checks.check("apply: check.py (sandbox) khong bi sua",
                 "app.sum_values" in (root / "check.py").read_text(encoding="utf-8"))
    checks.check("apply: verify that exit 0", run_check(root).returncode == 0)
    checks.check("apply: ca 2 file bao changed",
                 all(item["changed"] for item in report["files"]) and len(report["files"]) == 2,
                 report["files"])
    checks.check("apply: sha cuoi khac sha goc",
                 all(item["final_sha256"] != original[item["path"]] for item in report["files"]))
    checks.check("apply: transaction committed",
                 (report["transaction"] or {}).get("state") == "committed",
                 report["transaction"])
    checks.check("apply: draft du 2 file",
                 len(list((root / ".agents" / "draft" / "L3").glob("*.py"))) == 2,
                 list((root / ".agents" / "draft" / "L3").glob("*")))

    text = ledger_text(root)
    checks.check("apply: ledger co transaction.begin + commit",
                 "transaction.begin" in text and "transaction.commit" in text)
    checks.check("apply: ledger khong rollback",
                 "transaction.rollback" not in text)

    events = [schema_event for schema_event in
              map(json.loads, text.splitlines()) if schema_event.get("run_id") == "l3-apply-1"]
    checks.check("apply: recipe.done ghi files (2)",
                 any(event["event_type"] == "recipe.done"
                     and len(event.get("data", {}).get("files") or []) == 2
                     for event in events), events[-1:])


# --------------------------------------------------------------------------
# 2. Rollback: verify fail luc apply -> ca 2 file ve byte goc
# --------------------------------------------------------------------------
def test_multi_rollback(checks: Checks) -> None:
    root = make_project("rollback")
    (root / "verify.py").write_text(VERIFY_MARKER, encoding="utf-8")
    (root / ".project_marker").write_text("marker", encoding="utf-8")
    originals = {name: (root / name).read_bytes() for name in ("lib.py", "app.py")}

    script = {"L3R": [{
        "lib.py": {"format": "whole", "content": FIX_LIB},
        "app.py": {"format": "whole", "content": FIX_APP},
    }]}
    order = make_order(task_id="L3R", verify_command=f"{PY} verify.py",
                       sandbox_files=["lib.py", "app.py", "verify.py"])
    report = recipe_c.run(order, coder=workers.ScriptedCoder(script),
                          reviewer=workers.HeuristicReviewer(), root=root,
                          run_id="l3-rollback-1", use_memory=False)

    checks.check("rollback: khong applied", report["applied"] is False, report["status"])
    checks.check("rollback: status aborted", report["status"] == "aborted", report["status"])
    checks.check("rollback: bao da rollback", report["rolled_back"] is True,
                 report["transaction"])
    checks.check("rollback: transaction state rolled_back",
                 (report["transaction"] or {}).get("state") == "rolled_back",
                 report["transaction"])
    checks.check("rollback: lib.py ve dung byte goc",
                 (root / "lib.py").read_bytes() == originals["lib.py"])
    checks.check("rollback: app.py ve dung byte goc",
                 (root / "app.py").read_bytes() == originals["app.py"])
    checks.check("rollback: khong file nao bi sua do dang",
                 all(item["changed"] is False for item in report["files"]), report["files"])
    checks.check("rollback: reporter ghi ro loi verify",
                 "verify" in (report["error"] or "").lower(), report["error"])

    text = ledger_text(root)
    checks.check("rollback: ledger co transaction.rollback",
                 "transaction.rollback" in text)
    checks.check("rollback: ledger khong co transaction.commit",
                 "transaction.commit" not in text)
    run_events = [e for e in map(json.loads, text.splitlines())
                  if e.get("run_id") == "l3-rollback-1"]
    checks.check("rollback: recipe.apply fail duoc ghi",
                 any(e["event_type"] == "recipe.apply"
                     and e.get("result", {}).get("status") == "fail" for e in run_events),
                 [e["event_type"] for e in run_events])


# --------------------------------------------------------------------------
# 3. Transaction API: rollback / commit / crash recovery
# --------------------------------------------------------------------------
def test_transaction_api(checks: Checks) -> None:
    root = make_project("transaction")
    targets = [("lib.py", root / "lib.py"), ("app.py", root / "app.py")]
    originals = {name: (root / name).read_bytes() for name, _ in targets}

    tx = safe_writer.Transaction(root=root, run_id="l3-tx-1", log=False)
    tx.begin(targets)
    tx.write("lib.py", FIX_LIB)
    tx.write("app.py", FIX_APP)
    checks.check("transaction: ghi ca 2 file", (root / "lib.py").read_text(encoding="utf-8") == FIX_LIB)
    checks.check("transaction: journal open", tx.journal.exists())
    result = tx.rollback("test rollback")
    checks.check("transaction: rollback ok", result["ok"] is True, result)
    checks.check("transaction: rollback byte-exact",
                 all((root / name).read_bytes() == data for name, data in originals.items()))
    checks.check("transaction: journal da don", not tx.journal.exists())

    tx2 = safe_writer.Transaction(root=root, run_id="l3-tx-2", log=False)
    tx2.begin(targets)
    tx2.write("lib.py", FIX_LIB)
    tx2.commit()
    checks.check("transaction: commit giu code moi",
                 (root / "lib.py").read_text(encoding="utf-8") == FIX_LIB)
    checks.check("transaction: commit don journal", not tx2.journal.exists())

    # Gia lap process bi kill giua giao dich: journal con open -> lan sau tu rollback.
    (root / "lib.py").write_bytes(originals["lib.py"])
    (root / "app.py").write_bytes(originals["app.py"])
    tx3 = safe_writer.Transaction(root=root, run_id="l3-tx-3", log=False)
    tx3.begin(targets)
    tx3.write("lib.py", FIX_LIB)
    tx3.write("app.py", FIX_APP)
    recovered = safe_writer.recover_open_transactions(root, log=False)
    checks.check("recovery: tim thay 1 giao dich mo", len(recovered) == 1, recovered)
    checks.check("recovery: ca 2 file ve byte goc",
                 all((root / name).read_bytes() == data for name, data in originals.items()))
    journal = json.loads(tx3.journal.read_text(encoding="utf-8"))
    checks.check("recovery: journal danh dau recovered", journal["state"] == "recovered")


# --------------------------------------------------------------------------
# 4. AgenticCoder.patch_set: 1 lan goi model sua ca 2 file
# --------------------------------------------------------------------------
def test_agentic_patch_set(checks: Checks) -> None:
    root = make_project("agentic")
    sandbox = Path(tempfile.mkdtemp(prefix="l3_agentic_",
                                    dir=str(Path(tempfile.gettempdir()) / "opencode")))
    coder = workers.AgenticCoder("stub/model")
    coder.bind_workspace(root, sandbox)

    def fake_runner(prompt: str):
        cwd = Path(coder._active_cwd)
        (cwd / "lib.py").write_text(FIX_LIB, encoding="utf-8")
        (cwd / "app.py").write_text(FIX_APP, encoding="utf-8")
        return "da sua 2 file", {}

    coder.runner = fake_runner
    patches = coder.patch_set(make_order(), current_files={"lib.py": BUG_LIB, "app.py": BUG_APP},
                              feedback=None, round_index=1)
    checks.check("agentic: 1 request cho 2 file", coder.calls == 1, coder.calls)
    checks.check("agentic: tra ve 2 patch whole",
                 set(patches) == {"lib.py", "app.py"}
                 and all(p["format"] == "whole" for p in patches.values()))
    checks.check("agentic: noi dung dong bo",
                 "sum_values" in patches["lib.py"]["content"]
                 and "sum_values" in patches["app.py"]["content"])

    def noop_runner(prompt: str):
        return "khong doi gi", {}
    sandbox2 = Path(tempfile.mkdtemp(prefix="l3_agentic_noop_",
                                     dir=str(Path(tempfile.gettempdir()) / "opencode")))
    coder_noop = workers.AgenticCoder("stub/model")
    coder_noop.bind_workspace(root, sandbox2)
    coder_noop.runner = noop_runner
    try:
        coder_noop.patch_set(make_order(), current_files={"lib.py": BUG_LIB, "app.py": BUG_APP},
                             feedback=None, round_index=1)
        raised = ""
    except Exception as exc:                            # noqa: BLE001
        raised = getattr(exc, "kind", "")
    checks.check("agentic: khong doi gi -> noop", raised == "noop", raised)
    checks.check("agentic: dry-run khong ghi vao du an",
                 (root / "lib.py").read_text(encoding="utf-8") == BUG_LIB)
    shutil.rmtree(sandbox, ignore_errors=True)
    shutil.rmtree(sandbox2, ignore_errors=True)


# --------------------------------------------------------------------------
# 5. FallbackCoder: patch_set di qua chain, dem dung so request
# --------------------------------------------------------------------------
def test_fallback_patch_set(checks: Checks) -> None:
    root = make_project("fallback")
    order = make_order()
    script = {"L3": [{"lib.py": {"format": "whole", "content": FIX_LIB},
                      "app.py": {"format": "whole", "content": FIX_APP}}]}

    def factory(model: str):
        if "bad" in model:
            class Boom(workers.Coder):
                def patch_set(self, *args, **kwargs):
                    self.calls += 1
                    raise workers.WorkerError("429", kind="rate_limit")
            return Boom()
        return workers.ScriptedCoder(script)

    coder = fallback.FallbackCoder(["stub/bad", "stub/good"], factory=factory,
                                   health=fallback.HealthRegistry(path=root / "health.json"))
    patches = coder.patch_set(order, current_files={"lib.py": BUG_LIB, "app.py": BUG_APP},
                              feedback=None, round_index=1)
    checks.check("fallback: chuyen model va van du 2 patch",
                 set(patches) == {"lib.py", "app.py"}, list(patches))
    checks.check("fallback: dem 2 request (1 fail + 1 ok)", coder.calls == 2, coder.calls)
    checks.check("fallback: 1 lan fail duoc ghi",
                 len([a for a in coder.attempts if a["status"] == "fail"]) == 1,
                 coder.attempts)


# --------------------------------------------------------------------------
# 6. Khong hoi quy
# --------------------------------------------------------------------------
def test_no_regression(checks: Checks, *, fast: bool = False) -> None:
    if fast:
        print("[skip] 5 suite hoi quy (--fast)")
        return
    suites = [
        ("Phase 0", [sys.executable, str(AGENTS_DIR / "run_phase0_acceptance.py")]),
        ("Phase 0.5", [sys.executable, str(AGENTS_DIR / "run_phase05_acceptance.py")]),
        ("Phase 1", [sys.executable, str(AGENTS_DIR / "tests" / "smoke_suite.py")]),
        ("Phase 3", [sys.executable, str(AGENTS_DIR / "tests" / "test_phase3.py"), "--fast"]),
        ("Phase 4", [sys.executable, str(AGENTS_DIR / "tests" / "test_phase4.py"), "--fast"]),
    ]
    for name, command in suites:
        started = subprocess.run(command, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=900)
        tail = (started.stdout or "")[-300:]
        checks.check(f"khong hoi quy: {name} PASS", started.returncode == 0,
                     f"exit={started.returncode} tail={tail}")


# --------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Level 3 acceptance tests.")
    parser.add_argument("--fast", action="store_true", help="bo qua 5 suite hoi quy")
    args = parser.parse_args(argv)

    checks = Checks()
    started = time.perf_counter()
    print("[1/6] multi-file apply ...")
    test_multi_apply(checks)
    print("[2/6] multi-file rollback ...")
    test_multi_rollback(checks)
    print("[3/6] transaction API + crash recovery ...")
    test_transaction_api(checks)
    print("[4/6] AgenticCoder.patch_set ...")
    test_agentic_patch_set(checks)
    print("[5/6] FallbackCoder.patch_set ...")
    test_fallback_patch_set(checks)
    print("[6/6] khong hoi quy ...")
    test_no_regression(checks, fast=args.fast)

    print("")
    print(f"LEVEL 3: {checks.passed}/{checks.total} PASS ({time.perf_counter() - started:.1f}s)")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    sys.exit(main())
