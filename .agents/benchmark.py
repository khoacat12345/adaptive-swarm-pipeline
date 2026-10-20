#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Benchmark chon model theo domain (Phase 5B).

Chay bo test KHO (tests/hard_tasks.py) cho tung model, gom theo domain, cham
diem tong hop 3 tieu chi:
  - do chinh xac (pass rate)
  - toc do (trung binh giay)
  - hieu qua quota (cost/pass -> so task lam duoc trong han muc $ thang)
Sau do xuat ra MAIN + ESCALATE cho moi domain (escalate bat buoc KHAC HO main).

Cach dung:
    python .agents/benchmark.py --models "opencode/deepseek-v4.1-flash-free,opencode/glm-5.3-flash-free"
    python .agents/benchmark.py --domains backend,security --budget 900
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import eval_suite            # noqa: E402
import hard_tasks            # noqa: E402

# Gia $ / 1M token (in, out) va han muc $/thang cua tung model (OpenCode Go).
# Free: in/out = 0, monthly = 0 (khong tieu quota -> cost = 0 -> quota score = 1).
PRICING = {
    "opencode-go/kimi-k3":            {"in": 3.00, "out": 15.00, "monthly": 15},
    "opencode-go/qwen3.8-max":        {"in": 2.00, "out": 6.00,  "monthly": 15},
    "opencode-go/glm-5.3":            {"in": 1.40, "out": 4.40,  "monthly": 15},
    "opencode-go/kimi-k2.7-code":     {"in": 0.95, "out": 4.00,  "monthly": 60},
    "opencode-go/deepseek-v4-pro":    {"in": 0.66, "out": 1.98,  "monthly": 15},
    "opencode-go/minimax-m3":         {"in": 0.30, "out": 1.20,  "monthly": 60},
    "opencode-go/claude-haiku-5-5":   {"in": 0.10, "out": 0.50,  "monthly": 15},
    "opencode-go/gpt-6-luna":         {"in": 0.10, "out": 0.50,  "monthly": 15},
    "opencode/deepseek-v4.1-flash-free": {"in": 0.0, "out": 0.0,  "monthly": 0},
    "opencode/glm-5.3-flash-free":        {"in": 0.0, "out": 0.0,  "monthly": 0},
}
FAMILY = {
    "opencode-go/kimi-k3": "kimi",
    "opencode-go/qwen3.8-max": "qwen",
    "opencode-go/glm-5.3": "glm",
    "opencode-go/kimi-k2.7-code": "kimi",
    "opencode-go/deepseek-v4-pro": "deepseek",
    "opencode-go/minimax-m3": "minimax",
    "opencode-go/claude-haiku-5-5": "anthropic",
    "opencode-go/gpt-6-luna": "openai",
    "opencode/deepseek-v4.1-flash-free": "deepseek",
    "opencode/glm-5.3-flash-free": "glm",
}


def tokens(record: dict, key: str) -> int:
    value = record.get(key) or 0
    return int(value or 0)


def cost_of(record: dict) -> float:
    """Chi phi $ cua 1 record: dung cost tu CLI neu co, khong thi uoc tu token."""
    reported = record.get("cost") or 0.0
    if reported:
        return float(reported)
    price = PRICING.get(record.get("model"), {"in": 0.0, "out": 0.0})
    return (tokens(record, "tokens_in") / 1e6 * price["in"]
            + tokens(record, "tokens_out") / 1e6 * price["out"])


def cost_per_pass(records: list[dict]) -> float:
    """Chi phi $ trung binh cho moi lan PASS trong nhom (0 neu free)."""
    spent = sum(cost_of(record) for record in records)
    passes = sum(1 for record in records if record.get("pass"))
    if not passes:
        return float("inf")
    return spent / passes


