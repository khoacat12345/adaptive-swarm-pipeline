#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0-03 / P1-08: normalized model capability registry."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

import model_registry  # noqa: E402
import secret_scan  # noqa: E402

ROOT = Path(tempfile.gettempdir()) / "opencode" / "model_registry_tests"


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


def make_root() -> Path:
    shutil.rmtree(ROOT, ignore_errors=True)
    (ROOT / ".agents").mkdir(parents=True)
    config = {
        "models": {
            "free/coder": {
                "id": "free/coder", "provider": "free", "family": "free",
                "auth_status": "available", "is_free": True, "cost_tier": "free",
                "context_window": 128000,
                "pricing": None,
                "capabilities": {"tooling": 0.9},
            },
            "paid/model": {
                "id": "paid/model", "provider": "paid", "family": "paid",
                "auth_status": "available", "is_free": False, "cost_tier": "cheap",
                "context_window": 64000,
                "pricing": {"input": 1.0, "output": 2.0, "cache_read": 0.1},
                "capabilities": {"tooling": 0.1},
            },
        }
    }
    (ROOT / ".agents" / "config.json").write_text(
        json.dumps(config), encoding="utf-8")
    (ROOT / ".agents" / "tmp").mkdir(parents=True)
    (ROOT / ".agents" / "tmp" / "health.json").write_text(
        json.dumps({"entries": {"free/coder": {"last_status": "ok",
                                               "successes": 3}}}),
        encoding="utf-8",
    )
    return ROOT


def main() -> int:
    checks = Checks()
    root = make_root()
    models = model_registry.load(root)
    checks.check("registry: loads both models", len(models) == 2, models)
    free = models["free/coder"]
    checks.check("registry: free pricing zeroed", free.pricing == {
        "input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0,
    }, free.as_dict())
    checks.check("registry: tool support", free.supports_tools is True, free.as_dict())
    paid = models["paid/model"]
    checks.check("registry: paid tool support false",
                 paid.supports_tools is False, paid.as_dict())
    checks.check("registry: health merged",
                 free.health.get("successes") == 3, free.as_dict())
    checks.check("registry: free usable", model_registry.usable(root, "free/coder"))
    checks.check("registry: paid blocked by free-only",
                 not model_registry.usable(root, "paid/model", free_only=True))
    checks.check("registry: tool requirement blocks paid",
                 not model_registry.usable(root, "paid/model", require_tools=True))
    cost = model_registry.estimate_cost(
        root, "paid/model", input_tokens=1_000_000, output_tokens=500_000)
    checks.check("registry: estimated cost", cost == 2.0, cost)
    checks.check("registry: free cost zero",
                 model_registry.estimate_cost(root, "free/coder",
                                              input_tokens=10_000) == 0.0)
    report = model_registry.report(root)
    checks.check("registry: report count", report["count"] == 2, report)
    checks.check("registry: report free count", report["free"] == 1, report)

    partial = model_registry.normalize(
        "opencode/deepseek-v4.1-flash-free",
        {"id": "opencode/deepseek-v4.1-flash-free"},
        {},
    )
    checks.check("registry: offline fallback context",
                 partial.context_window == 128000, partial.as_dict())
    checks.check("registry: offline fallback tools", partial.supports_tools is True,
                 partial.as_dict())
    checks.check("registry: offline fallback json", partial.json_mode is True,
                 partial.as_dict())
    checks.check("registry: fallback flag", partial.fallback_metadata is True,
                 partial.as_dict())
    checks.check("registry: fallback family", partial.family == "deepseek",
                 partial.as_dict())

    clean, findings = secret_scan.scan_text(
        "token='sk-ant-abcdefghijklmnopqrstuvwx' and password='hunter2hunter2'"
    )
    checks.check("security: secret is redacted",
                 "sk-ant-" not in clean and "hunter2hunter2" not in clean, clean)
    checks.check("security: redaction findings exist", len(findings) >= 1, findings)
    checks.check("security: redaction never logs raw value",
                 all("sk-ant-abcdefghijklmnopqrstuvwx" not in str(item)
                     for item in findings), findings)
    print(f"MODEL REGISTRY: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
