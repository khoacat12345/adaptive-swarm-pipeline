#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quota tracker: doc usage THAT tu `opencode stats` va cat theo tran.

Nguon du lieu: `opencode.db` (session_message: cost/token tung request) - chinh xac
theo rolling 5h/7d/30d. Fallback: `opencode stats` neu DB khong doc duoc.

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
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

def _default_bin() -> str:
    found = shutil.which("opencode")
    if found:
        return found
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA") or "", "npm", "opencode.cmd")
    return "opencode"


DEFAULT_BIN = _default_bin()

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
# The console is authoritative.  Current Go usage is ~52% at $15.62, i.e. a
# ~$30 monthly cap; the old $14 estimate falsely marked a healthy plan as
# exhausted.  Override with SWARM_PLAN_MONTHLY_USD when the plan changes.
def _plan_cap_default() -> float:
    raw = os.environ.get("SWARM_PLAN_MONTHLY_USD", "30")
    try:
        return float(raw)
    except ValueError:
        return 30.0


PLAN_MONTHLY_USD = _plan_cap_default()


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


# --------------------------------------------------------------------------
# Nguon CHINH XAC: opencode.db (cost tung request, co timestamp)
# --------------------------------------------------------------------------
def db_path() -> Path:
    env = os.environ.get("OPENCODE_DB")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    if not path.exists():
        raise FileNotFoundError(str(path))
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


CONSOLE_STATUS_URL = "https://opencode.ai/console/api/go/status"


