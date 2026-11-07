#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safe v2 notification bridge for Telegram/Discord/generic webhooks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import notify
import secret_scan

_WIN_PATH = re.compile(r"[A-Za-z]:\\[^\s\"'<>]+")
_POSIX_PATH = re.compile(r"(?<![\w.])/(?:home|Users|tmp|var|etc)/[^\s\"'<>]+")
EVENT_LABELS = {
    "HANDOFF_READY": "Run completed and handoff is ready",
    "USAGE_WAITING": "Run is waiting for Usage approval",
    "BUDGET_OVERRUN": "Run stopped: budget overrun",
    "CRASH": "Run crashed or was interrupted",
}


def redact_secrets(text: str) -> str:
    value = str(text or "")
    try:
        value, _findings = secret_scan.scan_text(value, filename="notification")
    except Exception:
        pass
    value = _WIN_PATH.sub("[REDACTED_PATH]", value)
    value = _POSIX_PATH.sub("[REDACTED_PATH]", value)
    return value


def _message(event: str, payload: dict[str, Any]) -> str:
    label = EVENT_LABELS.get(str(event), str(event))
    run_id = payload.get("run_id") or "?"
    status = payload.get("status") or ""
    details = []
    for key in ("phase_id", "reason", "requests", "cost_usd", "handoff"):
        if payload.get(key) is not None:
            details.append(f"{key}={payload[key]}")
    suffix = (" | " + ", ".join(details)) if details else ""
    return redact_secrets(f"[{label}] run={run_id} status={status}{suffix}")


def notify_event(root: str | Path, event: str, payload: dict[str, Any] | None = None,
                 *, config: dict | None = None) -> dict:
    """Send one safe webhook message. Never raises."""
    payload = dict(payload or {})
    text = _message(event, payload)
    try:
        cfg = config if config is not None else notify.load_config(root)
        if not cfg.get("enabled"):
            return {"ok": False, "skipped": True, "text": text}
        return {**notify.send(text, config=cfg), "text": text}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200], "text": text}
