#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 5.1 - catalog-aware model selection (no OpenCode Go assumption)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import router  # noqa: E402
import work_order as work_order_module  # noqa: E402

PY = f'"{sys.executable}"'


class Checks:
    def __init__(self) -> None:
        self.total = 0
        self.failures: list[str] = []

    def check(self, name, condition, detail=""):
        self.total += 1
        if not bool(condition):
            self.failures.append(f"{name}: {detail}" if detail else name)

    @property
    def passed(self):
        return self.total - len(self.failures)


def order(level=2):
    return work_order_module.create(
        task_id="SEL1", title="selector", objective="Sua logic calc.py",
        boundaries="Chi sua calc.py", input_context="calc.py",
        acceptance_criteria="check.py exit 0", verify_command=f"{PY} check.py",
        target_files=["calc.py"], sandbox_files=["calc.py", "check.py"],
        level=level, max_rounds=1,
    )


def write_catalog(root: Path) -> None:
    (root / ".agents").mkdir(parents=True, exist_ok=True)
    models = {
        "local/alpha": {"family": "alpha", "is_free": True, "cost_tier": "free",
                         "capabilities": {"code": 0.9, "reasoning": 0.7,
                                          "long_ctx": 0.6, "vision": 0.1, "speed": 0.8}},
        "local/beta": {"family": "beta", "is_free": True, "cost_tier": "free",
                        "capabilities": {"code": 0.8, "reasoning": 0.8,
                                         "long_ctx": 0.7, "vision": 0.1, "speed": 0.7}},
    }
    roles = {role: {"chain": ["local/alpha", "local/beta"], "why": "local test"}
             for role in ("coder", "reviewer", "security", "architect", "test_writer")}
    (root / ".agents" / "config.json").write_text(json.dumps(
        {"models": models, "roles": roles}), encoding="utf-8")


def write_expensive_catalog(root: Path) -> None:
    (root / ".agents").mkdir(parents=True, exist_ok=True)
    models = {
        "free/coder": {"family": "freecoder", "is_free": True, "cost_tier": "free",
                        "capabilities": {"code": 0.80, "reasoning": 0.75, "long_ctx": 0.8}},
        "paid/qwen-max": {"family": "qwen", "is_free": False, "cost_tier": "high",
                           "capabilities": {"code": 0.99, "reasoning": 0.98, "long_ctx": 0.95}},
        "paid/kimi": {"family": "kimi", "is_free": False, "cost_tier": "high",
                       "capabilities": {"code": 0.98, "reasoning": 0.97, "long_ctx": 0.92}},
    }
    roles = {role: {"chain": ["paid/qwen-max", "paid/kimi", "free/coder"]}
             for role in ("coder", "reviewer", "security", "architect", "test_writer")}
    (root / ".agents" / "config.json").write_text(json.dumps(
        {"models": models, "roles": roles,
         "constraints": {"free_only": False}}), encoding="utf-8")


def main() -> int:
    checks = Checks()
    root = Path(tempfile.gettempdir()) / "opencode" / "selector_tests"
    shutil.rmtree(root, ignore_errors=True)
    write_catalog(root)
    plan = router.route(order(), root=root, use_catalog=True)
    checks.check("selector: W2 from catalog", plan.get("catalog_source") == "catalog", plan)
    checks.check("selector: no OpenCode Go required",
                 all("opencode-go" not in item for item in plan.get("chain") or []), plan)
    checks.check("selector: main local", plan.get("main") == "local/alpha", plan)
    checks.check("selector: escalate different family", plan.get("escalate") == "local/beta", plan)
    checks.check("selector: known family chain",
                 plan.get("family") == ["alpha", "beta"], plan)

    empty = root / "empty"
    empty.mkdir()
    fallback = router.route(order(), root=empty, use_catalog=True)
    checks.check("selector: catalog missing -> static", fallback.get("catalog_source") == "static",
                 fallback)
    checks.check("selector: static remains valid", bool(fallback.get("chain")), fallback)

    expensive = root / "expensive"
    write_expensive_catalog(expensive)
    w1 = router.route(order(level=1), root=expensive, use_catalog=True)
    checks.check("selector: W1 never picks paid when free exists",
                 w1.get("main") == "free/coder", w1)
    checks.check("selector: W1 chain stays within free",
                 all("paid/" not in item for item in w1.get("chain") or []), w1)
    w2 = router.route(order(level=2), root=expensive, use_catalog=True)
    checks.check("selector: complex W2 may use stronger paid model",
                 "paid/" in str(w2.get("main")), w2)

    print(f"MODEL SELECTOR: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
