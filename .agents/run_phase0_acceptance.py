#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nghiem thu Phase 0 — chung minh lop an toan hoat dong that.

Kich ban theo dung Definition of Done:
  DoD-1  Tao file test gia lap        : scratch/test_calc.py
  DoD-2  Tao 1 ban backup an toan     : .agents/backups/*.bak + *.meta.json
  DoD-3  Co tinh ghi code sai cu phap : qua safe_writer (co verify)
  DoD-4  SystemBot bat duoc loi       : exit_code != 0
  DoD-5  Safe_writer rollback         : file tro ve dung 100% byte goc
  DoD-6  Moi hanh dong ghi ledger     : ledger.jsonl toan ven, du event type

Kiem tra bo sung:
  EXTRA-1  Guard chan lenh mang / lenh pha huy
  EXTRA-2  Timeout ngat sach process con
  EXTRA-3  Backup cho file CHUA ton tai -> rollback phai XOA file

Chay: python .agents/run_phase0_acceptance.py
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import time
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent
ROOT = AGENTS_DIR.parent
sys.path.insert(0, str(AGENTS_DIR))

import ledger  # noqa: E402
import safe_writer  # noqa: E402
import system_bot  # noqa: E402

PY = f'"{sys.executable}"'
SCRATCH_DIR = ROOT / "scratch"
TARGET = SCRATCH_DIR / "test_calc.py"
GHOST = SCRATCH_DIR / "ghost_never_existed.py"
VERIFY_CMD = f'{PY} -m py_compile "{TARGET}"'

VALID_SOURCE = '''"""Demo target cho Phase 0 acceptance test."""


def add(a, b):
    """Cong hai so."""
    return a + b


def divide(a, b):
    """Chia hai so, chan chia cho 0."""
    if b == 0:
        raise ValueError("khong chia cho 0")
    return a / b
'''

BROKEN_SOURCE = '''"""Demo target — ban CO TINH viet sai cu phap."""


def add(a, b)
    return a + b


def divide(a, b):
    if b == 0:
        raise ValueError("khong chia cho 0"
    return a / b
'''


class Checks:
    def __init__(self) -> None:
        self.items: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok, detail: str = "") -> bool:
        ok = bool(ok)
        self.items.append((name, ok, str(detail)))
        return ok

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def passed(self) -> int:
        return sum(1 for _, ok, _ in self.items if ok)

    @property
    def failed(self) -> list[tuple[str, bool, str]]:
        return [item for item in self.items if not item[1]]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_checks(run_id: str) -> Checks:
    checks = Checks()

    # ------------------------------------------------------------------
    # 0.1 — Scaffold
    # ------------------------------------------------------------------
    for name in ("draft", "backups", "tmp"):
        checks.check(f"0.1 scaffold .agents/{name}/", (AGENTS_DIR / name).is_dir())
    checks.check("0.1 scaffold .agents/.gitignore", (AGENTS_DIR / ".gitignore").is_file())
    checks.check("0.1 scaffold .agents/README.md", (AGENTS_DIR / "README.md").is_file())
    checks.check("0.1 tmp/ va backups/ duoc ignore",
                 "tmp/" in (AGENTS_DIR / ".gitignore").read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # DoD-1 — Tao file test gia lap
    # ------------------------------------------------------------------
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    baseline_report = safe_writer.write_safely(
        TARGET, VALID_SOURCE, root=ROOT, run_id=run_id, reason="acceptance:baseline",
    )
    original_bytes = TARGET.read_bytes()
    original_sha = _sha(original_bytes)
    checks.check("DoD-1 tao scratch/test_calc.py", TARGET.is_file())
    checks.check("DoD-1 ghi atomic + toan ven", baseline_report["integrity_ok"],
                 f"sha={baseline_report['sha256'][:12]}")
    checks.check("DoD-1 sha tren dia khop", _sha(TARGET.read_bytes()) == original_sha)

    # Baseline: chung minh lenh verify thuc su hoat dong (de lan sau fail la co y nghia)
    baseline_bot = system_bot.run_command(VERIFY_CMD, cwd=str(SCRATCH_DIR), root=ROOT, run_id=run_id)
    checks.check("PRE bot tren file hop le -> exit 0", baseline_bot["exit_code"] == 0,
                 f"exit={baseline_bot['exit_code']}")

    # ------------------------------------------------------------------
    # DoD-2 — Backup an toan
    # ------------------------------------------------------------------
    backup_info = safe_writer.backup_file(
        TARGET, root=ROOT, run_id=run_id, reason="acceptance:manual-backup",
    )
    backup_path = Path(backup_info["backup_path"])
    meta = safe_writer.read_backup_meta(backup_info["meta_path"])
    checks.check("DoD-2 file .bak duoc tao", backup_path.is_file(), backup_path.name)
    checks.check("DoD-2 backup khop byte goc", _sha(backup_path.read_bytes()) == original_sha)
    checks.check("DoD-2 meta ghi existed=True", meta["existed"] is True)
    checks.check("DoD-2 ten backup dung format <file>.<ts>.<hash>.bak",
                 backup_path.suffix == ".bak" and original_sha[:8] in backup_path.name)

    # ------------------------------------------------------------------
    # DoD-3/4/5 — Ghi code sai -> bot bat -> rollback
    # ------------------------------------------------------------------
    broken_report = safe_writer.write_safely(
        TARGET, BROKEN_SOURCE,
        verify_cmd=VERIFY_CMD, root=ROOT, run_id=run_id,
        reason="acceptance:broken-syntax",
    )
    checks.check("DoD-3 da ghi code sai cu phap",
                 broken_report["written"] and broken_report["sha256"] != original_sha)
    verify = broken_report.get("verify") or {}
    checks.check("DoD-4 SystemBot bat loi -> exit != 0",
                 verify and verify["exit_code"] not in (0, None),
                 f"exit={verify.get('exit_code')} result={verify.get('result')}")
    stderr_text = " ".join(verify.get("stderr_tail") or [])
    checks.check("DoD-4 log chi ro SyntaxError", "SyntaxError" in stderr_text,
                 stderr_text.strip().splitlines()[0][:90] if stderr_text.strip() else "")
    checks.check("DoD-5 safe_writer da kich hoat rollback", broken_report["rolled_back"] is True)

    final_bytes = TARGET.read_bytes()
    checks.check("DoD-5 byte goc nguyen ven 100%", final_bytes == original_bytes,
                 f"{len(final_bytes)} bytes")
    checks.check("DoD-5 sha256 khop goc", _sha(final_bytes) == original_sha)
    checks.check("DoD-5 file van ton tai sau rollback", TARGET.is_file())

    # ------------------------------------------------------------------
    # EXTRA-1 — Guard chan lenh nguy hiem / mang
    # ------------------------------------------------------------------
    net = system_bot.run_command("curl https://example.com/steal", root=ROOT, run_id=run_id)
    checks.check("EXTRA-1 chan lenh mang (exit 126)", net["exit_code"] == 126)
    checks.check("EXTRA-1 category=network", net["blocked_category"] == "network")

    danger = system_bot.run_command("rm -rf /", root=ROOT, run_id=run_id)
    checks.check("EXTRA-1 chan lenh pha huy", danger["exit_code"] == 126,
                 danger["blocked_category"] or "")

    redirect = system_bot.run_command("echo hi > scratch/hacked.txt", root=ROOT, run_id=run_id)
    checks.check("EXTRA-1 chan redirection ghi file", redirect["exit_code"] == 126,
                 redirect["blocked_category"] or "")
    checks.check("EXTRA-1 khong tao file nao", not (SCRATCH_DIR / "hacked.txt").exists())

    # ------------------------------------------------------------------
    # EXTRA-2 — Timeout ngat sach process con
    # ------------------------------------------------------------------
    slow = system_bot.run_command(
        f'{PY} -c "import time;time.sleep(15)"', root=ROOT, run_id=run_id, timeout=2,
    )
    checks.check("EXTRA-2 timeout -> exit 124", slow["exit_code"] == 124 and slow["timed_out"])
    checks.check("EXTRA-2 ngat dung han (khong cho 15s)", slow["duration_ms"] < 12000,
                 f"{slow['duration_ms']}ms")

    # ------------------------------------------------------------------
    # EXTRA-3 — Backup cho file chua ton tai -> rollback phai XOA
    # ------------------------------------------------------------------
    if GHOST.exists():
        GHOST.unlink()
    ghost_report = safe_writer.write_safely(
        GHOST, "print('tam thoi')\n", root=ROOT, run_id=run_id, reason="acceptance:ghost",
    )
    checks.check("EXTRA-3 file moi duoc tao", GHOST.is_file())
    checks.check("EXTRA-3 backup ghi existed=False", ghost_report["backup"]["existed"] is False)
    safe_writer.rollback(GHOST, backup_path=ghost_report["backup"]["backup_path"],
                         root=ROOT, run_id=run_id)
    checks.check("EXTRA-3 rollback da xoa file moi", not GHOST.exists())

    # ------------------------------------------------------------------
    # DoD-6 — Ledger toan ven + du event
    # ------------------------------------------------------------------
    report = ledger.validate_ledger(root=ROOT)
    checks.check("DoD-6 ledger 0 dong loi", report["invalid_events"] == 0,
                 f"{report['total_events']} events, {report['invalid_events']} loi")

    events = [e for e in ledger.read_events(root=ROOT) if e.get("run_id") == run_id]
    types = {e.get("event_type") for e in events}
    required = {
        "safe_writer.backup", "safe_writer.write", "safe_writer.verify",
        "safe_writer.rollback", "system_bot.run", "system_bot.blocked",
    }
    missing = required - types
    checks.check("DoD-6 du event type bat buoc", not missing, f"thieu {sorted(missing)}" if missing else "")
    checks.check("DoD-6 moi event co usage + result",
                 all(isinstance(e.get("usage"), dict) and isinstance(e.get("result"), dict)
                     for e in events))
    checks.check("DoD-6 co >= 10 event trong run nay", len(events) >= 10, f"{len(events)} events")

    return checks


def main() -> int:
    started = time.perf_counter()
    run_id = ledger.new_run_id("phase0")
    ledger.append_event(
        "phase0.acceptance.start", run_id=run_id,
        result={"status": "info", "error": None},
        data={"root": str(ROOT), "python": sys.version.split()[0]},
        root=ROOT,
    )

    print("=" * 74)
    print("  PHASE 0 ACCEPTANCE - Safety & Verification Layer")
    print(f"  run_id : {run_id}")
    print(f"  root   : {ROOT}")
    print("=" * 74)

    checks = Checks()
    error = None
    try:
        checks = run_checks(run_id)
    except Exception as exc:  # noqa: BLE001 — nghiem thu phai bao loi ro rang
        error = f"{type(exc).__name__}: {exc}"
        checks.check("Kich ban chay khong loi", False, error)

    for name, ok, detail in checks.items:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail else ""))

    duration_ms = int((time.perf_counter() - started) * 1000)
    ok_all = checks.passed == checks.total and error is None
    status = "pass" if ok_all else "fail"

    ledger.append_event(
        "phase0.acceptance.end", run_id=run_id, duration_ms=duration_ms,
        result={"status": status, "error": error},
        data={
            "checks_total": checks.total,
            "checks_passed": checks.passed,
            "failed": [name for name, ok, _ in checks.items if not ok],
        },
        root=ROOT,
    )

    stats = ledger.stats(root=ROOT)
    print("-" * 74)
    print(f"  KET QUA : {checks.passed}/{checks.total} PASS"
          + (f"  ({len(checks.failed)} FAIL)" if checks.failed else "")
          + f"   trong {duration_ms} ms")
    print(f"  LEDGER  : {ledger.resolve_ledger_path(root=ROOT)}")
    print(f"            {stats['total_events']} events | "
          f"types={len(stats['by_type'])} | status={stats['by_status']}")
    print("=" * 74)

    # Don rac do py_compile sinh ra
    pycache = SCRATCH_DIR / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache, ignore_errors=True)

    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
