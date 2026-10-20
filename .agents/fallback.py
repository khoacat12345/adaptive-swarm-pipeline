#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fallback chain + passive health memory (Tasks 3.1, 3.2).

Nguyen tac:

  * KHONG ping truoc. Goi thang model dau tien; chi khi THAT BAI moi chuyen con
    ke tiep trong CHINH luot goi do (khong can nguoi dung can thiep).
  * Moi lan that bai ghi vao bo nho suc khoe: {model, latency, status, error_type}
    va dat cooldown (429/quota: 5 phut, timeout: 2 phut, model khong ton tai: 1 gio).
  * Model dang cooldown thi bi bo qua ngay, khong dot request.
  * Loi do *chat luong ban sua* (noop / patch / deleted) van chuyen model de thu
    con khac, nhung KHONG cooldown (model van song, chi la lan nay khong sua).

Tich hop: `recipe_c` chi can mot coder; `FallbackCoder` dong vai tro do.

    coder = FallbackCoder(models, factory=lambda m: workers.AgenticCoder(m),
                          health=HealthRegistry(path))
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from workers import Coder, WorkerError

# Cooldown theo loai loi (giay). 0 = ghi nhan nhung khong chan.
COOLDOWN_SECONDS = {
    "quota": 300,
    "rate_limit": 300,
    "timeout": 120,
    "model": 3600,
    "cli": 60,
    "config": 3600,
    "error": 60,
    "noop": 0,
    "patch": 0,
    "parse": 0,
    "deleted": 0,
    "exhausted": 0,
}


def cooldown_for(kind: str | None) -> int:
    return int(COOLDOWN_SECONDS.get(str(kind or "error"), 60))


class HealthRegistry:
    """Bo nho suc khoe thu dong. Khong ping, chi ghi nhan ket qua that."""

    def __init__(self, path=None, *, cooldowns: dict | None = None,
                 now=None, max_entries: int = 200) -> None:
        self.path = Path(path) if path else None
        self.cooldowns = dict(COOLDOWN_SECONDS)
        if cooldowns:
            self.cooldowns.update({str(key): int(value) for key, value in cooldowns.items()})
        self.now = now or time.time
        self.max_entries = max_entries
        self.entries: dict[str, dict] = {}
        self.load()

    # -- persistence -------------------------------------------------------
    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        entries = data.get("entries") if isinstance(data, dict) else None
        if isinstance(entries, dict):
            self.entries = {str(key): value for key, value in entries.items()
                            if isinstance(value, dict)}

    def save(self) -> None:
        if self.path is None:
            return
        trimmed = dict(sorted(self.entries.items(),
                              key=lambda item: item[1].get("last_at", 0),
                              reverse=True)[: self.max_entries])
        self.entries = trimmed
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"entries": trimmed}, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except OSError:
            pass

    # -- api ---------------------------------------------------------------
    def cooled(self, model: str) -> tuple[bool, int]:
        """(dang bi chan?, so giay con lai)."""
        entry = self.entries.get(str(model)) or {}
        until = float(entry.get("cooldown_until") or 0)
        remaining = int(until - float(self.now()))
        return (remaining > 0, max(remaining, 0))

    def record(self, model: str, *, ok: bool, kind: str | None = None,
               latency_ms: int = 0, error: str | None = None) -> dict:
        key = str(model)
        now = float(self.now())
        entry = dict(self.entries.get(key) or {})
        entry["model"] = key
        entry["last_at"] = now
        entry["last_status"] = "ok" if ok else "fail"
        entry["last_latency_ms"] = int(latency_ms)
        entry["last_error_type"] = None if ok else str(kind or "error")
        entry["last_error"] = None if ok else str(error or "")[:200]
        entry["successes"] = int(entry.get("successes", 0)) + (1 if ok else 0)
        entry["failures"] = int(entry.get("failures", 0)) + (0 if ok else 1)
        if ok:
            entry["cooldown_until"] = 0
        else:
            entry["cooldown_until"] = now + int(self.cooldowns.get(str(kind or "error"), 60))
        self.entries[key] = entry
        self.save()
        return entry

    def snapshot(self) -> dict:
        now = float(self.now())
        result = {}
        for key, entry in self.entries.items():
            cooled, remaining = self.cooled(key)
            result[key] = {
                "status": entry.get("last_status"),
                "error_type": entry.get("last_error_type"),
                "latency_ms": entry.get("last_latency_ms"),
                "cooldown_remaining_s": remaining if cooled else 0,
                "successes": entry.get("successes", 0),
                "failures": entry.get("failures", 0),
                "last_at": entry.get("last_at"),
            }
        return result


