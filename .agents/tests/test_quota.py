#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 5C - quota tracker + quota-aware routing.

    python .agents/tests/test_quota.py
    python .agents/tests/test_quota.py --fast   # bo qua hoi quy

DoD:
  1. cost_by_model gop dung cac variant + cost that.
  2. remaining/choose: model sap can bi loai, model free luon ok.
  3. router tu day escalate len khi main het dia.
  4. ca 2 het -> exhausted=True (khong am tham goi mu).
  5. khong hoi quy Phase 5.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import quota                          # noqa: E402
import router                         # noqa: E402
import work_order as work_order_module  # noqa: E402


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


def make_order(**overrides) -> work_order_module.WorkOrder:
    data = {
        "task_id": "Q1",
        "title": "quota",
        "objective": "Sua ham add trong calc.py de tra ve tong",
        "boundaries": "Chi sua calc.py",
        "input_context": "calc.py",
        "acceptance_criteria": "check.py exit 0",
        "verify_command": "python check.py",
        "target_files": ["calc.py"],
        "sandbox_files": ["calc.py", "check.py"],
        "level": 1,
        "max_rounds": 1,
    }
    data.update(overrides)
    return work_order_module.WorkOrder(**data)


class FakeQuota:
    """Quota gia: remaining_pct[model] = % con lai; ok[model] = con dung duoc."""

    available = True

    def __init__(self, remaining_pct: dict | None = None, ok: dict | None = None) -> None:
        self.remaining_pct = remaining_pct or {}
        self.ok_map = ok or {}

    def remaining(self, model: str) -> dict:
        remaining = float(self.remaining_pct.get(model, 100.0))
        ok = bool(self.ok_map.get(model, True))
        pct = 100.0 - remaining
        return {"model": model, "unlimited": False, "ok": ok,
                "windows": {"today": {"pct": pct}, "week": {"pct": pct},
                            "month": {"pct": pct}},
                "worst_pct": pct}


def test_cost_by_model(checks: Checks) -> None:
    stats = {"models": [
        {"model": {"providerID": "opencode-go", "id": "kimi-k3", "variant": "default"},
         "cost": 0.50},
        {"model": {"providerID": "opencode-go", "id": "kimi-k3", "variant": "high"},
         "cost": 0.25},
        {"model": {"providerID": "opencode", "id": "deepseek-v4.1-flash-free"},
         "cost": 0},
        {"model": {"providerID": "opencode-go", "id": "glm-5.3"}, "cost": 0.1},
    ]}
    costs = quota.cost_by_model(stats)
    checks.check("quota: gop variant cung model", costs["opencode-go/kimi-k3"] == 0.75,
                 costs)
    checks.check("quota: free cost 0", costs["opencode/deepseek-v4.1-flash-free"] == 0)
    checks.check("quota: doc stats rong", quota.cost_by_model(None) == {})


def test_remaining(checks: Checks) -> None:
    tracker = quota.QuotaTracker(caps={"opencode-go/kimi-k3": 15.0})
    tracker.available = True
    tracker.usage = {"today": {"opencode-go/kimi-k3": 3.2}, "week": {}, "month": {}}
    entry = tracker.remaining("opencode-go/kimi-k3")
    checks.check("quota: 5h qua 20% -> het dia", entry["ok"] is False, entry)
    checks.check("quota: pct 5h > 100", entry["windows"]["today"]["pct"] > 100, entry)

    tracker.usage = {"today": {"opencode-go/kimi-k3": 1.0}, "week": {}, "month": {}}
    checks.check("quota: 5h con du -> ok", tracker.remaining("opencode-go/kimi-k3")["ok"])

    free = tracker.remaining("opencode/deepseek-v4.1-flash-free")
    checks.check("quota: free vo han", free["unlimited"] and free["ok"], free)

    tracker.usage = {"today": {"opencode-go/kimi-k3": 9.0}, "week": {}, "month": {}}
    chosen, info = tracker.choose(["opencode-go/kimi-k3"], refresh=False)
    checks.check("quota: choose loai model het", chosen is None, info)


