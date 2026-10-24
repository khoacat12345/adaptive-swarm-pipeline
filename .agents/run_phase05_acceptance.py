#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nghiem thu Phase 0.5 — Scout.

DoD-1  `scout --write` sinh ra .agents/config.json
DoD-2  config co du 5 vai tro, moi vai tro >= 2 model (primary + du phong)
DoD-3  Sua tay config -> chay lai KHONG co --write -> khong bi ghi de

Kiem tra bo sung:
  EX-1  Preview khong tu tao file khi chua co config
  EX-2  --write ghi de thi phai co backup ban cu (safe_writer)
  EX-3  Canh bao config cu > 30 ngay
  EX-4  --verify mac dinh TAT (khong sinh request nao)
  EX-5  --verify bat len thi co micro-ping va ghi ledger (dung template gia de offline)
  EX-6  --no-cli -> dung snapshot du phong + co canh bao
  EX-7  --cost-policy cost-first -> coder phai la model free
  EX-8  config va ledger deu qua validate

Chay: python .agents/run_phase05_acceptance.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent
ROOT = AGENTS_DIR.parent
sys.path.insert(0, str(AGENTS_DIR))

import ledger  # noqa: E402
import scout  # noqa: E402
import safe_writer  # noqa: E402

SCOUT = AGENTS_DIR / "scout.py"
CONFIG = AGENTS_DIR / "config.json"
ROLES = ("coder", "reviewer", "security", "architect", "test_writer")
FAKE_PING = "python -c \"print('OK')\""


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


