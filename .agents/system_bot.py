#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SystemBot Runner — chay lenh kiem tra THAT tren may, co guard + timeout + JSON.

Trach nhiem:
  1. Chan lenh nguy hiem (xoa du lieu, format, doi he thong) va lenh mang.
  2. Timeout (mac dinh 30s) -> ngat ca cay process con, khong bo roi.
  3. Thu thap exit_code + 40 dong cuoi cua stdout/stderr (khong gioi han bo nho).
  4. Tra ve JSON co cau truc + ghi ledger event.

Quy uoc exit code:
  0    -> lenh chay xong, exit code 0
  N    -> exit code that cua process
  124  -> timeout
  126  -> bi guard chan

CLI:
  python system_bot.py --cmd "npm test"
  python system_bot.py --cmd "pytest -q" --timeout 60 --cwd .
  python system_bot.py --check "curl http://x"        # chi kiem tra guard
  python system_bot.py -- pytest -q                   # passthrough
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger  # noqa: E402  (module cung thu muc .agents/)

AGENTS_DIR = Path(__file__).resolve().parent
DEFAULT_TIMEOUT = 30
TAIL_LINES = 40
MAX_TAIL_BYTES = 256 * 1024

EXIT_TIMEOUT = 124
EXIT_BLOCKED = 126

# --------------------------------------------------------------------------
# Guard: chan lenh nguy hiem / lenh mang
# --------------------------------------------------------------------------

