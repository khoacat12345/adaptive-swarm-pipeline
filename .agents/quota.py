#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quota tracker: doc usage THAT tu `opencode stats` va cat theo tran.

Nguon du lieu: `opencode stats --days N --models --cost --json`
  -> models[].model.{providerID,id}, models[].cost, tokens
Day la cung so lieu voi OpenCode Go console (cost that, khong phai uoc luong
tu ledger).

Tran (theo tai lieu OpenCode Go):
  - 5 gio  = 20% han muc thang
  - 1 tuan = 50%
  - 1 thang = 100%
Free (`opencode/*`) khong tieu quota -> coi nhu vo han.

Dung:
    python .agents/quota.py --json
    # trong code:
    tracker = QuotaTracker(); tracker.refresh()
    tracker.choose(["opencode-go/glm-5.3", "opencode-go/claude-haiku-5-5"])
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_BIN = shutil.which("opencode") or r"C:\Users\tuank\AppData\Roaming\npm\opencode.CMD"

# Han muc $/thang cua tung model tren OpenCode Go (tai lieu Go, 2026-10).
CAPS = {
    "opencode-go/kimi-k3": 15.0,
    "opencode-go/qwen3.8-max": 15.0,
    "opencode-go/glm-5.3": 15.0,
    "opencode-go/deepseek-v4-pro": 15.0,
    "opencode-go/claude-haiku-5-5": 15.0,
    "opencode-go/gpt-6-luna": 15.0,
    "opencode-go/kimi-k2.7-code": 60.0,
    "opencode-go/minimax-m3": 60.0,
    "opencode-go/glm-5.3-flash": 60.0,
    "opencode-go/deepseek-v4.1-flash": 80.0,
}

# Ty le tran theo cua so (theo tai lieu: 5h = 20%, tuan = 50%, thang = 100%).
WINDOW_RATIO = {"today": 0.20, "week": 0.50, "month": 1.00}
WINDOW_DAYS = {"today": 1, "week": 7, "month": 30}

# Ngan sach CHUNG cua plan (console hien Rolling/Weekly/Monthly dung chung).
# Uoc tu console Go ($10 plan, Monthly 8% ~ $1.11 -> ~$14). Co the override.
PLAN_MONTHLY_USD = 14.0


def _bin(binary: str | None = None) -> str:
    return binary or DEFAULT_BIN


def read_stats(days: int, *, binary: str | None = None, timeout: int = 40) -> dict | None:
    """Goi `opencode stats`; tra dict hoac None neu khong doc duoc."""
    cmd = [_bin(binary), "stats", "--days", str(days), "--models", "--cost",
           "--json", "--limit", "100"]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = (done.stdout or "").lstrip("\ufeff").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def cost_by_model(stats: dict | None) -> dict[str, float]:
    """Tong cost theo model key `provider/id` (gop cac variant)."""
    result: dict[str, float] = {}
    for item in (stats or {}).get("models") or []:
        model = item.get("model") or {}
        provider = str(model.get("providerID") or "")
        model_id = str(model.get("id") or "")
        if not provider or not model_id:
            continue
        key = f"{provider}/{model_id}"
        result[key] = result.get(key, 0.0) + float(item.get("cost") or 0.0)
    return result


