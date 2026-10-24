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
import secret_scan

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
    """One English line for the timeline."""
    kind = norm["event_type"]
    data = norm["details"]
    if kind == "recipe.start":
        return (f"task {data.get('task_id') or '?'} (Level {data.get('level')})"
                f" - file {data.get('target')}"
                f" | verify: {_short(data.get('verify_command'), 70)}")
    if kind == "plan.validated":
        nodes = data.get("nodes") or []
        return f"plan valid: {' -> '.join(str(node) for node in nodes)}"
    if kind == "clarify.required":
        problems = data.get("problems") or []
        return f"STOP - clarify {len(problems)} item(s) before running"
    if kind == "memory.hints":
        hints = data.get("hints") or []
        return f"loaded {len(hints)} memory hint(s): {_short(hints[0], 80) if hints else ''}"
    if kind == "recipe.coder":
        state = "patch produced" if norm["status"] in ("ok", "pass") else "patch failed"
        return f"{state} (round {data.get('round')})"
    if kind == "router.decided":
        if data.get("exhausted"):
            extra = " [QUOTA EXHAUSTED]"
        elif data.get("mode") == "fallback":
            extra = " [FALLBACK - free/cheap model]"
        elif data.get("mode") == "escalate":
            extra = " [ESCALATE]"
        else:
            extra = ""
        return (f"router selected {data.get('work_type')} ({data.get('label')}): "
                f"{' -> '.join(data.get('chain') or [])}{extra}")
    if kind == "plan.submitted":
        return f"plan {data.get('job_id')}: {len(data.get('subtasks') or [])} subtask(s) - awaiting approval"
    if kind == "plan.approved":
        return f"plan {data.get('job_id')} approved"
    if kind == "plan.rejected":
        return f"plan {data.get('job_id')} rejected"
    if kind == "job.start":
        return f"job {data.get('job_id')} started ({data.get('subtasks')} subtask(s))"
    if kind == "job.done":
        return f"job {data.get('job_id')} finished: {data.get('status')}"
    if kind == "recipe.ladder":
        history = [item for item in (data.get("history") or []) if isinstance(item, dict)]
        levels = " -> ".join(str(item.get("level_name")) for item in history)
        state = "ok" if norm["status"] in ("ok", "pass") else "failed"
        return f"format ladder {levels} ({len(history)} attempt(s)) {state}"
    if kind == "recipe.draft_verify":
        code = data.get("exit_code")
        return f"draft verify ({data.get('mode', '?')}) exit={code}"
    if kind == "meeting.message":
        recipients = ", ".join(str(item) for item in (data.get("recipients") or []))
        body = _short(_redact(str(data.get("body") or "")), 90)
        return (f"{data.get('sender') or '?'} -> {recipients or '?'} "
                f"[{data.get('kind') or 'message'}] {body}")
    if kind == "meeting.opened":
        return f"meeting opened, round 1 ({len(data.get('roles') or [])} reviewers)"
    if kind == "meeting.conflict":
        return f"meeting found {len(data.get('conflicts') or [])} conflict(s)"
    if kind == "meeting.rebuttal":
        return (f"rebuttal round 2: {data.get('sender') or '?'} -> "
                f"{', '.join(str(item) for item in (data.get('recipients') or []))}")
    if kind == "meeting.vote":
        return f"vote {data.get('role') or '?'} = {data.get('verdict') or '?'}"
    if kind == "meeting.decision":
        return f"meeting decision: {data.get('verdict') or '?'}"
    if kind == "meeting.aborted":
        return f"meeting aborted: {_short(data.get('reason'), 80)}"
    if kind == "reviewer.findings":
        verdict = data.get("verdict") or norm["status"]
        if data.get("fallback_reason"):
            return f"wave fallback heuristic ({verdict}) - review budget exhausted"
        per_role = data.get("per_role") or []
        if per_role:
            roles = ", ".join(
                f"{item.get('role')}({str(item.get('model') or '').split('/')[-1]})="
                f"{item.get('verdict')}"
                for item in per_role if isinstance(item, dict))
            return f"wave {verdict}: {roles}"
        reasons = data.get("reasons") or []
        return f"verdict {verdict}" + (f": {_short('; '.join(map(str, reasons)), 90)}"
                                       if reasons else "")
    if kind == "safe_writer.backup":
        return f"backup before write {_short(data.get('target'), 60)}"
    if kind == "safe_writer.write":
        return f"atomic write {_short(data.get('target'), 60)}"
    if kind == "safe_writer.verify":
        return f"real-file verify exit={data.get('exit_code')}"
    if kind == "safe_writer.rollback":
        return f"ROLLBACK to backup {_short(data.get('target'), 60)}"
    if kind == "transaction.begin":
        return f"multi-file transaction opened: {', '.join(data.get('files') or [])}"
    if kind == "transaction.commit":
        return f"transaction committed: {len(data.get('files') or [])} file(s)"
    if kind == "transaction.rollback":
        return f"ROLLBACK all {len(data.get('files') or [])} file(s): {_short(data.get('reason') or '', 60)}"
    if kind == "transaction.recovered":
        return f"recovered interrupted transaction: {', '.join(data.get('files') or [])}"
    if kind == "budget.exceeded":
        return f"stopped at budget cap ({data.get('exceeded') or 'budget'})"
    if kind == "recipe.fallback":
        return (f"fallback {data.get('failures', 0)} time(s)"
                f" across {len(data.get('models') or [])} model(s)")
    if kind == "recipe.guard.restore":
        return f"guard restored external edit(s): {', '.join(data.get('files') or [])}"
    if kind == "recipe.done":
        error = data.get("error")
        tail = f" - {_short(error, 80)}" if error else ""
        return f"CONCLUSION {data.get('status') or norm['status']}{tail}"
    if kind == "secret.blocked":
        return "blocked secret from leaving the machine"
    if kind == "system_bot.run":
        return f"real machine command ({_short(data.get('command'), 60)})"
    if kind == "system_bot.blocked":
        return f"guard blocked command: {_short(data.get('reason') or data.get('category'), 70)}"
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
            "details": _public_details(norm.get("details") or {}),
        })
    return timeline