def run_scout(args: list[str], timeout: int = 240) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    return subprocess.run(
        [sys.executable, str(SCOUT)] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, cwd=str(ROOT), env=env,
    )


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def write_config_raw(config: dict) -> None:
    CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_checks() -> Checks:
    checks = Checks()

    # Bat buoc: kich ban nay se sua config.json, nen phai luu lai ban goc de tra ve
    # nguyen trang khi ket thuc (test khong duoc phep pha trang thai cua nguoi dung).
    original_config = CONFIG.read_bytes() if CONFIG.exists() else None

    # Don trang thai de kiem tra duong "tao moi"
    if CONFIG.exists():
        CONFIG.unlink()

    # ------------------------------------------------------------------
    # EX-1 — preview khong tu tao file
    # ------------------------------------------------------------------
    preview = run_scout([])
    checks.check("EX-1 preview exit 0", preview.returncode == 0, f"exit={preview.returncode}")
    checks.check("EX-1 preview KHONG tao config.json", not CONFIG.exists())
    checks.check("EX-1 preview in huong dan --write", "--write" in preview.stdout)

    # ------------------------------------------------------------------
    # DoD-1 — ghi that
    # ------------------------------------------------------------------
    written = run_scout(["--write"])
    checks.check("DoD-1 scout --write exit 0", written.returncode == 0, f"exit={written.returncode}")
    checks.check("DoD-1 config.json duoc tao", CONFIG.is_file())
    if not CONFIG.is_file():
        return checks

    config = load_config()

    # ------------------------------------------------------------------
    # DoD-2 — du vai tro, moi vai tro >= 2 model
    # ------------------------------------------------------------------
    roles = config.get("roles") or {}
    checks.check("DoD-2 du 5 vai tro", set(roles) == set(ROLES), f"co {sorted(roles)}")
    for role in ROLES:
        chain = (roles.get(role) or {}).get("chain") or []
        checks.check(f"DoD-2 {role}: >= 2 model", len(chain) >= 2, f"{len(chain)} model")
        checks.check(f"DoD-2 {role}: primary == chain[0]",
                     (roles.get(role) or {}).get("primary") == (chain[0] if chain else None))

    checks.check("DoD-2 co metadata scouted_at", bool(config.get("scouted_at")), config.get("scouted_at", ""))
    checks.check("DoD-2 co version + schema_version",
                 bool(config.get("version")) and config.get("schema_version") == scout.SCHEMA_VERSION)
    checks.check("DoD-2 co catalog source", bool(config["catalog"]["source"]), config["catalog"]["source"])
    checks.check("DoD-2 registry model >= 5", len(config.get("models") or {}) >= 5,
                 f"{len(config.get('models') or {})} model")
    checks.check("DoD-2 default is free-only",
                 (config.get("constraints") or {}).get("free_only") is True
                 and all(m.get("is_free") for m in (config.get("models") or {}).values()),
                 str((config.get("constraints") or {}).get("free_only")))
    checks.check("DoD-2 moi model co field bat buoc",
                 all(all(k in m for k in ("provider", "auth_status", "context_window",
                                          "pricing", "is_free", "supports_cache"))
                     for m in (config.get("models") or {}).values()))
    checks.check("DoD-2 khong co model cua provider unconfigured",
                 all(m["auth_status"] != "unconfigured" for m in (config.get("models") or {}).values()))
    checks.check("DoD-2 validate_config sach", not scout.validate_config(config),
                 "; ".join(scout.validate_config(config)))

    # 5 primary phai trai deu ho (bai toan gan co rang buoc)
    families = [(roles.get(r) or {}).get("primary_family") for r in ROLES]
    checks.check("DoD-2 5 primary thuoc 5 ho khac nhau", len(set(families)) == len(ROLES),
                 str(families))
    # reviewer phai khac ho coder
    checks.check("DoD-2 reviewer khac ho voi coder",
                 (roles["reviewer"]["primary_family"] != roles["coder"]["primary_family"]),
                 f"coder={roles['coder']['primary_family']} reviewer={roles['reviewer']['primary_family']}")

    # ------------------------------------------------------------------
    # DoD-3 — sua tay roi chay lai khong --write -> khong bi ghi de
    # ------------------------------------------------------------------
    hand = load_config()
    hand["hand_edit_marker"] = "HAND_EDITED_BY_USER"
    hand["roles"]["coder"]["primary"] = "HAND_EDITED_PRIMARY"
    write_config_raw(hand)
    before = CONFIG.read_bytes()

    preview2 = run_scout([])
    checks.check("DoD-3 chay lai khong --write exit 0", preview2.returncode == 0)
    checks.check("DoD-3 file config KHONG bi ghi de", CONFIG.read_bytes() == before)
    checks.check("DoD-3 marker sua tay con nguyen",
                 "HAND_EDITED_BY_USER" in CONFIG.read_text(encoding="utf-8"))
    checks.check("DoD-3 preview co hien diff theo vai tro", "DIFF THEO VAI TRO" in preview2.stdout)

    # ------------------------------------------------------------------
    # EX-2 — ghi de thi phai backup ban cu
    # ------------------------------------------------------------------
    rewritten = run_scout(["--write"])
    checks.check("EX-2 --write lan 2 exit 0", rewritten.returncode == 0)
    after = load_config()
    checks.check("EX-2 config da tro ve gia tri that", "hand_edit_marker" not in after)
    backups = safe_writer.list_backups(CONFIG)
    checks.check("EX-2 co backup cua config cu", len(backups) >= 1, f"{len(backups)} ban")
    if backups:
        newest = Path(backups[0]["backup_path"])
        checks.check("EX-2 backup chua dung ban sua tay",
                     "HAND_EDITED_BY_USER" in newest.read_text(encoding="utf-8"), newest.name)
        checks.check("EX-2 backup ghi existed=True", backups[0]["existed"] is True)

    # ------------------------------------------------------------------
    # EX-3 — canh bao config cu > 30 ngay
    # ------------------------------------------------------------------
    stale = load_config()
    stale["scouted_at"] = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat().replace("+00:00", "Z")
    write_config_raw(stale)
    stale_run = run_scout([])
    checks.check("EX-3 canh bao config qua cu", "CANH BAO: config cu da" in stale_run.stdout)
    checks.check("EX-3 co noi ro 30 ngay", "> 30 ngay" in stale_run.stdout)

    # khoi phuc config dung
    run_scout(["--write"])

    # ------------------------------------------------------------------
    # EX-4 — --verify mac dinh TAT (do trong dung khoang thoi gian cua lan chay nay)
    # ------------------------------------------------------------------
    before_count = len(ledger.read_events(root=ROOT))
    plain_run = run_scout([])
    checks.check("EX-4 chay khong --verify van exit 0", plain_run.returncode == 0)
    new_events = ledger.read_events(root=ROOT)[before_count:]
    checks.check("EX-4 khong sinh request verify nao",
                 not any(e.get("event_type") == "scout.verify" for e in new_events),
                 f"{len(new_events)} event moi")
    checks.check("EX-4 preview khong bao se gui request",
                 "se gui" not in plain_run.stdout)
    plain_written = run_scout(["--write"])
    checks.check("EX-4 config ghi ra co verify.enabled = false",
                 (load_config().get("verify") or {}).get("enabled") is False)

    # ------------------------------------------------------------------
    # EX-5 — --verify bat len: micro-ping that su duoc goi + ghi ledger
    # ------------------------------------------------------------------
    verify_run = run_scout(["--write", "--verify", "--verify-roles", "coder",
                            "--verify-template", FAKE_PING, "--verify-timeout", "30"])
    checks.check("EX-5 --verify exit 0", verify_run.returncode == 0,
                 verify_run.stderr.strip()[-160:] if verify_run.returncode else "")
    verified = load_config()
    result = ((verified.get("verify") or {}).get("results") or {}).get("coder") or {}
    checks.check("EX-5 verify chay 1 request cho coder", result.get("role") == "coder")
    checks.check("EX-5 verify bao OK", result.get("ok") is True, str(result.get("error")))
    checks.check("EX-5 verify ghi vao config", (verified.get("verify") or {}).get("enabled") is True)
    events = ledger.read_events(root=ROOT)
    checks.check("EX-5 co scout.verify event trong ledger",
                 any(e.get("event_type") == "scout.verify" for e in events))
    checks.check("EX-5 toi da 5 request/vai tro (chi 1 vai tro duoc yeu cau)",
                 len((verified.get("verify") or {}).get("results") or {}) == 1)

    # ------------------------------------------------------------------
    # EX-6 — --no-cli -> snapshot du phong
    # ------------------------------------------------------------------
    offline = run_scout(["--no-cli"])
    checks.check("EX-6 --no-cli exit 0", offline.returncode == 0)
    checks.check("EX-6 bao dung snapshot du phong", "builtin-snapshot" in offline.stdout
                 or "snapshot" in offline.stdout.lower())

    # ------------------------------------------------------------------
    # EX-7 — cost-first -> coder la model free
    # ------------------------------------------------------------------
    cost_first = run_scout(["--json", "--cost-policy", "cost-first"])
    ok_json = cost_first.returncode == 0
    parsed = None
    if ok_json:
        try:
            parsed, _ = json.JSONDecoder().raw_decode(cost_first.stdout[cost_first.stdout.index("{"):])
        except (ValueError, json.JSONDecodeError):
            ok_json = False
            parsed = None
    checks.check("EX-7 --cost-policy cost-first chay duoc", ok_json,
                 "" if ok_json else "khong parse duoc JSON")
    if parsed:
        coder_id = parsed["roles"]["coder"]["primary"]
        coder_model = parsed["models"][coder_id]
        checks.check("EX-7 cost-first: coder la model free", coder_model["is_free"] is True,
                     f"{coder_id} (cost={coder_model['cost_tier']})")
        checks.check("EX-7 ghi ro cost_policy trong config",
                     parsed.get("cost_policy") == "cost-first")

    # ------------------------------------------------------------------
    # EX-9 — dang lenh `python -m` (DoD viet `python -m .agents.scout`)
    # ------------------------------------------------------------------
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"

    module_run = subprocess.run(
        [sys.executable, "-m", "agents.scout"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240, cwd=str(ROOT), env=env,
    )
    checks.check("EX-9 `python -m agents.scout` chay duoc", module_run.returncode == 0,
                 f"exit={module_run.returncode}")
    checks.check("EX-9 van la che do preview (khong tu ghi)",
                 "SCOUT PREVIEW" in module_run.stdout)

    literal = subprocess.run(
        [sys.executable, "-m", ".agents.scout"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120, cwd=str(ROOT), env=env,
    )
    literal_out = (literal.stdout or "") + (literal.stderr or "")
    checks.check("EX-9 `python -m .agents.scout` bi Python tu choi (da ghi tai lieu)",
                 literal.returncode != 0 and "Relative module names not supported" in literal_out,
                 f"exit={literal.returncode}")

    # ------------------------------------------------------------------
    # EX-10 — config.json khong duoc ro ri secret
    # ------------------------------------------------------------------
    raw_text = CONFIG.read_text(encoding="utf-8")
    leak = re.search(r"sk-[A-Za-z0-9_\-]{8,}", raw_text)
    checks.check("EX-10 config khong chua api key", leak is None and "apiKey" not in raw_text,
                 leak.group(0)[:12] if leak else "")

    # ------------------------------------------------------------------
    # EX-11 — --free-only: rang buoc cung, chi dung model mien phi
    # ------------------------------------------------------------------
    free_run = run_scout(["--free-only", "--json"])
    ok_free = free_run.returncode == 0
    free_cfg = None
    if ok_free:
        try:
            free_cfg, _ = json.JSONDecoder().raw_decode(
                free_run.stdout[free_run.stdout.index("{"):])
        except (ValueError, json.JSONDecodeError):
            ok_free = False
    checks.check("EX-11 `--free-only --json` chay duoc", ok_free,
                 f"exit={free_run.returncode}")
    if free_cfg:
        registry = free_cfg["models"]
        checks.check("EX-11 moi model trong config deu mien phi",
                     all(m["is_free"] for m in registry.values()), f"{len(registry)} model")
        checks.check("EX-11 moi vai tro van du >= 2 model",
                     all(len(v["chain"]) >= 2 for v in free_cfg["roles"].values()),
                     "; ".join(f"{r}={len(v['chain'])}" for r, v in free_cfg["roles"].items()))
        checks.check("EX-11 ghi ro rang buoc free_only",
                     (free_cfg.get("constraints") or {}).get("free_only") is True)
        free_families = [v.get("primary_family") for v in free_cfg["roles"].values()]
        checks.check("EX-11 5 primary van thuoc 5 ho khac nhau",
                     len(set(free_families)) == len(ROLES), str(free_families))
        checks.check("EX-11 coder uu tien ho deepseek khi co model free",
                     free_cfg["roles"]["coder"]["primary_family"] == "deepseek",
                     free_cfg["roles"]["coder"]["primary"])

    # ------------------------------------------------------------------
    # EX-13 — moi chuoi vai tro phai co san 2 cua thoat: quota + outage
    # ------------------------------------------------------------------
    registry = config["models"]
    catalog_providers = {m["provider"] for m in registry.values()}
    chains = {role: (roles[role].get("chain") or []) for role in ROLES}
    if any(m["is_free"] for m in registry.values()):
        checks.check("EX-13 moi chuoi co >= 1 model mien phi (cua thoat khi het quota)",
                     all(any(registry.get(m, {}).get("is_free") for m in chain)
                         for chain in chains.values()),
                     json.dumps(chains, ensure_ascii=False))
    if len(catalog_providers) >= 2:
        checks.check("EX-13 moi chuoi co >= 2 provider (cua thoat khi provider sap)",
                     all(len({registry.get(m, {}).get("provider") for m in chain}) >= 2
                         for chain in chains.values()),
                     json.dumps(chains, ensure_ascii=False))

    # ------------------------------------------------------------------
    # EX-8 — validate ledger + config
    # ------------------------------------------------------------------
    final = run_scout(["--write"])
    checks.check("EX-8 --write cuoi cung exit 0", final.returncode == 0)
    report = ledger.validate_ledger(root=ROOT)
    checks.check("EX-8 ledger 0 dong loi", report["invalid_events"] == 0,
                 f"{report['total_events']} events")
    final_config = load_config()
    checks.check("EX-8 config cuoi cung hop le", not scout.validate_config(final_config))
    checks.check("EX-8 moi vai tro cuoi cung >= 2 model",
                 all(len(v["chain"]) >= 2 for v in final_config["roles"].values()))

    # ------------------------------------------------------------------
    # EX-12 — tra config ve dung ban goc truoc khi test
    # ------------------------------------------------------------------
    if original_config is not None:
        CONFIG.write_bytes(original_config)
        restored = CONFIG.read_bytes() == original_config
        checks.check("EX-12 tra config ve dung ban goc cua nguoi dung", restored)
        if restored:
            kept = load_config()
            checks.check("EX-12 ban goc duoc giu nguyen noi dung",
                         kept.get("version") == json.loads(
                             original_config.decode("utf-8")).get("version"))

    return checks


def main() -> int:
    started = time.perf_counter()
    run_id = ledger.new_run_id("phase05")
    ledger.append_event(
        "phase05.acceptance.start", run_id=run_id,
        result={"status": "info", "error": None}, data={"root": str(ROOT)}, root=ROOT,
    )

    print("=" * 78)
    print("  PHASE 0.5 ACCEPTANCE - Scout")
    print(f"  run_id : {run_id}")
    print(f"  scout  : {SCOUT}")
    print("=" * 78)

    checks = Checks()
    error = None
    try:
        checks = run_checks()
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        checks.check("Kich ban chay khong loi", False, error)

    for name, ok, detail in checks.items:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail else ""))

    duration_ms = int((time.perf_counter() - started) * 1000)
    ok_all = checks.passed == checks.total and error is None
    ledger.append_event(
        "phase05.acceptance.end", run_id=run_id, duration_ms=duration_ms,
        result={"status": "pass" if ok_all else "fail", "error": error},
        data={"checks_total": checks.total, "checks_passed": checks.passed,
              "failed": [n for n, ok, _ in checks.items if not ok]},
        root=ROOT,
    )

    print("-" * 78)
    print(f"  KET QUA : {checks.passed}/{checks.total} PASS   trong {duration_ms} ms")
    print(f"  CONFIG  : {CONFIG}")
    print("=" * 78)
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