def score(records: list[dict]) -> dict:
    total = len(records)
    if not total:
        return {"acc": 0.0, "speed": 0.0, "quota": 0.0, "composite": 0.0,
                "pass": 0, "total": 0}
    passes = sum(1 for r in records if r.get("pass"))
    acc = passes / total
    avg_s = sum(r.get("seconds", 0) or 0 for r in records) / total
    speed = max(0.0, 1.0 - avg_s / 240.0)

    cpp = cost_per_pass(records)
    if cpp == 0 or cpp == float("inf"):
        quota = 1.0 if cpp == 0 else 0.0
    else:
        monthly = float(PRICING.get(records[0]["model"], {}).get("monthly", 15.0) or 0.0)
        if monthly <= 0:
            quota = 1.0
        else:
            import math
            capacity = monthly / cpp
            quota = max(0.0, min(1.0, math.log10(capacity + 1) / 5.0))
    composite = 0.5 * acc + 0.25 * speed + 0.25 * quota
    return {"acc": round(acc, 3), "speed": round(speed, 3), "quota": round(quota, 3),
            "composite": round(composite, 3), "pass": passes, "total": total,
            "avg_s": round(avg_s, 1), "cost_per_pass": None if cpp == float("inf") else round(cpp, 5)}


def rank_domains(records: list[dict]) -> dict:
    models = sorted({r["model"] for r in records})
    domains = sorted({hard_tasks_by_id[r["task"]]["domain"] for r in records
                      if r["task"] in hard_tasks_by_id})
    out: dict[str, dict] = {}
    for domain in domains:
        rows = {m: [] for m in models}
        for r in records:
            if hard_tasks_by_id.get(r["task"], {}).get("domain") == domain:
                rows[r["model"]].append(r)
        scored = {m: score(rows[m]) for m in rows if rows[m]}
        ranked = sorted(scored.items(), key=lambda kv: (-kv[1]["composite"], -kv[1]["acc"]))
        main = ranked[0][0] if ranked else None
        main_family = FAMILY.get(main, "?")
        escalate = next((m for m, _ in ranked if FAMILY.get(m, "?") != main_family), None)
        out[domain] = {"main": main, "main_family": main_family, "escalate": escalate,
                       "ranking": ranked}
    return out


def run(models: list[str], tasks: list[dict], *, budget: int, out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    records: list[dict] = []
    TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode"
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix="bench_", dir=str(TEMP_ROOT)))
    deadline = time.time() + budget
    with results_path.open("a", encoding="utf-8") as sink:
        for model in models:
            print(f"[{model}]")
            for task in tasks:
                if time.time() > deadline:
                    print("  -- het ngan sach thoi gian --")
                    break
                record = eval_suite.run_case(task, model, timeout=240,
                                             work_root=work_root, debug_dir=None,
                                             verbose=True)
                record["domain"] = task.get("domain")
                records.append(record)
                sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                sink.flush()
                if record["kind"] in eval_suite.STOP_KINDS:
                    print(f"  -- model khong dung duoc ({record['kind']}), bo qua --")
                    break
            if time.time() > deadline:
                break
    import shutil
    shutil.rmtree(work_root, ignore_errors=True)
    return records


def render(records: list[dict]) -> str:
    if not records:
        return "khong co ket qua"
    models = sorted({r["model"] for r in records})
    lines = []
    domains = rank_domains(records)
    for domain, entry in domains.items():
        lines.append(f"== {domain} ==")
        for model, sc in entry["ranking"]:
            mark = "MAIN " if model == entry["main"] else ("ESC  " if model == entry["escalate"] else "     ")
            lines.append(
                f"  {mark}{model[:44]:<44} acc={sc['acc']:.0%} "
                f"speed={sc['speed']:.2f} quota={sc['quota']:.2f} "
                f"comp={sc['composite']:.2f} ({sc['pass']}/{sc['total']})")
        lines.append(f"  -> main={entry['main']}  escalate={entry['escalate']}")
        lines.append("")
    return "\n".join(lines)


hard_tasks_by_id = {t["id"]: t for t in hard_tasks.HARD_TASKS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark model theo domain.")
    parser.add_argument("--models", required=True, help="model cach nhau bang dau phay")
    parser.add_argument("--domains", default=None, help="vd: backend,security (mac dinh: het)")
    parser.add_argument("--budget", type=int, default=1800, help="gioi han tong giay")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    tasks = list(hard_tasks.HARD_TASKS)
    if args.domains:
        wanted = {d.strip() for d in args.domains.split(",") if d.strip()}
        tasks = [t for t in tasks if t.get("domain") in wanted]

    out_dir = Path(args.out) if args.out else (
        HERE / "eval" / "bench" / time.strftime("%Y%m%d-%H%M%S"))
    records = run(models, tasks, budget=args.budget, out_dir=out_dir)
    print(render(records))
    (out_dir / "report.json").write_text(
        json.dumps({"domains": rank_domains(records), "results": records},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nbao cao: {out_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