_PUBLIC_DETAIL_KEYS = (
    "job_id", "task_id", "status", "level", "round", "mode", "command",
    "exit_code", "timed_out", "target", "files", "changed", "verdict",
    "reasons", "per_role", "skipped", "requests", "error", "attempts",
    "fallback_attempts", "rolled_back", "budget_exceeded", "secrets_blocked",
    "plan_ok", "chain", "work_type", "label", "main", "escalate", "degraded",
    "why", "model", "path", "bytes_written", "atomic", "sha256", "output_tail",
    "transaction", "history", "warnings", "nodes", "problems", "hints",
    "fallback_reason", "budget_skipped", "reviewer_fallback", "reviewer_skipped",
    "sender", "recipients", "kind", "body", "evidence_refs", "reply_to",
    "meeting", "decision", "conflicts", "budget", "participants", "vetoes",
    "dissent", "rounds", "objections", "rebuttals", "id",
)


def _redact(value):
    if isinstance(value, str):
        try:
            return secret_scan.scan_text(value)[0]
        except Exception:
            return value
    return value


def _redact_tree(value, *, depth: int = 0):
    if depth > 5:
        return "[truncated]"
    if isinstance(value, str):
        return _redact(value)[:2000]
    if isinstance(value, list):
        return [_redact_tree(item, depth=depth + 1) for item in value[:30]]
    if isinstance(value, dict):
        return {str(key): _redact_tree(item, depth=depth + 1)
                for key, item in list(value.items())[:40]}
    return value


def _public_details(details: dict) -> dict:
    """Only expose fields the dashboard actually needs; never raw secrets."""
    result = {}
    for key in _PUBLIC_DETAIL_KEYS:
        if key not in details:
            continue
        value = details[key]
        if key == "per_role" and isinstance(value, list):
            roles = []
            for item in value[:8]:
                if not isinstance(item, dict):
                    continue
                roles.append({
                    "role": item.get("role"),
                    "model": item.get("model"),
                    "verdict": item.get("verdict"),
                    "reasons": [_redact(str(reason))[:900]
                                for reason in (item.get("reasons") or [])[:6]],
                    "reviewer": item.get("reviewer"),
                })
            result[key] = roles
        elif key == "attempts" and isinstance(value, list):
            result[key] = [
                {"model": item.get("model"), "round": item.get("round"),
                 "status": item.get("status"), "latency_ms": item.get("latency_ms")}
                for item in value[:8] if isinstance(item, dict)
            ]
        elif key == "evidence_refs" and isinstance(value, list):
            refs = []
            for item in value[:20]:
                if not isinstance(item, dict):
                    continue
                refs.append({
                    "file": item.get("file"),
                    "line_start": item.get("line_start"),
                    "line_end": item.get("line_end"),
                    "verify_event_id": item.get("verify_event_id"),
                    "note": _redact(str(item.get("note") or ""))[:500],
                })
            result[key] = refs
        elif key in ("body", "brief", "diff", "note"):
            result[key] = _redact(str(value))[:4000]
        elif key in ("meeting", "decision", "conflicts"):
            result[key] = _redact_tree(value)
        elif isinstance(value, (str, int, float, bool, list, dict)):
            result[key] = _redact_tree(value) if isinstance(value, (list, dict)) else _redact(value)
    return result