def _default_factory(model: str) -> Coder:
    """Mac dinh: coder kieu agent (model tu sua file trong ban sao tam)."""
    import workers
    return workers.AgenticCoder(model)


class FallbackCoder(Coder):
    """Boc nhieu model thanh MOT coder, tu chuyen con khi con truoc that bai."""

    kind = "fallback"

    def __init__(self, models, *, factory=None, health: HealthRegistry | None = None,
                 verbose: bool = False) -> None:
        super().__init__()
        self.models = [str(model) for model in models if str(model).strip()]
        if not self.models:
            raise ValueError("FallbackCoder can it nhat 1 model")
        self.name = "fallback:" + "->".join(self.models)
        self.factory = factory or _default_factory
        self.health = health or HealthRegistry()
        self.verbose = verbose
        self.attempts: list[dict] = []
        self.skipped: list[dict] = []
        self._children: dict[str, Coder] = {}
        # recipe_c gan ham nay de chan fallback dot qua ngan sach request.
        self.limit_check = None

    # -- workspace ---------------------------------------------------------
    def bind_workspace(self, project_root, sandbox=None) -> None:
        super().bind_workspace(project_root, sandbox)
        for child in self._children.values():
            if hasattr(child, "bind_workspace"):
                child.bind_workspace(project_root, sandbox)

    def _child(self, model: str) -> Coder:
        child = self._children.get(model)
        if child is None:
            child = self.factory(model)
            if hasattr(child, "bind_workspace") and self.project_root is not None:
                child.bind_workspace(self.project_root, self.cwd)
            self._children[model] = child
        child.hints = list(self.hints)
        return child

    # -- patch -------------------------------------------------------------
    def patch(self, work_order, *, current_content: str, feedback: str | None,
              round_index: int) -> dict:
        return self._dispatch("patch", work_order, current_content=current_content,
                              feedback=feedback, round_index=round_index)

    def patch_set(self, work_order, *, current_files: dict, feedback: str | None,
                  round_index: int) -> dict:
        return self._dispatch("patch_set", work_order, current_files=current_files,
                              feedback=feedback, round_index=round_index)

    def _dispatch(self, method: str, work_order, **kwargs) -> dict:
        feedback = kwargs.get("feedback")
        round_index = kwargs.get("round_index", 1)
        failures: list[str] = []
        for model in self.models:
            if self.limit_check is not None and not self.limit_check():
                raise WorkerError(f"het ngan sach request truoc khi goi {model}", kind="budget")
            cooled, remaining = self.health.cooled(model)
            if cooled:
                self.skipped.append({"model": model, "round": round_index,
                                     "remaining_s": remaining})
                failures.append(f"{model}: dang cooldown ({remaining}s)")
                continue

            child = self._child(model)
            started = time.perf_counter()
            calls_before = int(getattr(child, "calls", 0) or 0)
            try:
                result = getattr(child, method)(work_order, **kwargs)
            except Exception as exc:                       # noqa: BLE001
                latency_ms = int((time.perf_counter() - started) * 1000)
                kind = getattr(exc, "kind", "error")
                self.calls += max(1, int(getattr(child, "calls", 0) or 0) - calls_before)
                self.health.record(model, ok=False, kind=kind, latency_ms=latency_ms,
                                   error=str(exc)[:200])
                self.errors.append(f"{model}: {exc}")
                self.error_kinds.append(kind)
                self.attempts.append({
                    "model": model, "round": round_index, "status": "fail",
                    "error_type": kind, "latency_ms": latency_ms,
                    "error": str(exc)[:200],
                })
                failures.append(f"{model}: {kind}")
                if self.verbose:
                    print(f"fallback: {model} fail ({kind}) sau {latency_ms}ms -> thu model ke tiep")
                continue

            latency_ms = int((time.perf_counter() - started) * 1000)
            self.calls += max(1, int(getattr(child, "calls", 0) or 0) - calls_before)
            self.health.record(model, ok=True, latency_ms=latency_ms)
            if isinstance(getattr(child, "usage", None), dict):
                self.usage.update(child.usage)
            self.attempts.append({
                "model": model, "round": round_index, "status": "ok",
                "latency_ms": latency_ms,
            })
            if self.verbose:
                print(f"fallback: {model} ok sau {latency_ms}ms")
            return result

        raise WorkerError("tat ca model deu that bai: " + " | ".join(failures)[:600],
                          kind="exhausted")

    # -- bao cao -----------------------------------------------------------
    def stats(self) -> dict:
        base = super().stats()
        base.update({
            "models": list(self.models),
            "attempts": list(self.attempts),
            "skipped": list(self.skipped),
            "health": self.health.snapshot(),
            "fallback_count": sum(1 for item in self.attempts if item["status"] == "fail"),
        })
        return base
