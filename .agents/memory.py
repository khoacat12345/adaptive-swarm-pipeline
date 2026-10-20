#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Memory Engine (Task 4.4) - rut kinh nghiem tu lich su chay.

Doc ledger (khong goi model) va tra loi 2 cau hoi:

  1. Loi nao hay bi may that bat nhat?  (SyntaxError, AssertionError, ...)
  2. Model nao hay timeout / bi cooldown?

`get_suggestions()` tra ve vai cau ngan de chen vao prompt Coder o lan chay sau.
Khi chua du du lieu -> tra ve [] (khong bia loi khuyen).

    python .agents/memory.py                     # bao cao
    python .agents/memory.py --json
    python .agents/memory.py --ledger path.jsonl --limit 5
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import schema

# (regex, nhan, loi khuyen ngan)
ERROR_RULES: tuple[tuple[str, str, str], ...] = (
    (r"khong tao ra thay doi|khong co thay doi nao", "nochange",
     "dam bao ban sua thuc su khac ban hien tai truoc khi tra file"),
    (r"IndentationError|TabError|unexpected indent|sai thut le", "indent",
     "giu nguyen thut le (4 space), khong tron tab va space"),
    (r"SyntaxError|invalid syntax|expected ':'|unexpected EOF|sai cu phap", "syntax",
     "tu chay py_compile tren ban nhap truoc khi tra file"),
    (r"NameError", "name",
     "kiem tra bien/ham da duoc khai bao truoc khi dung"),
    (r"TypeError", "type",
     "kiem tra so luong va kieu tham so khi goi ham"),
    (r"AssertionError", "logic",
     "doc lai acceptance_criteria va tu chay lenh verify cuc bo"),
    (r"ModuleNotFoundError|ImportError", "import",
     "chi dung thu vien chuan, khong them import moi"),
    (r"AttributeError", "attribute",
     "kiem tra thuoc tinh/phuong thuc co ton tai tren doi tuong"),
    (r"IndexError|KeyError", "index",
     "kiem tra gioi han mang/dict truoc khi truy cap"),
    (r"ZeroDivisionError", "divzero",
     "kiem tra mau so khac 0 truoc khi chia"),
    (r"timeout|timed out|exit_code=124", "timeout",
     "giam kich thuoc file hoac chia nho buoc sua"),
)

MIN_SAMPLES = 1


def _classify(text: str) -> str | None:
    blob = str(text or "")
    for pattern, label, _advice in ERROR_RULES:
        if re.search(pattern, blob):
            return label
    return None


def analyze(events: list[dict]) -> dict:
    runs: set[str] = set()
    patterns: dict[str, int] = {}
    advice: dict[str, str] = {}
    models: dict[str, dict] = {}
    cooldowns: list[dict] = []
    verify_failures = 0
    budget_hits = 0

    def _model_entry(name: str) -> dict:
        return models.setdefault(name, {"model": name, "attempts": 0, "failures": 0,
                                        "timeouts": 0, "quota": 0})

    for event in events:
        norm = schema.normalize(event)
        runs.add(norm["run_id"])
        data = norm["details"]
        kind = norm["event_type"]

        if kind == "recipe.coder":
            model = norm["model"] or data.get("model")
            if model:
                entry = _model_entry(str(model))
                entry["attempts"] += 1
                if norm["status"] in ("fail", "error", "timeout"):
                    entry["failures"] += 1
                    error_kind = str(data.get("kind") or "")
                    if "timeout" in error_kind:
                        entry["timeouts"] += 1
                    if "quota" in error_kind:
                        entry["quota"] += 1

        if kind == "recipe.fallback":
            for attempt in data.get("attempts") or []:
                if not isinstance(attempt, dict):
                    continue
                model = attempt.get("model")
                if not model:
                    continue
                entry = _model_entry(str(model))
                entry["attempts"] += 1
                if attempt.get("status") == "fail":
                    entry["failures"] += 1
                    error_type = str(attempt.get("error_type") or "")
                    if error_type == "timeout":
                        entry["timeouts"] += 1
                    if error_type in ("quota", "rate_limit"):
                        entry["quota"] += 1

        if kind == "budget.exceeded":
            budget_hits += 1

        if kind in ("recipe.draft_verify", "safe_writer.verify", "recipe.ladder"):
            if norm["status"] not in ("fail", "error", "timeout"):
                continue
            verify_failures += 1
            blob = " ".join([
                str(data.get("error") or ""),
                " ".join(str(line) for line in (data.get("output_tail") or [])),
                " ".join(str(line) for line in (data.get("history") or [])),
                str(data.get("output") or ""),
            ])
            label = _classify(blob) or "khac"
            patterns[label] = patterns.get(label, 0) + 1
            for _pattern, rule_label, rule_advice in ERROR_RULES:
                if rule_label == label:
                    advice[label] = rule_advice

        if kind == "recipe.done" and norm["status"] in ("fail", "error"):
            # run abort sau khi het vong: van tinh la mot lan verify that bai
            error = str(data.get("error") or "")
            label = _classify(error)
            if label:
                patterns[label] = patterns.get(label, 0) + 1
                for _pattern, rule_label, rule_advice in ERROR_RULES:
                    if rule_label == label:
                        advice[label] = rule_advice

    ranked = sorted(patterns.items(), key=lambda item: item[1], reverse=True)
    model_rows = sorted(models.values(),
                        key=lambda item: (item["failures"], item["attempts"]), reverse=True)
    suggestions = _suggestions(ranked, model_rows, verify_failures, budget_hits, cooldowns)

    return {
        "runs": len(runs),
        "events": len(events),
        "verify_failures": verify_failures,
        "error_patterns": [{"pattern": label, "count": count,
                            "advice": advice.get(label, "")} for label, count in ranked],
        "models": model_rows,
        "cooldowns": cooldowns,
        "budget_hits": budget_hits,
        "suggestions": suggestions,
    }


