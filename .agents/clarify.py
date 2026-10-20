#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clarify Gate (Task 3.6).

Task mo ho nghiem trong (thieu file dich, thieu muc tieu cu the) phai bi chan
TRUOC khi goi model. Quy tac: gom TAT CA diem can lam ro vao DUNG MOT lan hoi,
khong hoi lat nhat nhieu lan trong mot task.

    problems = clarify.check(work_order, root=root)
    if problems:
        raise clarify.ClarifyRequired(problems)     # 1 exception = 1 cau hoi

`ClarifyRequired` ke thua `WorkOrderRejected` nen moi cho bat WorkOrderRejected
cu van hoat dong, chi khac phan hien thi (1 khoi duy nhat).
"""

from __future__ import annotations

from pathlib import Path

import work_order as work_order_module

MIN_CRITERIA_CHARS = 8

# Muc tieu chung chung: neu ngan va khop mau nay thi chua du de giao viec.
VAGUE_MARKERS = (
    "sua loi", "fix bug", "fix loi", "toi uu", "refactor", "lam lai",
    "cai thien", "improve", "sua code", "fix code", "lam cho dung",
)


class ClarifyRequired(work_order_module.WorkOrderRejected):
    """Work Order mo ho -> hoi dung 1 lan, khong chay task."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(problems)
        self.message = build_question(problems)
        self.args = (self.message,)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def check(work_order, root=None) -> list[str]:
    """Tra ve danh sach diem can lam ro (rong = du ro de chay)."""
    problems: list[str] = list(work_order.problems())

    objective = (getattr(work_order, "objective", "") or "").strip()
    lowered = objective.lower()
    # Chi chan khi NGAN *va* chung chung ("sua loi", "fix bug"). Mot muc tieu ngan
    # nhung tro dung vao viec cu the ("sua add") van du de giao viec.
    if (objective and len(objective) < 60
            and any(marker in lowered for marker in VAGUE_MARKERS)):
        problems.append(
            "muc tieu chung chung - can neu ro hanh vi sai va hanh vi dung")

    criteria = (getattr(work_order, "acceptance_criteria", "") or "").strip()
    if criteria and len(criteria) < MIN_CRITERIA_CHARS:
        problems.append("tieu chi nghiem thu qua ngan - can noi ro ket qua nao la dat")

    targets = list(getattr(work_order, "target_files", []) or [])
    if len(targets) != 1 and targets:
        problems.append(f"can dung 1 file dich cho moi task, nhan duoc {len(targets)}")

    if root is not None and targets:
        base = Path(root)
        for relative in targets:
            if not (base / relative).exists():
                problems.append(f"file dich khong ton tai: {relative}")

    return _dedupe(problems)


def build_question(problems: list[str]) -> str:
    """Mot khoi duy nhat, danh so, de nguoi dung tra loi 1 lan."""
    lines = ["CAN LAM RO TRUOC KHI CHAY (hoi 1 lan duy nhat):"]
    for index, problem in enumerate(problems, start=1):
        lines.append(f"  {index}. {problem}")
    lines.append("Hay bo sung Work Order theo cac diem tren roi chay lai.")
    return "\n".join(lines)


def ask(problems: list[str]) -> int:
    print(build_question(problems))
    return 1


def build_parser():
    import argparse
    parser = argparse.ArgumentParser(description="Kiem tra Work Order co du ro khong.")
    parser.add_argument("--order", required=True, help="file JSON chua Work Order")
    parser.add_argument("--root", default=".")
    return parser


def main(argv=None) -> int:
    import json
    args = build_parser().parse_args(argv)
    data = json.loads(Path(args.order).read_text(encoding="utf-8"))
    order = work_order_module.WorkOrder.from_dict(data)
    problems = check(order, root=args.root)
    if not problems:
        print("Work Order du ro.")
        return 0
    return ask(problems)


if __name__ == "__main__":
    raise SystemExit(main())
