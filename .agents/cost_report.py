#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cost report (Task 3.8) - ke toan chi phi tu ledger.jsonl.

Doc ledger, gom theo run_id, xuat bang:

    run_id    status   requests  in_tok  out_tok  duration  fallback

`fallback` = so lan phai doi model / tong so request (ty le fallback da dien ra).
Chi doc event `recipe.done` (1 event / run). Thieu truong -> tinh la 0, khong raise.

    python .agents/cost_report.py                 # 20 run gan nhat
    python .agents/cost_report.py --run rc_xxx    # 1 run
    python .agents/cost_report.py --json
"""

from __future__ import annotations

from pathlib import Path

import ledger as ledger_module

DEFAULT_LIMIT = 20


def load_events(path=None, root=None) -> list[dict]:
    if path is not None:
        events = []
        file_path = Path(path)
        if not file_path.exists():
            return []
        for line in file_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                import json
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events
    return ledger_module.read_events(root=root or Path.cwd())


def _usage(event: dict) -> tuple[int, int, int]:
    usage = event.get("usage") or {}
    return (int(usage.get("in_tokens", 0) or 0),
            int(usage.get("out_tokens", 0) or 0),
            int(usage.get("cache_read_tokens", 0) or 0))


def summarize(events: list[dict], *, run_id: str | None = None,
              limit: int | None = DEFAULT_LIMIT) -> dict:
    runs: dict[str, dict] = {}
    for event in events:
        if event.get("event_type") != "recipe.done":
            continue
        current_run = str(event.get("run_id") or "run_unbound")
        if run_id and current_run != run_id:
            continue
        data = event.get("data") or {}
        result = event.get("result") or {}
        in_tokens, out_tokens, cache_tokens = _usage(event)
        entry = runs.setdefault(current_run, {
            "run_id": current_run,
            "task_id": data.get("task_id"),
            "status": data.get("status") or result.get("status"),
            "requests": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "cache_read_tokens": 0,
            "duration_ms": 0,
            "fallback_attempts": 0,
            "models": [],
            "error": result.get("error"),
        })
        entry["requests"] += int(data.get("requests", 0) or 0)
        entry["tokens_in"] += in_tokens
        entry["tokens_out"] += out_tokens
        entry["cache_read_tokens"] += cache_tokens
        entry["duration_ms"] += int(event.get("duration_ms", 0) or 0)
        entry["fallback_attempts"] += int(data.get("fallback_attempts", 0) or 0)
        models = data.get("models") or []
        if isinstance(models, list):
            entry["models"] = sorted(set(entry["models"]) | {str(model) for model in models})

    ordered = sorted(runs.values(), key=lambda item: item["run_id"], reverse=True)
    if limit is not None and limit >= 0:
        ordered = ordered[:limit]

    for entry in ordered:
        requests = entry["requests"]
        entry["fallback_rate"] = round(entry["fallback_attempts"] / requests, 3) if requests else 0.0
        entry["duration_s"] = round(entry["duration_ms"] / 1000.0, 1)

    totals = {
        "runs": len(ordered),
        "requests": sum(item["requests"] for item in ordered),
        "tokens_in": sum(item["tokens_in"] for item in ordered),
        "tokens_out": sum(item["tokens_out"] for item in ordered),
        "cache_read_tokens": sum(item["cache_read_tokens"] for item in ordered),
        "duration_ms": sum(item["duration_ms"] for item in ordered),
        "fallback_attempts": sum(item["fallback_attempts"] for item in ordered),
    }
    totals["avg_duration_ms"] = round(totals["duration_ms"] / totals["runs"], 1) if totals["runs"] else 0
    totals["fallback_rate"] = (round(totals["fallback_attempts"] / totals["requests"], 3)
                               if totals["requests"] else 0.0)
    return {"runs": ordered, "totals": totals}


def render(summary: dict) -> str:
    lines = [
        f"{'run_id':<22} {'status':<9} {'req':>4} {'in_tok':>9} {'out_tok':>9} "
        f"{'time_s':>7} {'fb_rate':>7}",
        "-" * 74,
    ]
    for entry in summary["runs"]:
        lines.append(
            f"{entry['run_id'][:22]:<22} {str(entry['status'])[:9]:<9} "
            f"{entry['requests']:>4} {entry['tokens_in']:>9} {entry['tokens_out']:>9} "
            f"{entry['duration_s']:>7} {entry['fallback_rate']:>7}"
        )
    totals = summary["totals"]
    lines.append("-" * 74)
    lines.append(
        f"{'TOTAL':<22} {'':<9} {totals['requests']:>4} {totals['tokens_in']:>9} "
        f"{totals['tokens_out']:>9} {round(totals['duration_ms'] / 1000.0, 1):>7} "
        f"{totals['fallback_rate']:>7}"
    )
    lines.append(
        f"runs={totals['runs']}  avg_time={totals['avg_duration_ms']}ms  "
        f"fallbacks={totals['fallback_attempts']}  cache_read={totals['cache_read_tokens']}"
    )
    return "\n".join(lines)


def build_parser():
    import argparse
    parser = argparse.ArgumentParser(description="Bao cao chi phi tu ledger.")
    parser.add_argument("--ledger", default=None, help="duong dan ledger.jsonl")
    parser.add_argument("--root", default=".")
    parser.add_argument("--run", default=None, help="chi 1 run_id")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None) -> int:
    import json
    args = build_parser().parse_args(argv)
    events = load_events(args.ledger, root=args.root)
    summary = summarize(events, run_id=args.run, limit=args.limit)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
