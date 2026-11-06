#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0-05 / P1-11: crash recovery and repository run lock."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import run_lock  # noqa: E402
import safe_writer  # noqa: E402

ROOT = Path(tempfile.gettempdir()) / "opencode" / "resilience_tests"


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


def make_root() -> Path:
    shutil.rmtree(ROOT, ignore_errors=True)
    ROOT.mkdir(parents=True)
    (ROOT / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    return ROOT


def test_lock(checks: Checks, root: Path) -> None:
    first = run_lock.RepoLock(root, owner="first").acquire()
    busy = False
    try:
        run_lock.RepoLock(root, owner="second").acquire()
    except run_lock.LockBusy:
        busy = True
    checks.check("lock: second process blocked", busy)
    first.release()
    second = run_lock.RepoLock(root, owner="second").acquire()
    checks.check("lock: reacquire works", second.acquired is True)
    second.release()

    stale = run_lock.lock_path(root)
    stale.write_text(json.dumps({
        "owner": "dead", "pid": 999999, "created_at": 1,
    }), encoding="utf-8")
    reclaimed = run_lock.RepoLock(root, owner="new").acquire()
    checks.check("lock: stale lock reclaimed", reclaimed.acquired is True)
    reclaimed.release()


def test_crash_recovery(checks: Checks, root: Path) -> None:
    child = r'''
import os, sys
from pathlib import Path
sys.path.insert(0, r"%s")
import safe_writer
root = Path(r"%s")
tx = safe_writer.Transaction(root=root, log=False, reason="crash test")
tx.begin([("a.py", root / "a.py")])
tx.write("a.py", "VALUE = 999\n")
os._exit(9)
''' % (str(AGENTS_DIR), str(root))
    script = root / "crash_child.py"
    script.write_text(child, encoding="utf-8")
    result = subprocess.run([sys.executable, str(script)], timeout=60)
    checks.check("crash: child exited abnormally", result.returncode != 0,
                 result.returncode)
    checks.check("crash: file was modified before crash",
                 (root / "a.py").read_text(encoding="utf-8") == "VALUE = 999\n")
    recovered = safe_writer.recover_open_transactions(root, log=False)
    checks.check("crash: journal recovered", bool(recovered), recovered)
    checks.check("crash: original bytes restored",
                 (root / "a.py").read_text(encoding="utf-8") == "VALUE = 1\n",
                 (root / "a.py").read_text(encoding="utf-8"))
    journals = list((root / ".agents" / "tmp" / "tx").glob("*.json"))
    states = []
    for journal in journals:
        try:
            states.append(json.loads(journal.read_text(encoding="utf-8")).get("state"))
        except (OSError, json.JSONDecodeError):
            pass
    checks.check("crash: no open journal remains", "open" not in states, states)


def main() -> int:
    root = make_root()
    checks = Checks()
    test_lock(checks, root)
    test_crash_recovery(checks, root)
    print(f"RESILIENCE: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