# Lenh pha huy: phai xuat hien o dau cau lenh hoac ngay sau toan tu noi chuoi.
_DESTRUCTIVE_TOKENS = [
    "rm", "del", "erase", "rmdir", "rd", "deltree",
    "mkfs", "diskpart", "fdisk", "shutdown", "reboot", "halt",
    "vssadmin", "bcdedit", "takeown", "icacls",
    "remove-item", "clear-disk", "format-volume", "stop-computer",
]
_CHAIN = r"(?:^|[;&|]|\|\||&&)\s*"
_DESTRUCTIVE_RE = re.compile(
    rf"{_CHAIN}(?:[\w./\\-]*[\\/])?(?:{'|'.join(_DESTRUCTIVE_TOKENS)})(?:\s|$)",
    re.IGNORECASE,
)
_FORMAT_DRIVE_RE = re.compile(r"\bformat\s+[a-zA-Z]:", re.IGNORECASE)
_RM_FORCE_RE = re.compile(r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+", re.IGNORECASE)

# Lenh mang: chan tuyet doi (tru khi --allow-network).
_NETWORK_TOKENS = [
    "curl", "wget", "nc", "netcat", "telnet", "ftp", "sftp", "scp", "ssh",
    "invoke-webrequest", "invoke-restmethod", "start-bitstransfer",
    "test-netconnection", "bitsadmin", "tftp",
]
_NETWORK_RE = re.compile(
    rf"(?:^|[\s;&|(])('|/)?(?:{'|'.join(_NETWORK_TOKENS)})(?:\.exe)?(?:'|\"|\s|$)",
    re.IGNORECASE,
)

# Trinh quan ly goi (doi mang) — chan mac dinh, mo bang --allow-network.
_INSTALL_TOKENS = [
    "pip install", "pip3 install", "npm install", "npm i ", "npm ci", "npm add",
    "pnpm install", "pnpm add", "yarn install", "yarn add",
    "cargo install", "go install", "go get", "apt-get", "apt install",
    "choco install", "winget install", "gem install", "composer require",
    "dotnet restore", "nuget restore", "conda install",
]
_INSTALL_RE = re.compile(
    rf"(?:^|[\s;&|(])(?:{'|'.join(re.escape(t) for t in _INSTALL_TOKENS)})",
    re.IGNORECASE,
)

# Command substitution + redirection ghi de file (nguy co mat du lieu).
_SUBST_RE = re.compile(r"\$\(|`")
_REDIRECT_RE = re.compile(r"(?<![0-9&])>{1,2}(?!&|\s*nul\b|\s*NUL\b)")

MAX_COMMAND_LENGTH = 2000


def check_command(command: str, allow_network: bool = False) -> dict:
    """Kiem tra lenh co duoc phep chay khong. Tra ve dict co 'allowed'."""
    cmd = (command or "").strip()
    if not cmd:
        return {"allowed": False, "category": "empty", "reason": "Lenh rong"}
    if len(cmd) > MAX_COMMAND_LENGTH:
        return {
            "allowed": False,
            "category": "too_long",
            "reason": f"Lenh dai qua {MAX_COMMAND_LENGTH} ky tu -- tu choi de an toan",
        }

    m = _DESTRUCTIVE_RE.search(cmd)
    if m:
        return {
            "allowed": False,
            "category": "destructive",
            "reason": f"Lenh pha huy bi chan: '{m.group(0).strip()}'",
        }
    if _FORMAT_DRIVE_RE.search(cmd):
        return {"allowed": False, "category": "destructive", "reason": "Lenh format o dia bi chan"}
    if _RM_FORCE_RE.search(cmd):
        return {"allowed": False, "category": "destructive", "reason": "rm -rf bi chan"}

    if not allow_network:
        m = _NETWORK_RE.search(cmd)
        if m:
            return {
                "allowed": False,
                "category": "network",
                "reason": f"Lenh mang bi chan: '{m.group(0).strip()}' (dung --allow-network neu that su can)",
            }
        m = _INSTALL_RE.search(cmd)
        if m:
            return {
                "allowed": False,
                "category": "network_install",
                "reason": f"Cai dat goi (doi mang) bi chan: '{m.group(0).strip()}'",
            }

    if _SUBST_RE.search(cmd):
        return {
            "allowed": False,
            "category": "substitution",
            "reason": "Command substitution ($( ) hoac backtick) bi chan",
        }
    if _REDIRECT_RE.search(cmd):
        return {
            "allowed": False,
            "category": "redirect",
            "reason": "Redirection ghi file (>) bi chan -- dung safe_writer de ghi file",
        }

    return {"allowed": True, "category": "ok", "reason": None}


# --------------------------------------------------------------------------
# Chay lenh
# --------------------------------------------------------------------------

def _tail_lines(path: Path, n: int = TAIL_LINES) -> list[str]:
    """Doc n dong cuoi cua file lon ma khong nap ca file vao bo nho."""
    if not path.exists():
        return []
    size = path.stat().st_size
    with open(path, "rb") as fh:
        if size > MAX_TAIL_BYTES:
            fh.seek(-MAX_TAIL_BYTES, os.SEEK_END)
            chunk = fh.read()
            chunk = chunk.split(b"\n", 1)[-1]
        else:
            chunk = fh.read()
    text = chunk.decode("utf-8", errors="replace")
    return text.splitlines()[-n:]


def _kill_tree(proc: subprocess.Popen) -> None:
    """Ngat ca cay process con (Windows: taskkill /T, POSIX: killpg)."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=15,
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), 9)
            except ProcessLookupError:
                pass
    except Exception:
        pass
    finally:
        try:
            proc.kill()
        except Exception:
            pass


def run_command(
    command: str,
    *,
    cwd=None,
    timeout: int = DEFAULT_TIMEOUT,
    root=None,
    run_id: str | None = None,
    allow_network: bool = False,
    tail_lines: int = TAIL_LINES,
    log: bool = True,
    tmp_dir: Path | None = None,
) -> dict:
    """Chay 1 lenh va tra ve ket qua co cau truc."""
    started = time.perf_counter()
    work_dir = str(Path(cwd).resolve()) if cwd else os.getcwd()
    guard = check_command(command, allow_network=allow_network)

    if not guard["allowed"]:
        result = {
            "command": command,
            "cwd": work_dir,
            "allowed": False,
            "blocked_reason": guard["reason"],
            "blocked_category": guard["category"],
            "exit_code": EXIT_BLOCKED,
            "timed_out": False,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "stdout_tail": [],
            "stderr_tail": [],
            "output_tail": [],
        }
        if log:
            ledger.append_event(
                "system_bot.blocked",
                run_id=run_id,
                duration_ms=result["duration_ms"],
                result={"status": "blocked", "error": guard["reason"]},
                data={
                    "command": command,
                    "cwd": work_dir,
                    "category": guard["category"],
                },
                root=root,
            )
        return result

    tmp_dir = Path(tmp_dir) if tmp_dir else (
        ledger.resolve_agents_dir(root=root) / "tmp"
    )
    tmp_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{os.getpid()}_{int(started * 1000) % 100000}"
    out_path = tmp_dir / f"bot_{stamp}.out"
    err_path = tmp_dir / f"bot_{stamp}.err"

    creationflags = 0
    popen_extra: dict = {}
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_extra["start_new_session"] = True

    exit_code = None
    timed_out = False
    spawn_error = None

    try:
        with open(out_path, "wb") as out_fh, open(err_path, "wb") as err_fh:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=work_dir,
                stdout=out_fh,
                stderr=err_fh,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                **popen_extra,
            )
            try:
                exit_code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc)
                exit_code = EXIT_TIMEOUT
    except OSError as exc:  # khong spawn duoc
        spawn_error = str(exc)
        exit_code = EXIT_BLOCKED

    duration_ms = int((time.perf_counter() - started) * 1000)
    stdout_tail = _tail_lines(out_path, tail_lines)
    stderr_tail = _tail_lines(err_path, tail_lines)
    output_tail = (stdout_tail + stderr_tail)[-tail_lines:]

    if timed_out:
        status = "timeout"
    elif spawn_error:
        status = "error"
    elif exit_code == 0:
        status = "pass"
    else:
        status = "fail"

    result = {
        "command": command,
        "cwd": work_dir,
        "allowed": True,
        "blocked_reason": None,
        "blocked_category": None,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "timeout_seconds": timeout,
        "duration_ms": duration_ms,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "output_tail": output_tail,
        "spawn_error": spawn_error,
        "result": status,
    }

    if log:
        ledger.append_event(
            "system_bot.run",
            run_id=run_id,
            duration_ms=duration_ms,
            result={"status": status, "error": spawn_error},
            data={
                "command": command,
                "cwd": work_dir,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "stdout_lines": len(stdout_tail),
                "stderr_lines": len(stderr_tail),
                "output_tail": output_tail[-5:],  # ledger chi luu 5 dong de khong phinh
            },
            root=root,
        )

    # Don file tam ngay sau khi doc xong
    for tmp in (out_path, err_path):
        try:
            tmp.unlink()
        except OSError:
            pass

    return result


def format_report(result: dict, max_lines: int = 40) -> str:
    """Ban bao cao nguoi doc duoc, dung de in ra terminal/bao cao."""
    head = (
        f"[SystemBot] {result['result'].upper()}  exit={result['exit_code']}  "
        f"{result['duration_ms']}ms"
    )
    lines = [head, f"  cmd : {result['command']}", f"  cwd : {result['cwd']}"]
    if result.get("blocked_reason"):
        lines.append(f"  CHAN: {result['blocked_reason']}")
    tail = result.get("output_tail") or []
    if tail:
        lines.append(f"  --- {min(len(tail), max_lines)} dong cuoi ---")
        lines.extend(f"  | {line}" for line in tail[-max_lines:])
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SystemBot Runner (guard + timeout + JSON)")
    parser.add_argument("--cmd", default=None, help="lenh can chay")
    parser.add_argument("--check", default=None, help="chi kiem tra guard, khong chay")
    parser.add_argument("--cwd", default=None, help="thu muc chay lenh")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--root", default=None, help="project root (de ghi ledger)")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--tail", type=int, default=TAIL_LINES)
    parser.add_argument("--no-ledger", action="store_true", help="khong ghi ledger")
    parser.add_argument("--human", action="store_true", help="in bao cao nguoi doc thay vi JSON")
    parser.add_argument("passthrough", nargs=argparse.REMAINDER)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.check is not None:
        verdict = check_command(args.check, allow_network=args.allow_network)
        verdict["command"] = args.check
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
        return 0 if verdict["allowed"] else EXIT_BLOCKED

    command = args.cmd
    if not command:
        extra = [a for a in (args.passthrough or []) if a != "--"]
        if extra:
            command = " ".join(extra)
    if not command:
        print("Loi: can --cmd hoac -- <lenh>", file=sys.stderr)
        return 2

    result = run_command(
        command,
        cwd=args.cwd,
        timeout=args.timeout,
        root=args.root,
        run_id=args.run_id,
        allow_network=args.allow_network,
        tail_lines=args.tail,
        log=not args.no_ledger,
    )

    if args.human:
        print(format_report(result, max_lines=args.tail))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["exit_code"] or 0)


if __name__ == "__main__":
    raise SystemExit(main())