def _suggestions(ranked, model_rows, verify_failures, budget_hits, cooldowns) -> list[str]:
    tips: list[str] = []
    known = [(label, count) for label, count in ranked if label != "khac"]
    if verify_failures and known:
        label, count = known[0]
        share = round(100 * count / max(verify_failures, 1))
        advice = next((item[2] for item in ERROR_RULES if item[1] == label), "")
        if share >= 40:
            tips.append(f"{share}% loi verify la {label} ({count}/{verify_failures}) -> {advice}")
        else:
            tips.append(f"loi verify dang gap: {label} ({count} lan) -> {advice}")

    for row in model_rows:
        if row["timeouts"] >= 1:
            tips.append(f"model {row['model']} tung timeout {row['timeouts']} lan "
                        "-> tranh dung cho file lon, uu tien model nhanh hon")
        if row["quota"] >= 1:
            tips.append(f"model {row['model']} tung bi quota/429 {row['quota']} lan "
                        "-> de fallback tu chuyen, dung ep 1 model")

    if budget_hits >= 1:
        tips.append(f"da co {budget_hits} run cham tran ngan sach "
                    "-> giam max_rounds hoac chi dung 1 model cho task nho")

    for item in cooldowns:
        tips.append(f"model {item.get('model')} dang cooldown "
                    f"({item.get('error_type')}, con {item.get('cooldown_remaining_s')}s) "
                    "-> se tu bi bo qua")

    seen: set[str] = set()
    unique: list[str] = []
    for tip in tips:
        if tip not in seen:
            seen.add(tip)
            unique.append(tip)
    return unique


def analyze_health(path) -> list[dict]:
    """Doc bo nho suc khoe (Phase 3) de biet model nao dang bi chan."""
    target = Path(path)
    if not target.exists():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows: list[dict] = []
    for name, entry in (data.get("entries") or {}).items():
        if not isinstance(entry, dict):
            continue
        remaining = int(entry.get("cooldown_until", 0) or 0)
        remaining = max(0, remaining - int(time.time()))
        if remaining <= 0:
            continue
        rows.append({"model": name, "error_type": entry.get("last_error_type"),
                     "cooldown_remaining_s": remaining})
    return rows


def _ledger_path(root=None, ledger=None) -> Path:
    if ledger:
        return Path(ledger)
    base = Path(root or ".").resolve()
    if base.name == ".agents":
        return base / "ledger.jsonl"
    return base / ".agents" / "ledger.jsonl"


def get_suggestions(limit: int = 3, *, ledger=None, root=None) -> list[str]:
    """Loi khuyen ngan de chen vao prompt Coder. Chua du du lieu -> []."""
    path = _ledger_path(root=root, ledger=ledger)
    report = analyze(schema.read_events(path))
    report["cooldowns"] = analyze_health(Path(path).parent / "tmp" / "health.json")
    merged = _suggestions(
        [(item["pattern"], item["count"]) for item in report["error_patterns"]],
        report["models"], report["verify_failures"], report["budget_hits"],
        report["cooldowns"],
    )
    return merged[: max(0, limit)]


def render(report: dict) -> str:
    lines = [
        f"Memory: {report['runs']} run, {report['events']} event, "
        f"{report['verify_failures']} lan verify that bai",
        "",
        "Loi hay gap (do may that bat):",
    ]
    if report["error_patterns"]:
        for item in report["error_patterns"][:8]:
            lines.append(f"  {item['pattern']:<12} {item['count']:>3} lan   {item['advice']}")
    else:
        lines.append("  (chua co du lieu verify that bai)")

    lines += ["", "Model theo suc khoe:"]
    if report["models"]:
        for row in report["models"][:8]:
            lines.append(f"  {row['model']:<42} attempts={row['attempts']:<3} "
                         f"fail={row['failures']:<3} timeout={row['timeouts']:<3} "
                         f"quota={row['quota']}")
    else:
        lines.append("  (chua ghi nhan lan goi model nao)")

    lines += ["", "Goi y chen vao prompt Coder:"]
    if report["suggestions"]:
        for tip in report["suggestions"]:
            lines.append(f"  - {tip}")
    else:
        lines.append("  (chua du du lieu de ket luan)")
    return "\n".join(lines)


def build_parser():
    import argparse
    parser = argparse.ArgumentParser(description="Rut kinh nghiem tu ledger.")
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--root", default=".")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    path = _ledger_path(root=args.root, ledger=args.ledger)
    events = schema.read_events(path)
    report = analyze(events)
    report["cooldowns"] = analyze_health(Path(path).parent / "tmp" / "health.json")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
