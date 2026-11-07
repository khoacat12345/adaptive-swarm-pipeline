#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-08: context-window enforcement and trimming."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

import context_guard  # noqa: E402

ROOT = Path(tempfile.gettempdir()) / "opencode" / "context_guard_tests"


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
    shutil.rmtree(ROOT, ignore_errors=True)
    (ROOT / ".agents").mkdir(parents=True)
    (ROOT / ".agents" / "config.json").write_text(json.dumps({
        "models": {
            "mock/small": {
                "id": "mock/small", "provider": "mock", "family": "small",
                "auth_status": "available", "is_free": True,
                "cost_tier": "free", "context_window": 1000,
                "pricing": None, "capabilities": {"tooling": 0.9},
            },
            "mock/large": {
                "id": "mock/large", "provider": "mock", "family": "large",
                "auth_status": "available", "is_free": True,
                "cost_tier": "free", "context_window": 100000,
                "pricing": None, "capabilities": {"tooling": 0.9},
            },
        }
    }), encoding="utf-8")
    checks = Checks()
    short = context_guard.fit_prompt("hello", "mock/small", root=ROOT)
    checks.check("context: short prompt unchanged", short.trimmed is False,
                 short.as_dict())
    long_text = ("line of code\n" * 5000)
    fitted = context_guard.fit_prompt(long_text, "mock/small", root=ROOT)
    checks.check("context: long prompt trimmed", fitted.trimmed is True,
                 fitted.as_dict())
    checks.check("context: final tokens under window",
                 fitted.final_tokens <= 1000, fitted.as_dict())
    checks.check("context: trim marker visible",
                 "context trimmed" in fitted.text, fitted.text[:120])
    large = context_guard.fit_prompt(long_text, "mock/large", root=ROOT)
    checks.check("context: large window keeps prompt", large.trimmed is False,
                 large.as_dict())
    unknown = context_guard.fit_prompt(long_text, "unknown/model", root=ROOT)
    checks.check("context: unknown window fails open", unknown.trimmed is False,
                 unknown.as_dict())
    checks.check("context: token estimate sane",
                 context_guard.estimate_tokens("abcd" * 100) == 100,
                 context_guard.estimate_tokens("abcd" * 100))
    print(f"CONTEXT GUARD: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
