#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quota tracker: doc usage THAT tu Go usage API, fallback `opencode stats`.

Nguon uu tien: GET https://opencode.ai/zen/go/v1/usage voi API key trong
credential `opencode-go`. Tra ve rolling/weekly/monthly percent truc tiep.
Fallback: `opencode.db` (session_message cost/token) va console cookie cho
cac cai dat cu.

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
from dataclasses import dataclass
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
GO_USAGE_URL = "https://opencode.ai/zen/go/v1/usage"


def _go_api_key() -> str | None:
    """Read the OpenCode Go API key from the local credential table."""
    con = _connect()
    try:
        row = con.execute(
            "select value from credential where integration_id like 'opencode%' "
            "and active=1 order by integration_id='opencode-go' desc limit 1"
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    try:
        credential = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    token = credential.get("key") or credential.get("access") or credential.get("token")
    return str(token) if token else None


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


def _read_go_usage_api(key: str, timeout: int) -> dict | None:
    """Authoritative Go usage: GET /zen/go/v1/usage (API-key auth).

    Response shape:
      {"usage": {"rolling": {"percent": 2, "resetsAt": ...},
                 "weekly":  {"percent": 40, ...},
                 "monthly": {"percent": 51, ...}}}
    """
    request = urllib.request.Request(
        GO_USAGE_URL,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                 "User-Agent": "adaptive-swarm/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None
    result: dict[str, dict] = {}
    for source, target in (("rolling", "today"), ("weekly", "week"),
                           ("monthly", "month")):
        item = usage.get(source) or {}
        if not isinstance(item, dict) or item.get("percent") is None:
            continue
        pct = max(0.0, min(100.0, float(item.get("percent") or 0.0)))
        reset = item.get("resetsAt")
        result[target] = {
            "used": round(pct, 1), "limit": 100.0,
            "remaining": round(100.0 - pct, 1), "pct": round(pct, 1),
            "resets_at": reset, "resets_at_ms": _iso_ms(reset),
            "source": "go-api",
        }
    return result or None


def read_go_status(timeout: int = 6) -> dict | None:
    """Doc meters CHINH XAC tu OpenCode Go usage API, fallback console."""
    if os.environ.get("SWARM_CONSOLE", "1") == "0":
        return None
    key = _go_api_key()
    if key:
        meters = _read_go_usage_api(key, timeout)
        if meters:
            return meters
    # Console fallback requires a browser session cookie; keep it for older
    # installations where the API key endpoint is unavailable.
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
            "source": "console",
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
            source = next((item.get("source") for item in self.meters.values()
                           if item.get("source")), "console")
            return {"ok": ok, "cap": month.get("limit", 0), "windows": windows,
                    "worst_pct": max((item["pct"] for item in windows.values()), default=0),
                    "source": source}
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

    # -- dispatch headroom (F05) -------------------------------------
    @classmethod
    def from_snapshot(cls, payload: dict | None, *, root=None,
                      plan_cap: float | None = None,
                      cache_ttl: int = 0) -> "QuotaTracker":
        """Build an offline tracker from a cached snapshot.

        Dispatch tests and air-gapped machines must never hit the network or a
        live sqlite database.  The snapshot shape is exactly ``snapshot()``.
        """
        tracker = cls(root=root, plan_cap=plan_cap, cache_ttl=cache_ttl)
        data = payload if isinstance(payload, dict) else {}
        tracker.available = bool(data.get("available"))
        tracker.error = data.get("error")
        tracker.usage = {
            window: {str(model): float(cost) for model, cost in (values or {}).items()}
            for window, values in (data.get("usage") or {}).items()
            if isinstance(values, dict)
        }
        tracker.spend = dict(data.get("spend") or {})
        tracker.reset = dict(data.get("reset") or {})
        tracker.hourly = list(data.get("hourly") or [])
        tracker.requests = list(data.get("requests") or [])
        tracker.message_count = int(data.get("message_count") or 0)
        tracker.meters = dict(data.get("meters") or {})
        tracker.console = bool(data.get("console"))
        tracker.console_error = data.get("console_error")
        tracker.fetched_at = float(data.get("fetched_at") or 0.0)
        if data.get("plan_cap") is not None:
            tracker.plan_cap = float(data["plan_cap"])
        return tracker

    def headroom(self, model: str, *, reserve_pct: float = 15.0) -> dict:
        """Quota decision for one more dispatch of ``model``.

        ``reserve_pct`` keeps that share of every window free for verification
        and reviewers.  Free/unknown-capped models are unlimited; a paid model
        with unknown usage is *not* silently treated as healthy.
        """
        return evaluate_headroom(
            model, self.remaining(model), reserve_pct=reserve_pct,
        )

    def choose_with_headroom(
        self,
        chain: list[str],
        *,
        reserve_pct: float = 15.0,
        refresh: bool = True,
    ) -> tuple[str | None, dict]:
        """Pick the first model in ``chain`` with dispatch headroom."""
        if refresh:
            self.refresh()
        info = {
            model: evaluate_headroom(
                model, self.remaining(model), reserve_pct=reserve_pct,
            )
            for model in chain
        }
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


    # ------------------------------------------------------------------
    # F05 normalized snapshot helpers (scheduler / dashboard projection)
    # ------------------------------------------------------------------
    def model_snapshot(self, model: str, *, reserve_pct: float = 15.0) -> dict:
        """Stable per-model view built on ``evaluate_headroom``."""
        decision = evaluate_headroom(
            model, self.remaining(model), reserve_pct=reserve_pct,
        )
        worst = float(decision.get("worst_pct") or 0.0)
        return {
            "model": model,
            "ok": bool(decision.get("ok")),
            "unlimited": bool(decision.get("unlimited")),
            "unknown": bool(decision.get("unknown")),
            "reason": decision.get("reason") or "",
            "headroom_pct": round(max(0.0, 100.0 - worst), 1),
            "worst_pct": round(worst, 1),
            "reserve_pct": decision.get("reserve_pct", reserve_pct),
            "wait_seconds": decision.get("wait_seconds", 0),
            "windows": self.remaining(model).get("windows") or {},
            "source": "free" if decision.get("unlimited") else
                      ("unknown" if decision.get("unknown") else "tracker"),
        }

    def snapshot_models(self, models=None, *, reserve_pct: float = 15.0) -> dict:
        """``{model: model_snapshot}`` for an explicit chain or every capped model."""
        names = list(models) if models else list(self.caps)
        rows = {
            str(name): self.model_snapshot(str(name), reserve_pct=reserve_pct)
            for name in names
        }
        return {
            "available": bool(self.available),
            "error": self.error,
            "plan_cap": self.plan_cap,
            "reserve_pct": reserve_pct,
            "shared": self.shared(),
            "models": rows,
        }

    def best(self, models, *, reserve_pct: float = 15.0) -> tuple[str | None, dict]:
        """Pick the model with the most headroom; free models win ties."""
        rows = {
            str(name): self.model_snapshot(str(name), reserve_pct=reserve_pct)
            for name in models
        }
        usable = [(name, row) for name, row in rows.items() if row["ok"]]
        if not usable:
            return None, rows
        usable.sort(
            key=lambda item: (bool(item[1]["unlimited"]), item[1]["headroom_pct"]),
            reverse=True,
        )
        return usable[0][0], rows


def load_tracker(*, root=None, refresh: bool = True, **kwargs) -> QuotaTracker:
    """Best-effort tracker factory: quota is advisory, never fatal."""
    tracker = QuotaTracker(root=root, **kwargs)
    if refresh:
        try:
            tracker.refresh()
        except Exception as exc:  # noqa: BLE001
            tracker.error = f"refresh failed: {str(exc)[:160]}"
    return tracker


def snapshot(models=None, *, root=None, tracker: QuotaTracker | None = None,
             refresh: bool = True) -> dict:
    """Module-level snapshot helper for CLI/scheduler callers."""
    active = tracker or load_tracker(root=root, refresh=refresh)
    payload = active.snapshot_models(models)
    payload["fetched_at"] = active.fetched_at
    return payload


def _clamp_pct(value, default: float = 15.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(95.0, number))


def evaluate_headroom(model: str, info: dict | None, *,
                      reserve_pct: float = 15.0) -> dict:
    """Pure quota decision used by the dispatch gate.

    ``info`` is a ``QuotaTracker.remaining(model)`` payload.  The function is
    intentionally total: missing or malformed data fails closed for paid models
    and stays open for models without a cap.
    """
    reserve = _clamp_pct(reserve_pct)
    data = info if isinstance(info, dict) else {}
    if data.get("unlimited"):
        return {
            "model": str(model), "ok": True, "unlimited": True,
            "reason": "no monthly cap", "reserve_pct": reserve,
            "worst_pct": 0.0, "blocked_windows": [], "wait_seconds": 0,
        }
    if data.get("unknown") or not data:
        return {
            "model": str(model), "ok": False, "unlimited": False,
            "unknown": True, "reason": "quota usage is unavailable",
            "reserve_pct": reserve, "worst_pct": 0.0,
            "blocked_windows": [], "wait_seconds": 0,
            "shared": data.get("shared") or {},
        }
    windows: dict[str, dict] = {}
    blocked: list[str] = []
    wait_seconds = 0
    # A provider that answered 429 (or a caller-recorded cooldown) is exhausted
    # for this dispatch even when the spend windows still look healthy.  This
    # keeps the gate honest when rate limits, not budget, are the bottleneck.
    raw_rate = data.get("rate_limit")
    rate = raw_rate if isinstance(raw_rate, dict) else {}
    limited = bool(data.get("rate_limited") or rate.get("limited"))
    if isinstance(raw_rate, (int, float)) and not isinstance(raw_rate, bool):
        limited = limited or raw_rate > 0
    if limited:
        blocked.append("rate_limit")
        try:
            wait_seconds = int(
                rate.get("wait_seconds") or data.get("retry_after") or 0
            )
        except (TypeError, ValueError):
            wait_seconds = 0
    for window in ("today", "week", "month"):
        item = data.get("windows", {}).get(window) or {}
        if not item:
            continue
        limit = float(item.get("limit") or 0.0)
        used = float(item.get("used") or 0.0)
        if limit <= 0:
            continue
        allowed = limit * (1.0 - reserve / 100.0)
        remaining = max(0.0, allowed - used)
        pct = (remaining / allowed * 100.0) if allowed > 0 else 0.0
        windows[window] = {
            "used": round(used, 4), "limit": round(limit, 4),
            "allowed": round(allowed, 4), "remaining": round(remaining, 4),
            "headroom_pct": round(pct, 1),
        }
        if used >= allowed:
            blocked.append(window)
    shared = data.get("shared") if isinstance(data.get("shared"), dict) else {}
    if shared and not shared.get("ok", True):
        blocked.append("shared")
        if shared.get("worst_pct") is not None:
            wait_seconds = 0
    worst = float(data.get("worst_pct") or 0.0)
    ok = not blocked and bool(data.get("ok", True))
    reason = "headroom available" if ok else "quota reserve exhausted: " + (
        ", ".join(blocked) or "unknown"
    )
    return {
        "model": str(model), "ok": ok, "unlimited": False,
        "reason": reason, "reserve_pct": reserve,
        "worst_pct": round(worst, 1), "windows": windows,
        "blocked_windows": blocked, "wait_seconds": int(wait_seconds),
        "shared": shared,
    }


def choose_with_headroom(
    chain: list[str],
    info: dict,
    *,
    reserve_pct: float = 15.0,
) -> tuple[str | None, dict]:
    """Pick a dispatchable model from precomputed ``remaining`` payloads."""
    decisions = {
        model: evaluate_headroom(
            model, (info or {}).get(model), reserve_pct=reserve_pct,
        )
        for model in chain
    }
    for model in chain:
        if decisions[model]["ok"]:
            return model, decisions
    return None, decisions


# --------------------------------------------------------------------------
# Dispatch gate (F05): "may this task start on this model right now?"
# --------------------------------------------------------------------------
@dataclass
class QuotaDecision:
    """One dispatch decision with redaction-safe evidence."""

    model: str | None = None
    ok: bool = True
    action: str = "dispatch"  # dispatch | reassign | pause
    reason: str = ""
    wait_seconds: int = 0
    evidence: dict = None

    def __post_init__(self) -> None:
        if self.evidence is None:
            self.evidence = {}

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "ok": bool(self.ok),
            "action": self.action,
            "reason": self.reason,
            "wait_seconds": int(self.wait_seconds or 0),
            "evidence": dict(self.evidence or {}),
        }


def _dedupe(values) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


class QuotaGate:
    """Stateful dispatch gate over a :class:`QuotaTracker`.

    * a model without a monthly cap is always dispatchable;
    * a paid model must keep ``reserve_pct`` free in every window;
    * an exhausted model is reassigned along the candidate chain;
    * when nothing has headroom the task is paused until a window resets.

    The gate never performs a model call and never touches the repository.
    """

    def __init__(
        self,
        tracker: "QuotaTracker | None" = None,
        *,
        root=None,
        reserve_pct: float = 15.0,
        verifier_reserve_pct: float = 10.0,
        candidates=(),
        strict: bool | None = None,
        refresh: bool = False,
    ) -> None:
        self.tracker = tracker
        self.root = Path(root).resolve() if root else None
        self.reserve_pct = _clamp_pct(reserve_pct)
        self.verifier_reserve_pct = _clamp_pct(verifier_reserve_pct)
        self.candidates = _dedupe(candidates)
        self.strict = strict
        self.refresh = bool(refresh)
        self._refreshed = tracker is not None
        self.decisions: list[dict] = []

    def _ensure_tracker(self) -> "QuotaTracker":
        if self.tracker is None:
            self.tracker = QuotaTracker(root=self.root)
        if self.refresh and not self._refreshed:
            try:
                self.tracker.refresh()
            except Exception:  # noqa: BLE001 - the gate must never crash a run
                pass
            self._refreshed = True
        return self.tracker

    def _strict(self) -> bool:
        if self.strict is not None:
            return bool(self.strict)
        return str(os.environ.get("SWARM_QUOTA_STRICT", "0")).strip().lower() in {
            "1", "true", "yes", "on",
        }

    def decide(
        self,
        model: str | None,
        *,
        candidates=(),
        phase_id: str | None = None,
        task_id: str | None = None,
        reserve_pct: float | None = None,
    ) -> QuotaDecision:
        reserve = _clamp_pct(self.reserve_pct if reserve_pct is None else reserve_pct)
        base = str(model or "").strip()
        chain = _dedupe([base, *candidates, *self.candidates])
        if not chain:
            return QuotaDecision(
                model=None, ok=True, action="dispatch",
                reason="no model binding; quota gate not applicable",
                evidence={"phase_id": phase_id, "task_id": task_id},
            )

        tracker = self._ensure_tracker()
        decisions: dict[str, dict] = {}
        for candidate in chain:
            try:
                info = tracker.remaining(candidate)
            except Exception as exc:  # noqa: BLE001
                info = {"model": candidate, "ok": False, "unknown": True,
                        "error": str(exc)[:200]}
            decisions[candidate] = evaluate_headroom(
                candidate, info, reserve_pct=reserve,
            )

        chosen: str | None = None
        for candidate in chain:
            if decisions[candidate].get("ok"):
                chosen = candidate
                break

        shared: dict = {}
        for decision in decisions.values():
            if decision.get("shared"):
                shared = decision["shared"]
                break

        evidence = {
            "phase_id": phase_id,
            "task_id": task_id,
            "requested": base or None,
            "chain": chain,
            "reserve_pct": reserve,
            "verifier_reserve_pct": self.verifier_reserve_pct,
            "windows": {
                name: decisions[name].get("windows", {}) for name in chain
            },
            "blocked": {
                name: decisions[name].get("blocked_windows", []) for name in chain
            },
            "shared": shared,
        }

        if chosen is not None:
            action = "dispatch" if chosen == base else "reassign"
            decision = QuotaDecision(
                model=chosen,
                ok=True,
                action=action,
                reason=(
                    decisions[chosen].get("reason") if action == "dispatch"
                    else f"reassigned from {base or 'none'} to {chosen}"
                ),
                evidence=evidence,
            )
            self.decisions.append(decision.as_dict())
            return decision

        unknown_paid = [
            name for name in chain if decisions[name].get("unknown")
        ]
        if unknown_paid and not self._strict():
            decision = QuotaDecision(
                model=base or (chain[0] if chain else None),
                ok=True,
                action="dispatch",
                reason=(
                    "quota usage unavailable; dispatching in non-strict mode "
                    "(set SWARM_QUOTA_STRICT=1 to fail closed)"
                ),
                evidence={**evidence, "ungated": True, "unknown": unknown_paid},
            )
            self.decisions.append(decision.as_dict())
            return decision

        blocked = sorted({
            window for name in chain
            for window in decisions[name].get("blocked_windows", [])
        })
        wait = self.wait_seconds_for(chain)
        decision = QuotaDecision(
            model=base or (chain[0] if chain else None),
            ok=False,
            action="pause",
            reason=(
                "quota reserve exhausted"
                + (f" for: {', '.join(blocked)}" if blocked else "")
            ),
            wait_seconds=wait,
            evidence=evidence,
        )
        self.decisions.append(decision.as_dict())
        return decision

    def wait_seconds_for(self, models) -> int:
        """Seconds until the earliest quota window reset, if known."""
        tracker = self._ensure_tracker()
        try:
            snapshot = tracker.snapshot()
        except Exception:  # noqa: BLE001
            snapshot = {}
        resets = []
        for window in ("today", "week", "month"):
            try:
                value = int((snapshot.get("reset") or {}).get(window) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                resets.append(value)
        if not resets:
            return 0
        now_ms = int(time.time() * 1000)
        return max(0, int((min(resets) - now_ms) / 1000))

    def snapshot(self) -> dict:
        tracker = self._ensure_tracker()
        names = list(self.candidates) or list(getattr(tracker, "caps", {}))
        try:
            remaining = {model: tracker.remaining(model) for model in names}
        except Exception:  # noqa: BLE001
            remaining = {}
        try:
            tracker_snapshot = tracker.snapshot()
        except Exception:  # noqa: BLE001
            tracker_snapshot = {}
        return {
            "strict": self._strict(),
            "reserve_pct": self.reserve_pct,
            "verifier_reserve_pct": self.verifier_reserve_pct,
            "candidates": list(self.candidates),
            "tracker": tracker_snapshot,
            "remaining": remaining,
            "decisions": list(self.decisions[-50:]),
        }

    def headroom_table(self, models=None) -> dict[str, dict]:
        tracker = self._ensure_tracker()
        names = _dedupe(models or self.candidates or getattr(tracker, "caps", {}))
        return {
            model: tracker.headroom(model, reserve_pct=self.reserve_pct)
            for model in names
        }


def build_gate(
    root=None,
    *,
    tracker: "QuotaTracker | None" = None,
    candidates=(),
    reserve_pct: float = 15.0,
    strict: bool | None = None,
    refresh: bool = False,
) -> QuotaGate:
    return QuotaGate(
        tracker, root=root, candidates=candidates,
        reserve_pct=reserve_pct, strict=strict, refresh=refresh,
    )


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
            source = "console"
            for key, label in (("today", "5h"), ("week", "7d"), ("month", "30d")):
                item = tracker.meters.get(key) or {}
                source = item.get("source") or source
                if item.get("source") == "go-api":
                    parts.append(f"{label} {item.get('pct', 0):.1f}%")
                else:
                    parts.append(f"{label} {item.get('pct', 0):.1f}% "
                                 f"(${item.get('used', 0):.2f}/${item.get('limit', 0):.0f})")
            print(f"{source}: " + "  ".join(parts))
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
