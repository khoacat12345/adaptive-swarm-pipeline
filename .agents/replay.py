#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replay CLI (Task 4.2) - doc lai lich su tu ledger, 0 request / 0 token.

    python .agents/replay.py                 # run gan nhat
    python .agents/replay.py rc_2026...      # 1 run cu the
    python .agents/replay.py --last --json
    python .agents/replay.py --list          # liet ke cac run (moi nhat truoc)

Khong goi API model, khong chay verify, khong ghi file. Chi doc ledger.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import schema

NODE_COLOR = {
    "lead": "\x1b[36m",        # cyan
    "coder": "\x1b[35m",       # magenta
    "reviewer": "\x1b[33m",    # yellow
    "systembot": "\x1b[32m",   # green
    "security": "\x1b[31m",    # red
    "qa": "\x1b[37m",          # grey
    "system": "\x1b[90m",
}
STATUS_COLOR = {
    "pass": "\x1b[32m", "ok": "\x1b[32m",
    "fail": "\x1b[31m", "error": "\x1b[31m",
    "blocked": "\x1b[33m", "timeout": "\x1b[33m",
}
RESET = "\x1b[0m"
BOLD = "\x1b[1m"


def _enable_ansi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:                       # noqa: BLE001
        pass


def _ts(value: str) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _fmt_duration(ms: int) -> str:
    if ms >= 60_000:
        return f"{ms / 60_000:.1f}m"
    if ms >= 1_000:
        return f"{ms / 1_000:.1f}s"
    return f"{ms}ms"


def _short(value, limit: int = 110) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _message(event: dict, norm: dict) -> str:
    """Cau mo ta 1 dong cho timeline (tieng Viet khong dau)."""
    kind = norm["event_type"]
    data = norm["details"]
    if kind == "recipe.start":
        return (f"nhan task {data.get('task_id') or '?'} (Level {data.get('level')})"
                f" - file {data.get('target')}"
                f" | verify: {_short(data.get('verify_command'), 70)}")
    if kind == "plan.validated":
        nodes = data.get("nodes") or []
        return f"plan hop le: {' -> '.join(str(node) for node in nodes)}"
    if kind == "clarify.required":
        problems = data.get("problems") or []
        return f"DUNG - can lam ro {len(problems)} diem truoc khi chay"
    if kind == "memory.hints":
        hints = data.get("hints") or []
        return f"nap {len(hints)} goi y tu memory: {_short(hints[0], 80) if hints else ''}"
    if kind == "recipe.coder":
        state = "nhap ban sua" if norm["status"] in ("ok", "pass") else "that bai"
        return f"{state} (vong {data.get('round')})"
    if kind == "recipe.ladder":
        history = [item for item in (data.get("history") or []) if isinstance(item, dict)]
        levels = " -> ".join(str(item.get("level_name")) for item in history)
        state = "ok" if norm["status"] in ("ok", "pass") else "that bai"
        return f"format ladder {levels} ({len(history)} lan thu) {state}"
    if kind == "recipe.draft_verify":
        code = data.get("exit_code")
        return f"verify ban nhap ({data.get('mode', '?')}) exit={code}"
    if kind == "reviewer.findings":
        verdict = data.get("verdict") or norm["status"]
        reasons = data.get("reasons") or []
        return f"ket luan {verdict}" + (f": {_short('; '.join(map(str, reasons)), 90)}"
                                        if reasons else "")
    if kind == "safe_writer.backup":
        return f"backup truoc khi ghi {_short(data.get('target'), 60)}"
    if kind == "safe_writer.write":
        return f"ghi atomic {_short(data.get('target'), 60)}"
    if kind == "safe_writer.verify":
        return f"verify file that exit={data.get('exit_code')}"
    if kind == "safe_writer.rollback":
        return f"ROLLBACK ve ban backup {_short(data.get('target'), 60)}"
    if kind == "transaction.begin":
        return f"mo giao dich da file: {', '.join(data.get('files') or [])}"
    if kind == "transaction.commit":
        return f"chot giao dich {len(data.get('files') or [])} file"
    if kind == "transaction.rollback":
        return f"ROLLBACK toan bo {len(data.get('files') or [])} file: {_short(data.get('reason') or '', 60)}"
    if kind == "transaction.recovered":
        return f"khoi phuc giao dich bi do giua chung: {', '.join(data.get('files') or [])}"
    if kind == "budget.exceeded":
        return f"dung vi cham tran ({data.get('exceeded') or 'ngan sach'})"
    if kind == "recipe.fallback":
        return (f"fallback {data.get('failures', 0)} lan"
                f" tren {len(data.get('models') or [])} model")
    if kind == "recipe.guard.restore":
        return f"guard khoi phuc file bi sua ngoai pipeline: {', '.join(data.get('files') or [])}"
    if kind == "recipe.done":
        error = data.get("error")
        tail = f" - {_short(error, 80)}" if error else ""
        return f"KET LUAN {data.get('status') or norm['status']}{tail}"
    if kind == "secret.blocked":
        return "chan gui secret ra model"
    if kind == "system_bot.run":
        return f"chay lenh may that ({_short(data.get('command'), 60)})"
    if kind == "system_bot.blocked":
        return f"guard chan lenh: {_short(data.get('reason') or data.get('category'), 70)}"
    if kind.startswith("phase0") or kind.startswith("phase05"):
        return f"{kind} {_short(json.dumps(data, ensure_ascii=False), 80)}"
    return f"{kind} {_short(json.dumps(data, ensure_ascii=False), 90)}"


