#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scout catalog tests: free routes, dead proxies, snapshot drift.

    python .agents/tests/test_scout_catalog.py

The three failures these lock down:

1. A free model was dropped because its provider had no stored API key, even
   though free models answer without one.  That removed the only working routes
   in the whole system.
2. A local proxy that is down was still selected as a role model, because the
   scouted catalog kept saying "available".
3. The builtin snapshot drifted from the live catalog (34/52 models gone), so a
   CLI outage would write a config full of dead model IDs.
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

import scout  # noqa: E402


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


class patched:
    """Temporarily replace module attributes, restore on exit."""

    def __init__(self, module, **values) -> None:
        self.module = module
        self.values = values
        self.saved: dict[str, object] = {}

    def __enter__(self):
        for name, value in self.values.items():
            self.saved[name] = getattr(self.module, name)
            setattr(self.module, name, value)
        return self

    def __exit__(self, *exc) -> bool:
        for name, value in self.saved.items():
            setattr(self.module, name, value)
        return False


def no_auth() -> dict:
    return {"available": False, "has_console_auth": False,
            "has_go_auth": False, "raw": ""}


def no_local_providers(root) -> dict:
    return {"providers": {}, "files": []}


def proxy_configured(root) -> dict:
    return {"providers": {"9router": {"has_api_key": True,
                                      "base_url": "http://127.0.0.1:20128/v1"}},
            "files": []}


def write_catalog(directory: str, models: list[str]) -> Path:
    path = Path(directory) / "catalog.json"
    path.write_text(json.dumps({"models": models}), encoding="utf-8")
    return path


def collect(directory: str, models: list[str], *, proxy_up: bool,
            include_unconfigured: bool = False,
            proxy_in_config: bool = True) -> dict:
    catalog = write_catalog(directory, models)
    local = proxy_configured if proxy_in_config else no_local_providers
    with patched(scout,
                 read_auth_status=no_auth,
                 read_opencode_configs=local,
                 probe_local_providers=lambda **kw: {"9router": proxy_up}):
        return scout.collect_catalog(Path(directory), str(catalog),
                                     use_cli=False,
                                     include_unconfigured=include_unconfigured)


def test_free_model_survives_unconfigured(checks: Checks) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = collect(tmp, ["opencode/alpha-free", "opencode/paid-b"],
                         proxy_up=True, proxy_in_config=False)
    ids = [item["id"] for item in result["models"]]
    checks.check("free model kept when provider has no key",
                 ids == ["opencode/alpha-free"], ids)
    entry = result["models"][0] if result["models"] else {}
    checks.check("free model marked available",
                 entry.get("auth_status") == "available", entry.get("auth_status"))
    checks.check("free model carries auth_note",
                 "free model" in str(entry.get("auth_note", "")), entry.get("auth_note"))
    checks.check("paid model still dropped",
                 result["stats"]["dropped_unconfigured"] == 1,
                 result["stats"])


def test_paid_model_kept_when_unconfigured_allowed(checks: Checks) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = collect(tmp, ["opencode/paid-b"], proxy_up=True,
                         include_unconfigured=True, proxy_in_config=False)
    checks.check("--include-unconfigured keeps paid model",
                 [m["id"] for m in result["models"]] == ["opencode/paid-b"],
                 result["models"])


def test_dead_proxy_excluded_from_roles(checks: Checks) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = collect(tmp, ["9router/any-model", "opencode/alpha-free"],
                         proxy_up=False)
    ids = [item["id"] for item in result["models"]]
    checks.check("dead proxy model excluded", ids == ["opencode/alpha-free"], ids)
    checks.check("dead proxy counted as unavailable",
                 result["stats"]["dropped_unavailable"] == 1, result["stats"])
    checks.check("dead proxy warning present",
                 any("khong phan hoi" in w for w in result["warnings"]),
                 result["warnings"])
    checks.check("dead proxy provider marked unavailable",
                 result["providers"].get("9router") == "unavailable",
                 result["providers"])


def test_live_proxy_kept(checks: Checks) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = collect(tmp, ["9router/any-model"], proxy_up=True)
    checks.check("reachable proxy model kept",
                 [m["id"] for m in result["models"]] == ["9router/any-model"],
                 result["models"])
    checks.check("reachable proxy not marked unavailable",
                 result["providers"].get("9router") != "unavailable",
                 result["providers"])


def test_probe_tcp(checks: Checks) -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    checks.check("probe closes on unused port",
                 scout.probe_tcp("127.0.0.1", port, timeout=0.2) is False)
    probe.listen(1)
    try:
        checks.check("probe sees a listening port",
                     scout.probe_tcp("127.0.0.1", port, timeout=0.5) is True)
    finally:
        probe.close()


def test_snapshot_is_sane(checks: Checks) -> None:
    models = scout.SNAPSHOT_MODELS
    checks.check("snapshot is not empty", len(models) > 10, len(models))
    checks.check("snapshot has no duplicates",
                 len(models) == len(set(models)),
                 [m for m in models if models.count(m) > 1][:5])
    age = scout.config_age_days({"scouted_at": scout.SNAPSHOT_DATE})
    checks.check("snapshot date parses", age is not None, scout.SNAPSHOT_DATE)
    if age is not None:
        checks.check("snapshot younger than 45 days", age <= 45,
                     f"{age:.0f} ngay tu {scout.SNAPSHOT_DATE}")


def test_snapshot_matches_live(checks: Checks) -> None:
    live = scout.read_catalog_from_cli()
    if not live.get("ok"):
        print("    (bo qua: khong doc duoc live catalog)")
        return
    live_ids = set(live["models"])
    missing = [m for m in scout.SNAPSHOT_MODELS if m not in live_ids]
    checks.check("every snapshot model still exists live",
                 not missing, missing[:8])


def main() -> int:
    checks = Checks()
    for name, test in (
        ("free/unconfigured", test_free_model_survives_unconfigured),
        ("paid/unconfigured", test_paid_model_kept_when_unconfigured_allowed),
        ("dead proxy", test_dead_proxy_excluded_from_roles),
        ("live proxy", test_live_proxy_kept),
        ("tcp probe", test_probe_tcp),
        ("snapshot sane", test_snapshot_is_sane),
        ("snapshot vs live", test_snapshot_matches_live),
    ):
        print(f"[{name}] ...")
        test(checks)

    failed = len(checks.failures)
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    print(f"\nSCOUT: {checks.total - failed}/{checks.total} PASS")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
