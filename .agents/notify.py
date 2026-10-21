#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-way webhook notifications (Discord / Telegram / generic).

Config (first match wins):
  1. env: SWARM_WEBHOOK (+ SWARM_WEBHOOK_KIND, SWARM_WEBHOOK_CHAT)
  2. .agents/notify.json:
     {"enabled": true, "kind": "discord", "url": "https://discord.com/api/webhooks/..."}
     {"enabled": true, "kind": "telegram", "url": "https://api.telegram.org/bot<token>/sendMessage",
      "chat_id": "123456"}
     {"enabled": true, "kind": "generic", "url": "http://127.0.0.1:9999/hook"}

Never raises: notification failure must not break a job.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path


def load_config(root) -> dict:
    url = os.environ.get("SWARM_WEBHOOK")
    if url:
        return {"enabled": True,
                "kind": os.environ.get("SWARM_WEBHOOK_KIND", "generic"),
                "url": url,
                "chat_id": os.environ.get("SWARM_WEBHOOK_CHAT")}
    try:
        data = json.loads((Path(root) / ".agents" / "notify.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"enabled": False}
    return data if isinstance(data, dict) else {"enabled": False}


def format_message(event: str, payload: dict) -> str:
    job_id = payload.get("job_id") or "?"
    if event == "plan.submitted":
        return f"New plan {job_id}: {payload.get('subtasks', '?')} subtask(s) waiting for approval."
    if event == "plan.approved":
        return f"Plan {job_id} approved. Starting the swarm."
    if event == "plan.rejected":
        return f"Plan {job_id} rejected. Nothing was written."
    if event == "job.done":
        seconds = payload.get("seconds")
        tail = f" in {seconds}s" if seconds is not None else ""
        return f"Job {job_id}: {payload.get('status', '?')}{tail}."
    return f"{event}: {job_id}"


def send(text: str, *, config: dict | None = None, timeout: int = 10) -> dict:
    config = config or {}
    url = config.get("url")
    if not config.get("enabled") or not url:
        return {"ok": False, "skipped": True}
    kind = str(config.get("kind") or "generic").lower()
    if kind == "discord":
        body = {"content": text}
    elif kind == "telegram":
        body = {"chat_id": config.get("chat_id"), "text": text}
    else:
        body = {"text": text}
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "adaptive-swarm/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"ok": 200 <= response.status < 300, "status": response.status}
    except Exception as exc:                        # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


def notify(event: str, payload: dict, *, root) -> dict:
    """Gui thong bao neu duoc cau hinh; khong bao gio raise."""
    try:
        config = load_config(root)
        if not config.get("enabled"):
            return {"ok": False, "skipped": True}
        return send(format_message(event, payload), config=config)
    except Exception as exc:                        # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python notify.py <text>")
        sys.exit(2)
    print(json.dumps(send(sys.argv[1], config=load_config(".")), ensure_ascii=False))