class QuotaTracker:
    """Doc usage that + quyet dinh model nao con du dia.

    Cache trong `.agents/tmp/quota.json` (mac dinh 60s) de khong goi CLI
    nhieu lan trong cung mot run.
    """

    def __init__(self, *, root=None, binary: str | None = None,
                 caps: dict | None = None, cache_ttl: int = 60,
                 plan_cap: float = PLAN_MONTHLY_USD) -> None:
        self.root = Path(root).resolve() if root else Path.cwd()
        self.binary = binary
        self.caps = dict(caps or CAPS)
        self.plan_cap = float(plan_cap)
        self.cache_ttl = cache_ttl
        self.usage: dict[str, dict[str, float]] = {name: {} for name in WINDOW_DAYS}
        self.available = False
        self.error: str | None = None
        self.fetched_at = 0.0

    @property
    def cache_path(self) -> Path:
        return self.root / ".agents" / "tmp" / "quota.json"

    def refresh(self, *, force: bool = False) -> "QuotaTracker":
        now = time.time()
        if not force and self.fetched_at and now - self.fetched_at < self.cache_ttl:
            return self
        cached = self._read_cache(now)
        if cached and not force:
            self.usage = cached["usage"]
            self.available = bool(cached.get("available"))
            self.fetched_at = float(cached.get("at") or now)
            return self

        ok = False
        for window, days in WINDOW_DAYS.items():
            stats = read_stats(days, binary=self.binary)
            if stats is None:
                continue
            self.usage[window] = cost_by_model(stats)
            ok = True
        self.available = ok
        self.fetched_at = now
        if not ok:
            self.error = "khong doc duoc opencode stats"
        self._write_cache()
        return self

    def _read_cache(self, now: float) -> dict | None:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if now - float(data.get("at") or 0) > self.cache_ttl:
            return None
        return data

    def _write_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(
                {"at": self.fetched_at, "available": self.available,
                 "usage": self.usage}, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError:
            pass

    def shared(self) -> dict:
        """Ngan sach CHUNG cua plan: tong cost cac model tra tien (opencode-go/)."""
        windows: dict[str, dict] = {}
        ok = True
        worst = 0.0
        for window, ratio in WINDOW_RATIO.items():
            used = sum(float(cost) for model, cost in self.usage.get(window, {}).items()
                       if str(model).startswith("opencode-go/"))
            limit = self.plan_cap * ratio
            pct = (used / limit * 100.0) if limit else 0.0
            worst = max(worst, pct)
            if used >= limit:
                ok = False
            windows[window] = {"used": round(used, 4), "limit": round(limit, 4),
                               "remaining": round(max(0.0, limit - used), 4),
                               "pct": round(pct, 1)}
        return {"ok": ok, "cap": round(self.plan_cap, 2),
                "windows": windows, "worst_pct": round(worst, 1)}

    def remaining(self, model: str) -> dict:
        cap = self.caps.get(model)
        if not cap:
            return {"model": model, "unlimited": True, "ok": True,
                    "windows": {}, "worst_pct": 0.0}
        windows: dict[str, dict] = {}
        model_ok = True
        worst = 0.0
        for window, ratio in WINDOW_RATIO.items():
            used = float(self.usage.get(window, {}).get(model, 0.0))
            limit = cap * ratio
            pct = (used / limit * 100.0) if limit else 0.0
            worst = max(worst, pct)
            if used >= limit:
                model_ok = False
            windows[window] = {"used": round(used, 4), "limit": round(limit, 4),
                               "remaining": round(max(0.0, limit - used), 4),
                               "pct": round(pct, 1)}
        shared = self.shared()
        return {"model": model, "unlimited": False,
                "ok": bool(model_ok and shared["ok"]),
                "model_ok": model_ok, "shared_ok": shared["ok"],
                "windows": windows, "worst_pct": round(worst, 1),
                "shared": shared}

    def choose(self, chain: list[str], *, refresh: bool = True) -> tuple[str | None, dict]:
        """Chon model dau tien con du dia; tra (model, info) cho TOAN BO chain."""
        if refresh:
            self.refresh()
        info: dict = {model: self.remaining(model) for model in chain}
        for model in chain:
            if info[model]["ok"]:
                return model, info
        return None, info

    def snapshot(self) -> dict:
        return {"available": self.available, "error": self.error,
                "fetched_at": self.fetched_at, "plan_cap": self.plan_cap,
                "shared": self.shared(),
                "usage": {window: dict(values) for window, values in self.usage.items()}}


def _cli(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Xem quota con lai theo model.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--models", default=None, help="danh sach model, cach nhau bang dau phay")
    args = parser.parse_args(argv)

    tracker = QuotaTracker(root=args.root).refresh(force=args.refresh)
    models = ([item.strip() for item in args.models.split(",") if item.strip()]
              if args.models else list(tracker.caps))
    report = {model: tracker.remaining(model) for model in models}
    if args.json:
        print(json.dumps({"tracker": tracker.snapshot(), "models": report},
                         ensure_ascii=False, indent=2))
    else:
        if not tracker.available:
            print(f"CANH BAO: {tracker.error or 'khong co du lieu'}")
        shared = tracker.shared()
        print(f"NGAN SACH CHUNG (plan ${shared['cap']:.2f}/thang): "
              f"5h {shared['windows']['today']['pct']:.1f}% / "
              f"tuan {shared['windows']['week']['pct']:.1f}% / "
              f"thang {shared['windows']['month']['pct']:.1f}% "
              f"{'OK' if shared['ok'] else 'CAN'}")
        print("")
        print(f"{'MODEL':<40} {'5H%':>6} {'TUAN%':>6} {'THANG%':>7}  OK")
        for model, entry in report.items():
            if entry["unlimited"]:
                print(f"{model:<40} {'-':>6} {'-':>6} {'-':>7}  yes (free)")
                continue
            w = entry["windows"]
            print(f"{model:<40} {w['today']['pct']:>6.1f} {w['week']['pct']:>6.1f} "
                  f"{w['month']['pct']:>7.1f}  {'yes' if entry['ok'] else 'NO'}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