def _console_token() -> str:
    con = _connect()
    try:
        row = con.execute(
            "select value from credential where integration_id='opencode' and active=1 limit 1"
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise RuntimeError("no opencode credential")
    credential = json.loads(row[0])
    token = credential.get("access")
    if not token:
        raise RuntimeError("no console access token")
    return str(token)


def _iso_ms(value) -> int:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def read_go_status(timeout: int = 6) -> dict | None:
    """Doc meters CHINH XAC tu console (used/limit/resetsAt)."""
    if os.environ.get("SWARM_CONSOLE", "1") == "0":
        return None
    token = _console_token()
    request = urllib.request.Request(
        CONSOLE_STATUS_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                 "User-Agent": "adaptive-swarm/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    meters = (data.get("access") or {}).get("meters") or {}
    result: dict[str, dict] = {}
    for source, target in (("fiveHour", "today"), ("week", "week"), ("month", "month")):
        item = meters.get(source) or {}
        limit = int(item.get("limitMicroCents") or 0) / 1e8
        used = int(item.get("usedMicroCents") or 0) / 1e8
        if limit <= 0:
            continue
        result[target] = {
            "used": round(used, 4), "limit": round(limit, 4),
            "remaining": round(max(0.0, limit - used), 4),
            "pct": round(used / limit * 100.0, 1),
            "resets_at": item.get("resetsAt"),
            "resets_at_ms": _iso_ms(item.get("resetsAt")),
        }
    return result or None


def read_messages(days: int = 30) -> list[dict]:
    """Doc cost/token tung assistant message (rolling window chinh xac)."""
    cutoff = int((time.time() - days * 86400) * 1000)
    con = _connect()
    try:
        rows = con.execute(
            """
            select sm.data, sm.session_id, s.project_id, p.worktree
            from session_message sm
            left join session_v2 s on s.id = sm.session_id
            left join project p on p.id = s.project_id
            where sm.type = 'assistant' and sm.time_created >= ?
            """, (cutoff,)).fetchall()
    finally:
        con.close()
    messages: list[dict] = []
    for data, session_id, project_id, worktree in rows:
        try:
            obj = json.loads(data)
        except (TypeError, json.JSONDecodeError):
            continue
        model = obj.get("model") or {}
        provider = str(model.get("providerID") or "")
        model_id = str(model.get("id") or "")
        if not provider or not model_id:
            continue
        timing = obj.get("time") or {}
        created = int(timing.get("created") or 0)
        completed = int(timing.get("completed") or created)
        tokens = obj.get("tokens") or {}
        cache = tokens.get("cache") or {}
        messages.append({
            "t": completed or created,
            "model": f"{provider}/{model_id}",
            "cost": float(obj.get("cost") or 0.0),
            "tokens_in": int(tokens.get("input") or 0),
            "tokens_out": int(tokens.get("output") or 0),
            "tokens_cache": int(cache.get("read") or 0),
            "duration_ms": max(0, completed - created),
            "session": session_id,
            "project": worktree or project_id or "",
        })
    return messages


def workspaces(*, limit: int = 50) -> list[dict]:
    """Danh sach project/workspace + thong ke (thay cho 'history' kieu run)."""
    con = _connect()
    try:
        rows = con.execute(
            """
            select min(p.id) as id,
                   coalesce(nullif(max(p.name), ''), min(p.worktree)) as label,
                   min(p.worktree) as worktree,
                   max(p.time_active) as active_at,
                   count(s.id) as sessions,
                   coalesce(sum(s.cost), 0) as cost,
                   coalesce(sum(s.tokens_input), 0) as tokens_in,
                   coalesce(sum(s.tokens_output), 0) as tokens_out,
                   max(s.time_updated) as last_used,
                   max(p.time_created) as created_at
            from project p
            left join session_v2 s on s.project_id = p.id
            where lower(replace(p.worktree, '\\', '/')) not like '%/appdata/local/temp/%'
              and lower(replace(p.worktree, '\\', '/')) not like '%/temp/%'
              and instr(lower(replace(p.worktree, '\\', '/')), '/swarm_worker_') = 0
            group by lower(replace(p.worktree, '\\', '/'))
            having count(s.id) > 0
            order by coalesce(max(p.time_active), max(p.time_created)) desc
            limit ?
            """, (limit,)).fetchall()
    finally:
        con.close()
    return [{"id": row[0], "name": row[1], "worktree": row[2],
             "active_at": row[3], "sessions": row[4], "cost": round(row[5] or 0, 4),
             "tokens_in": row[6] or 0, "tokens_out": row[7] or 0,
             "last_used": row[8]} for row in rows]


class QuotaTracker:
    """Doc usage that + quyet dinh model nao con du dia.

    Cache trong `.agents/tmp/quota.json` (mac dinh 60s) de khong goi CLI
    nhieu lan trong cung mot run.
    """

    def __init__(self, *, root=None, binary: str | None = None,
                 caps: dict | None = None, cache_ttl: int = 60,
                 plan_cap: float | None = None) -> None:
        self.root = Path(root).resolve() if root else Path.cwd()
        self.binary = binary
        self.caps = dict(caps or CAPS)
        self.plan_cap = float(plan_cap if plan_cap is not None else PLAN_MONTHLY_USD)
        self.cache_ttl = cache_ttl
        self.usage: dict[str, dict[str, float]] = {name: {} for name in WINDOW_DAYS}
        self.spend: dict[str, dict] = {}
        self.reset: dict[str, int] = {}
        self.hourly: list[dict] = []
        self.meters: dict[str, dict] = {}
        self.console = False
        self.console_error: str | None = None
        self.requests: list[dict] = []
        self.message_count = 0
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
            self.spend = cached.get("spend") or {}
            self.reset = cached.get("reset") or {}
            self.hourly = cached.get("hourly") or []
            self.requests = cached.get("requests") or []
            self.message_count = int(cached.get("message_count") or 0)
            self.available = bool(cached.get("available"))
            self.error = cached.get("error")
            self.meters = cached.get("meters") or {}
            self.console = bool(cached.get("console"))
            self.console_error = cached.get("console_error")
            self.fetched_at = float(cached.get("at") or now)
            return self

        try:
            messages = read_messages(days=30)
            self._load(messages)
            self.available = True
            self.error = None
        except Exception as exc:                    # noqa: BLE001
            self.usage = {window: {} for window in WINDOW_DAYS}
            self.spend = {}
            self.requests = []
            self.message_count = 0
            self.available = False
            self.error = f"db: {str(exc)[:160]}"

        try:
            meters = read_go_status()
            self.meters = meters or {}
            self.console = bool(meters)
            self.console_error = None if meters else "console unavailable"
        except Exception as exc:                    # noqa: BLE001
            self.meters = {}
            self.console = False
            self.console_error = str(exc)[:160]

        self.fetched_at = now
        self._write_cache()
        return self

    def _load(self, messages: list[dict]) -> None:
        now = time.time() * 1000
        windows = {"today": now - 5 * 3600 * 1000,
                   "week": now - 7 * 86400 * 1000,
                   "month": now - 30 * 86400 * 1000}
        local = time.localtime()
        midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday,
                                0, 0, 0, 0, 0, -1)) * 1000
        self.usage = {window: {} for window in windows}
        for message in messages:
            for window, cutoff in windows.items():
                if message["t"] >= cutoff:
                    model = message["model"]
                    self.usage[window][model] = self.usage[window].get(model, 0.0) + message["cost"]

        self.spend = {}
        self.reset = {}
        self.hourly = [{"hour": hour, "cost": 0.0, "tokens": 0, "tokens_in": 0,
                        "tokens_out": 0, "tokens_cache": 0, "requests": 0}
                       for hour in range(24)]
        for window, cutoff in {**windows, "calendar_today": midnight}.items():
            selected = [message for message in messages if message["t"] >= cutoff]
            self.spend[window] = {
                "cost": round(sum(message["cost"] for message in selected), 4),
                "requests": len(selected),
                "tokens_in": sum(message["tokens_in"] for message in selected),
                "tokens_out": sum(message["tokens_out"] for message in selected),
                "tokens_cache": sum(message["tokens_cache"] for message in selected),
            }
            if window == "calendar_today":
                self.reset[window] = midnight + 86400 * 1000
            else:
                span = {"today": 5 * 3600, "week": 7 * 86400,
                        "month": 30 * 86400}[window] * 1000
                oldest = min((message["t"] for message in selected), default=None)
                self.reset[window] = oldest + span if oldest else now + span
        for message in messages:
            if message["t"] >= midnight:
                hour = time.localtime(message["t"] / 1000).tm_hour
                bucket = self.hourly[hour]
                bucket["cost"] += message["cost"]
                bucket["tokens_in"] += message["tokens_in"]
                bucket["tokens_out"] += message["tokens_out"]
                bucket["tokens_cache"] += message["tokens_cache"]
                bucket["tokens"] += message["tokens_in"] + message["tokens_out"]
                bucket["requests"] += 1
        for bucket in self.hourly:
            bucket["cost"] = round(bucket["cost"], 4)
        self.requests = sorted(messages, key=lambda message: message["t"],
                               reverse=True)[:60]
        self.message_count = len(messages)

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
            payload = {"at": self.fetched_at, "available": self.available,
                       "error": self.error, "usage": self.usage, "spend": self.spend,
                       "reset": self.reset, "hourly": self.hourly,
                       "requests": self.requests, "message_count": self.message_count,
                       "meters": self.meters, "console": self.console,
                       "console_error": self.console_error}
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except OSError:
            pass

    def shared(self) -> dict:
        """Ngan sach CHUNG cua plan: uu tien meters that tu console."""
        if self.meters:
            windows: dict[str, dict] = {}
            ok = True
            for key in ("today", "week", "month"):
                item = self.meters.get(key) or {}
                if not item:
                    continue
                windows[key] = {"used": item["used"], "limit": item["limit"],
                                "remaining": item["remaining"], "pct": item["pct"]}
                if item["pct"] >= 100:
                    ok = False
            month = self.meters.get("month") or {}
            return {"ok": ok, "cap": month.get("limit", 0), "windows": windows,
                    "worst_pct": max((item["pct"] for item in windows.values()), default=0),
                    "source": "console"}
        windows = {}
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
                "windows": windows, "worst_pct": round(worst, 1),
                "source": "estimate"}

    def remaining(self, model: str) -> dict:
        cap = self.caps.get(model)
        if not cap:
            return {"model": model, "unlimited": True, "ok": True,
                    "windows": {}, "worst_pct": 0.0}
        if not self.available:
            return {"model": model, "unlimited": False, "ok": False,
                    "unknown": True, "windows": {}, "worst_pct": 0.0}
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
        reset = dict(self.reset)
        for key in ("today", "week", "month"):
            item = self.meters.get(key) or {}
            if item.get("resets_at_ms"):
                reset[key] = item["resets_at_ms"]
        return {"available": self.available, "error": self.error,
                "fetched_at": self.fetched_at, "plan_cap": self.plan_cap,
                "shared": self.shared(), "spend": self.spend,
                "reset": reset, "hourly": self.hourly,
                "requests": self.requests, "message_count": self.message_count,
                "console": self.console, "console_error": self.console_error,
                "meters": self.meters,
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
        labels = (("calendar_today", "today"), ("today", "5h"),
                  ("week", "7d"), ("month", "30d"))
        for key, label in labels:
            spend = tracker.spend.get(key) or {}
            print(f"{label:<6} ${spend.get('cost', 0):8.2f}  "
                  f"{spend.get('requests', 0):>5} req  "
                  f"{spend.get('tokens_in', 0):>9} / {spend.get('tokens_out', 0):<8} tok")
        if tracker.console:
            parts = []
            for key, label in (("today", "5h"), ("week", "7d"), ("month", "30d")):
                item = tracker.meters.get(key) or {}
                parts.append(f"{label} {item.get('pct', 0):.1f}% "
                             f"(${item.get('used', 0):.2f}/${item.get('limit', 0):.0f})")
            print("console: " + "  ".join(parts))
        else:
            print("console: khong lay duoc meters that (dang dung uoc luong)")
        print("")
        print(f"{'MODEL':<40} {'5H$':>8} {'7D$':>8} {'30D$':>8}  STATUS")
        for model in models:
            if model not in tracker.caps:
                print(f"{model:<40} {'-':>8} {'-':>8} {'-':>8}  free")
                continue
            cost = lambda window: (tracker.usage.get(window) or {}).get(model, 0.0)
            print(f"{model:<40} {cost('today'):>8.2f} {cost('week'):>8.2f} "
                  f"{cost('month'):>8.2f}  paid")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