def list_runs(events: list[dict]) -> list[dict]:
    """Tong hop run theo thu tu xuat hien (run cuoi cung = moi nhat)."""
    order: list[str] = []
    stats: dict[str, dict] = {}
    for event in events:
        run_id = str(event.get("run_id") or "run_unbound")
        if run_id not in stats:
            order.append(run_id)
            stats[run_id] = {"run_id": run_id, "events": 0, "start": None, "status": None,
                             "task_id": None, "level": None, "file": None,
                             "duration_ms": 0, "requests": 0, "models": [],
                             "in_tokens": 0, "out_tokens": 0}
        entry = stats[run_id]
        entry["events"] += 1
        entry["in_tokens"] += int((event.get("usage") or {}).get("in_tokens", 0) or 0)
        entry["out_tokens"] += int((event.get("usage") or {}).get("out_tokens", 0) or 0)
        if entry["start"] is None:
            entry["start"] = event.get("timestamp")
        data = event.get("data") or {}
        event_type = event.get("event_type")
        if event_type == "recipe.start":
            entry["task_id"] = data.get("task_id")
            entry["level"] = data.get("level")
            entry["file"] = data.get("target")
        if event_type == "recipe.done":
            entry["status"] = data.get("status") or (event.get("result") or {}).get("status")
            entry["duration_ms"] = int(event.get("duration_ms", 0) or 0)
            entry["requests"] = int(data.get("requests", 0) or 0)
            models = data.get("models") or []
            if isinstance(models, list):
                entry["models"] = list(dict.fromkeys(entry["models"] + [str(m) for m in models]))
    return [stats[run_id] for run_id in reversed(order)]


def pick_run(events: list[dict], run_id: str | None = None) -> str | None:
    if run_id:
        return run_id
    runs = list_runs(events)
    return runs[0]["run_id"] if runs else None


def build_timeline(events: list[dict], run_id: str, *, limit: int = 200) -> list[dict]:
    selected = [event for event in events
                if str(event.get("run_id") or "run_unbound") == str(run_id)]
    if not selected:
        return []
    base = _ts(selected[0].get("timestamp"))
    timeline: list[dict] = []
    for event in selected[:limit]:
        norm = schema.normalize(event)
        offset = max(_ts(norm["timestamp"]) - base, 0.0)
        timeline.append({
            "at_s": round(offset, 3),
            "timestamp": norm["timestamp"],
            "event_type": norm["event_type"],
            "node": norm["node"],
            "model": norm["model"],
            "status": norm["status"],
            "duration_ms": norm["duration_ms"],
            "message": _message(event, norm),
        })
    return timeline


def render_timeline(timeline: list[dict], *, color: bool = True) -> str:
    if not timeline:
        return "(khong co event nao)"
    lines = []
    for item in timeline:
        node = item["node"]
        color_on = NODE_COLOR.get(node, "") if color else ""
        status_color = STATUS_COLOR.get(item["status"], "") if color else ""
        reset = RESET if color else ""
        model = f" {item['model']}" if item["model"] else ""
        label = f"{node:<9}"
        lines.append(
            f"{item['at_s']:>6.1f}s  {color_on}[{label}]{reset}{model} "
            f"{item['message']}"
            + (f"  {status_color}({item['duration_ms']}ms){reset}"
               if item["duration_ms"] else "")
        )
    return "\n".join(lines)


def render_run(events: list[dict], run_id: str, *, color: bool = True,
               limit: int = 200) -> str:
    timeline = build_timeline(events, run_id, limit=limit)
    runs = {run["run_id"]: run for run in list_runs(events)}
    info = runs.get(run_id) or {}
    total = info.get("duration_ms") or (timeline[-1]["duration_ms"] if timeline else 0)
    header = (f"{BOLD if color else ''}RUN {run_id}{RESET if color else ''}  "
              f"task={info.get('task_id') or '?'}  status={info.get('status') or '?'}  "
              f"events={len(timeline)}  time={_fmt_duration(total)}")
    return header + "\n" + "-" * min(len(header) + 8, 100) + "\n" + render_timeline(
        timeline, color=color)


def _ledger_path(args) -> Path:
    if args.ledger:
        return Path(args.ledger)
    root = Path(args.root).resolve()
    if root.name == ".agents":
        return root / "ledger.jsonl"
    return root / ".agents" / "ledger.jsonl"


def build_parser():
    import argparse
    parser = argparse.ArgumentParser(description="Replay lich su chay tu ledger (0 request).")
    parser.add_argument("run_id", nargs="?", default=None)
    parser.add_argument("--last", action="store_true", help="run gan nhat (mac dinh)")
    parser.add_argument("--list", action="store_true", help="liet ke cac run")
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--root", default=".")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _enable_ansi()
    color = not args.no_color and not args.json and sys.stdout.isatty()
    ledger_path = _ledger_path(args)
    events = schema.read_events(ledger_path)
    if not events:
        print(f"khong doc duoc event nao tu {ledger_path}")
        return 1

    if args.list:
        runs = list_runs(events)[: args.limit]
        if args.json:
            print(json.dumps(runs, ensure_ascii=False, indent=2))
        else:
            print(f"{'run_id':<28} {'status':<10} {'events':>6} {'time':>8}  task")
            for entry in runs:
                print(f"{entry['run_id'][:28]:<28} {str(entry['status'])[:10]:<10} "
                      f"{entry['events']:>6} {_fmt_duration(entry['duration_ms']):>8}  "
                      f"{entry.get('task_id') or ''}")
        return 0

    run_id = pick_run(events, args.run_id)
    if run_id is None:
        print("ledger rong")
        return 1
    timeline = build_timeline(events, run_id, limit=args.limit)
    if not timeline:
        print(f"khong tim thay run '{run_id}' trong {ledger_path}")
        return 1

    if args.json:
        print(json.dumps({"run_id": run_id, "timeline": timeline}, ensure_ascii=False, indent=2))
    else:
        print(render_run(events, run_id, color=color, limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
