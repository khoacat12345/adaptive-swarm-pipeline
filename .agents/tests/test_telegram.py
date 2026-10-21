#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 6C - Telegram bot 2 chieu (parser + actions, khong can mang)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import job                            # noqa: E402
import telegram_bot                   # noqa: E402

PY = f'"{sys.executable}"'
TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "telegram_tests"


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


def make_root(name: str) -> Path:
    root = TEMP_ROOT / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "a.py").write_text("def a():\n    return 0\n", encoding="utf-8")
    (root / "check_a.py").write_text(
        "from a import a\nassert a() == 1\nprint('OK')\n", encoding="utf-8")
    return root


def make_plan() -> dict:
    return {
        "job_id": "JTG",
        "objective": "fix a",
        "subtasks": [{
            "id": "S1", "title": "fix a", "objective": "fix a",
            "verify_command": f"{PY} check_a.py", "target_files": ["a.py"],
            "sandbox_files": ["a.py", "check_a.py"], "level": 1,
            "max_rounds": 1, "depends_on": []}],
    }


def test_commands(checks: Checks) -> None:
    root = make_root("commands")
    job.submit_plan(make_plan(), root, token="toktg")
    sent: list[str] = []

    def send(text):
        sent.append(text)

    result = telegram_bot.handle_command("/status", root=root, send=send)
    checks.check("telegram: /status ok", result["ok"] and "phase" in result["reply"], result)

    result = telegram_bot.handle_command("/approve", root=root, send=send)
    checks.check("telegram: /approve", result["ok"] and "approved" in result["reply"], result)
    checks.check("telegram: decision approved",
                 job.read_plan_state(root)["decision"]["status"] == "approved")

    result = telegram_bot.handle_command("/say use pandas", root=root, send=send)
    checks.check("telegram: /say", job.read_control(root)["opinion"] == "use pandas", result)

    telegram_bot.handle_command("/pause", root=root, send=send)
    checks.check("telegram: /pause", job.read_control(root)["paused"] is True)
    telegram_bot.handle_command("/resume", root=root, send=send)
    checks.check("telegram: /resume", job.read_control(root)["paused"] is False)
    telegram_bot.handle_command("/stop", root=root, send=send)
    checks.check("telegram: /stop", job.read_control(root)["stop"] is True)

    checks.check("telegram: /help", "Commands" in
                 telegram_bot.handle_command("/help", root=root, send=send)["reply"])
    checks.check("telegram: lenh la", "unknown" in
                 telegram_bot.handle_command("/xyz", root=root, send=send)["reply"])
    checks.check("telegram: da gui phan hoi", len(sent) >= 7, len(sent))


def test_auth_and_errors(checks: Checks) -> None:
    root = make_root("auth")
    job.submit_plan(make_plan(), root, token="toktg")
    update = {"message": {"chat": {"id": 999}, "text": "/approve"}}
    result = telegram_bot.handle_update(update, root=root, chat_id=1234, send=None)
    checks.check("telegram: sai chat_id -> ignore", result.get("ignored") is True, result)

    update = {"message": {"chat": {"id": 1234}, "text": ""}}
    result = telegram_bot.handle_update(update, root=root, chat_id=1234, send=None)
    checks.check("telegram: tin rong -> ignore", result.get("ignored") is True, result)

    empty = make_root("empty")
    result = telegram_bot.handle_command("/approve", root=empty, send=None)
    checks.check("telegram: chua co plan -> error", result["ok"] is False, result)


def test_config(checks: Checks) -> None:
    root = make_root("config")
    (root / ".agents").mkdir(parents=True, exist_ok=True)
    (root / ".agents" / "notify.json").write_text(
        '{"telegram": {"bot_token": "abc", "chat_id": 123}}', encoding="utf-8")
    config = telegram_bot.load_config(root)
    checks.check("telegram: doc config", config["token"] == "abc" and config["chat_id"] == "123",
                 config)


def test_no_regression(checks: Checks, *, fast: bool = False) -> None:
    if fast:
        print("[skip] hoi quy Wave (--fast)")
        return
    started = subprocess.run([sys.executable, str(AGENTS_DIR / "tests" / "test_wave.py"),
                              "--fast"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=900)
    checks.check("khong hoi quy: Wave", started.returncode == 0,
                 (started.stdout or "")[-300:])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Telegram bot tests.")
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args(argv)

    checks = Checks()
    print("[1/4] commands ...")
    test_commands(checks)
    print("[2/4] auth/errors ...")
    test_auth_and_errors(checks)
    print("[3/4] config ...")
    test_config(checks)
    print("[4/4] khong hoi quy ...")
    test_no_regression(checks, fast=args.fast)

    print("")
    print(f"TELEGRAM: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    sys.exit(main())