def test_router_quota(checks: Checks) -> None:
    order = make_order(level=2)          # W2: deepseek-v4-pro -> kimi-k2.7-code
    main = router.ROUTES["W2"]["main"]
    escalate = router.ROUTES["W2"]["escalate"]
    root = Path(tempfile.gettempdir()) / "opencode" / "router_quota_test"
    shutil.rmtree(root, ignore_errors=True)
    (root / ".agents").mkdir(parents=True)
    free = "opencode/deepseek-v4.1-flash-free"
    (root / ".agents" / "config.json").write_text(json.dumps({
        "models": {free: {"is_free": True, "auth_status": "available",
                          "provider": "opencode"}},
        "roles": {"coder": {"chain": [main, free]}},
    }), encoding="utf-8")
    checks.check("router-quota: fallback from config",
                 router.fallback_models(root) == [free],
                 router.fallback_models(root))
    empty = root / "empty"
    empty.mkdir()
    checks.check("router-quota: no config -> no fallback",
                 router.fallback_models(empty) == [], router.fallback_models(empty))

    plan = router.route(order, quota=FakeQuota(), root=root)
    checks.check("router-quota: du dia -> giu main", plan["chain"][0] == main, plan)
    checks.check("router-quota: mode main", plan["mode"] == "main", plan["mode"])

    plan = router.route(order, quota=FakeQuota({main: 5}), root=root)
    checks.check("router-quota: main can + escalate doi dao -> escalate",
                 plan["chain"][0] == escalate and plan["mode"] == "escalate", plan)

    plan = router.route(order, quota=FakeQuota({main: 5, escalate: 20}), root=root)
    checks.check("router-quota: main can + escalate khong doi dao -> fallback",
                 plan["chain"][0] == free and plan["mode"] == "fallback", plan)
    checks.check("router-quota: bao degraded", plan["degraded"] is True, plan)

    plan = router.route(order, quota=FakeQuota({main: 5, escalate: 20}),
                        allow_escalate=True, root=root)
    checks.check("router-quota: allow_escalate -> nang len",
                 plan["chain"][0] == escalate and plan["mode"] == "escalate", plan)

    plan = router.route(order, quota=FakeQuota({main: 11}), root=root)
    checks.check("router-quota: main 11% van dung duoc", plan["mode"] == "main", plan)
    plan = router.route(order, quota=FakeQuota({main: 9}), root=root)
    checks.check("router-quota: main 9% bi coi la can", plan["mode"] != "main", plan)

    all_off = {main: 0, escalate: 0, free: 0}
    plan = router.route(order, quota=FakeQuota(ok=all_off), root=root)
    checks.check("router-quota: het ca main/escalate/free -> exhausted",
                 plan["exhausted"] is True and plan["mode"] == "exhausted", plan)

    plan = router.route(order, quota=FakeQuota(ok={main: True, escalate: False,
                                                   free: True}), root=root)
    checks.check("router-quota: escalate tat nhung free con -> van chay main",
                 plan["chain"][0] == main, plan)

    plan = router.route(order, root=root)
    checks.check("router-quota: khong truyen quota -> khong loc",
                 plan["chain"] == [main, escalate] and plan["quota"] == {}, plan)
    shutil.rmtree(root, ignore_errors=True)


def test_shared_budget(checks: Checks) -> None:
    tracker = quota.QuotaTracker(caps={"opencode-go/a": 15.0}, plan_cap=1.0)
    tracker.available = True
    tracker.usage = {"today": {}, "week": {},
                     "month": {"opencode-go/a": 0.6, "opencode-go/b": 0.5}}
    entry = tracker.remaining("opencode-go/a")
    checks.check("shared: per-model con nhung plan can -> khong ok",
                 entry["model_ok"] and not entry["shared_ok"] and not entry["ok"], entry)
    checks.check("shared: pct thang > 100", entry["shared"]["windows"]["month"]["pct"] > 100,
                 entry["shared"])

    free = tracker.remaining("opencode/deepseek-v4.1-flash-free")
    checks.check("shared: free van ok khi plan can", free["unlimited"] and free["ok"], free)

    tracker.usage = {"today": {}, "week": {"opencode/deepseek-v4.1-flash-free": 99.0},
                     "month": {}}
    checks.check("shared: usage free khong tinh vao plan",
                 tracker.shared()["ok"] is True, tracker.shared())

    tracker.meters = {
        "today": {"used": 0.24, "limit": 12.0, "remaining": 11.76, "pct": 2.0,
                  "resets_at_ms": 4102444800000},
        "week": {"used": 8.4, "limit": 30.0, "remaining": 21.6, "pct": 28.0,
                 "resets_at_ms": 4102444800000},
        "month": {"used": 8.4, "limit": 60.0, "remaining": 51.6, "pct": 14.0,
                  "resets_at_ms": 4102444800000},
    }
    tracker.console = True
    shared = tracker.shared()
    checks.check("shared: dung meters that tu console",
                 shared["source"] == "console"
                 and shared["windows"]["week"]["pct"] == 28.0
                 and shared["cap"] == 60.0, shared)
    snapshot = tracker.snapshot()
    checks.check("shared: snapshot co meters + reset that",
                 snapshot["console"] is True
                 and snapshot["meters"]["month"]["limit"] == 60.0
                 and snapshot["reset"]["month"] == 4102444800000,
                 snapshot["reset"])


def test_no_regression(checks: Checks, *, fast: bool = False) -> None:
    if fast:
        print("[skip] hoi quy Phase 5 (--fast)")
        return
    started = subprocess.run([sys.executable, str(AGENTS_DIR / "tests" / "test_phase5.py"),
                              "--fast"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=900)
    checks.check("khong hoi quy: Phase 5", started.returncode == 0,
                 (started.stdout or "")[-300:])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Phase 5C quota tests.")
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args(argv)

    checks = Checks()
    print("[1/5] cost_by_model ...")
    test_cost_by_model(checks)
    print("[2/5] remaining/choose ...")
    test_remaining(checks)
    print("[3/5] shared budget ...")
    test_shared_budget(checks)
    print("[4/5] router + quota ...")
    test_router_quota(checks)
    print("[5/5] khong hoi quy ...")
    test_no_regression(checks, fast=args.fast)

    print("")
    print(f"QUOTA: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    sys.exit(main())