def public_event(event: dict) -> dict:
    """Sanitized event for SSE / browser; no raw secrets."""
    norm = schema.normalize(event)
    return {
        "run_id": norm["run_id"],
        "timestamp": norm["timestamp"],
        "event_type": norm["event_type"],
        "node": norm["node"],
        "model": norm["model"],
        "status": norm["status"],
        "duration_ms": norm["duration_ms"],
        "message": _message(event, norm),
        "details": _public_details(norm.get("details") or {}),
    }


def meeting_messages(events: list[dict], run_id: str | None = None,
                     *, limit: int = 500) -> list[dict]:
    """Parse meeting.message events into transcript rows for the UI/tests."""
    out: list[dict] = []
    for event in events:
        if event.get("event_type") != "meeting.message":
            continue
        if run_id and str(event.get("run_id") or "") != str(run_id):
            continue
        norm = schema.normalize(event)
        data = _public_details(norm.get("details") or {})
        out.append({
            "run_id": norm["run_id"],
            "timestamp": norm["timestamp"],
            "round": data.get("round"),
            "sender": data.get("sender"),
            "recipients": data.get("recipients") or [],
            "kind": data.get("kind"),
            "body": data.get("body") or "",
            "evidence_refs": data.get("evidence_refs") or [],
            "reply_to": data.get("reply_to"),
            "message_id": data.get("id"),
        })
        if len(out) >= limit:
            break
    return out


def meeting_edges(messages: list[dict]) -> list[dict]:
    """Unique sender->recipient edges; no message means no edge."""
    edges: dict[tuple[str, str], dict] = {}
    for message in messages or []:
        sender = str(message.get("sender") or "").lower()
        for recipient in message.get("recipients") or []:
            receiver = str(recipient or "").lower()
            if not sender or not receiver:
                continue
            key = (sender, receiver)
            if key not in edges:
                edges[key] = {"from": sender, "to": receiver,
                              "kind": message.get("kind"),
                              "round": message.get("round"),
                              "count": 0}
            edges[key]["count"] += 1
    return list(edges.values())


def meeting_stats(events: list[dict], run_id: str | None = None) -> dict:
    messages = meeting_messages(events, run_id)
    rounds = sorted({int(item["round"]) for item in messages
                     if item.get("round") in (1, 2)})
    objections = sum(1 for item in messages if item.get("kind") == "objection")
    rebuttals = sum(1 for item in messages if item.get("kind") == "rebuttal")
    vetoes: list[str] = []
    dissent: list[str] = []
    verdict = None
    for event in events:
        if event.get("event_type") != "meeting.decision":
            continue
        if run_id and str(event.get("run_id") or "") != str(run_id):
            continue
        data = _public_details(schema.normalize(event).get("details") or {})
        verdict = data.get("verdict")
        vetoes = [str(item) for item in (data.get("vetoes") or [])]
        dissent = [str(item) for item in (data.get("dissent") or [])]
    requests = 0
    duration_ms = 0
    for event in events:
        if event.get("event_type") != "recipe.done":
            continue
        if run_id and str(event.get("run_id") or "") != str(run_id):
            continue
        data = _public_details(schema.normalize(event).get("details") or {})
        requests = int(data.get("requests") or 0)
        duration_ms = int(event.get("duration_ms", 0) or 0)
    return {
        "rounds": rounds,
        "round_count": len(rounds),
        "messages": len(messages),
        "objections": objections,
        "rebuttals": rebuttals,
        "vetoes": vetoes,
        "dissent": dissent,
        "verdict": verdict,
        "requests": requests,
        "duration_ms": duration_ms,
        "edges": meeting_edges(messages),
    }


def meeting_payload(events: list[dict], run_id: str | None = None) -> dict:
    messages = meeting_messages(events, run_id)
    return {"messages": messages, "edges": meeting_edges(messages),
            "stats": meeting_stats(events, run_id)}


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
