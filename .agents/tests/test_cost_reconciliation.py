#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0-02 / P0-04: actual spend reconciliation."""

from __future__ import annotations

import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

import spend_reconcile  # noqa: E402


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


def main() -> int:
    checks = Checks()
    before = spend_reconcile.from_mapping(
        {"cost_usd": 1.0, "requests": 10, "tokens_in": 100, "tokens_out": 50})
    after = spend_reconcile.from_mapping(
        {"cost_usd": 1.5, "requests": 13, "tokens_in": 400, "tokens_out": 150})
    result = spend_reconcile.reconcile(
        before, after, estimated_cost_usd=0.4, max_cost_usd=0.6)
    checks.check("spend: actual delta", result.actual_cost_usd == 0.5, result.as_dict())
    checks.check("spend: request delta", result.actual_requests == 3, result.as_dict())
    checks.check("spend: under cap", result.over_budget is False, result.as_dict())
    checks.check("spend: delta recorded", result.delta_cost_usd == 0.1,
                 result.as_dict())

    over = spend_reconcile.reconcile(
        before, after, estimated_cost_usd=0.1)
    checks.check("spend: over estimate", over.over_budget is True, over.as_dict())
    checks.check("spend: source preserved", over.source == "mapping", over.as_dict())

    # A missing provider source is visible, not silently reported as zero.
    unavailable = spend_reconcile.snapshot_from_quota("/definitely/not/a/project")
    checks.check("spend: unavailable source marked",
                 unavailable.source in {"unavailable", "opencode.db"},
                 unavailable.as_dict())
    print(f"COST RECONCILE: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
