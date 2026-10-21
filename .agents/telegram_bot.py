#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram bot 2 chieu: nhan lenh tu chat de dieu khien job.

Cau hinh (env hoac .agents/notify.json):
  env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  json: {"telegram": {"bot_token": "...", "chat_id": 123456}}

Lenh:
  /status                xem phase / subtask / quota
  /approve /reject       duyet plan dang cho
  /pause /resume /stop   dieu khien job
  /say <noi dung>        chen y kien chi dao cho subtask ke tiep
  /help

Chi chat_id da cau hinh moi duoc dieu khien. Chay:
  python .agents/telegram_bot.py --root .
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import job                            # noqa: E402

API = "https://api.telegram.org/bot{token}/{method}"


def load_config(root) -> dict:
    data: dict = {}
    try:
        data = json.loads((Path(root) / ".agents" / "notify.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    telegram = data.get("telegram") or {}
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or telegram.get("bot_token") or ""
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or telegram.get("chat_id")
               or data.get("chat_id") or "")
    return {"token": str(token), "chat_id": str(chat_id)}


def api_call(method: str, payload: dict, *, token: str, timeout: int = 35) -> dict:
    request = urllib.request.Request(
        API.format(token=token, method=method),
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except Exception as exc:                        # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


def send_message(chat_id, text: str, *, token: str) -> dict:
    return api_call("sendMessage", {"chat_id": chat_id, "text": str(text)[:4000]},
                    token=token, timeout=15)


def _plan_token(root) -> str:
    try:
        return (Path(root) / ".agents" / "tmp" / "plan_token.txt").read_text(
            encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError("no plan submitted yet") from exc


def handle_command(text: str, *, root, send) -> dict:
    """Thuc thi 1 lenh, tra {'ok': bool, 'reply': str}. `send` la callback."""
    command, _, argument = str(text or "").strip().partition(" ")
    command = command.lower().split("@", 1)[0]
    try:
        if command in ("/help", "/start"):
            reply = ("Commands: /status, /approve, /reject, /pause, /resume, /stop, "
                     "/say <direction>")
        elif command == "/status":
            state = job.read_state(root)
            plan_state = job.read_plan_state(root)
            control = job.read_control(root)
            decision = (plan_state.get("decision") or {}).get("status") or "-"
            reply = (f"job: {state.get('job_id') or '-'}\n"
                     f"phase: {state.get('phase') or '-'}\n"
                     f"subtask: {state.get('current') or '-'} "
                     f"({state.get('index') or 0}/{state.get('total') or 0})\n"
                     f"decision: {decision} | paused: {control.get('paused')} | "
                     f"stop: {control.get('stop')}\n"
                     f"direction: {control.get('opinion') or '-'}")
        elif command in ("/approve", "/reject"):
            result = job.decide(root, command[1:], token=_plan_token(root))
            reply = f"decision: {result.get('status')}"
        elif command in ("/pause", "/resume", "/stop"):
            result = job.set_control(root, command[1:],
                                     token=_plan_token(root))
            reply = (f"paused={result.get('paused')} stop={result.get('stop')}")
        elif command == "/say":
            if not argument.strip():
                reply = "usage: /say <direction>"
            else:
                result = job.set_control(root, "opinion", token=_plan_token(root),
                                         value=argument.strip())
                reply = f"direction set: {result.get('opinion')}"
        else:
            reply = "unknown command. Try /help"
        if send is not None:
            send(reply)
        return {"ok": True, "reply": reply}
    except Exception as exc:                        # noqa: BLE001
        reply = f"error: {str(exc)[:300]}"
        if send is not None:
            send(reply)
        return {"ok": False, "reply": reply}


def handle_update(update: dict, *, root, chat_id, send) -> dict:
    message = update.get("message") or {}
    chat = (message.get("chat") or {}).get("id")
    if str(chat) != str(chat_id):
        return {"ok": False, "ignored": True}
    text = str(message.get("text") or "").strip()
    if not text:
        return {"ok": False, "ignored": True}
    return handle_command(text, root=root, send=send)


def run(root, *, token: str, chat_id: str, poll_timeout: int = 25,
        once: bool = False) -> int:
    offset = None
    # Bo qua backlog cu (tranh mot /approve cu quyet dinh plan moi).
    seed = api_call("getUpdates", {"offset": -1, "limit": 1}, token=token, timeout=15)
    seed_items = seed.get("result") or []
    offset = (int(seed_items[-1].get("update_id") or 0) + 1) if seed_items else 0
    while True:
        payload: dict = {"timeout": poll_timeout, "offset": offset}
        data = api_call("getUpdates", payload, token=token,
                        timeout=poll_timeout + 10)
        if not data.get("ok"):
            print(f"getUpdates failed: {data.get('error') or data}", file=sys.stderr)
            time.sleep(3)
            if once:
                return 1
            continue
        for update in data.get("result") or []:
            offset = int(update.get("update_id") or 0) + 1
            handle_update(update, root=root, chat_id=chat_id,
                          send=lambda text: send_message(chat_id, text, token=token))
        if once:
            return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Telegram bot for the swarm.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--token", default=None)
    parser.add_argument("--chat-id", default=None)
    parser.add_argument("--once", action="store_true", help="poll once then exit")
    args = parser.parse_args(argv)

    config = load_config(args.root)
    token = args.token or config["token"]
    chat_id = args.chat_id or config["chat_id"]
    if not token or not chat_id:
        print("missing bot token / chat id.\n"
              "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, or add to "
              ".agents/notify.json:\n"
              '  {"telegram": {"bot_token": "...", "chat_id": 123456}}',
              file=sys.stderr)
        return 2
    return run(args.root, token=token, chat_id=str(chat_id), once=args.once)


if __name__ == "__main__":
    sys.exit(main())
