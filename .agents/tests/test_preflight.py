#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Health & cost preflight gate tests.

    python .agents/tests/test_preflight.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR,):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import job  # noqa: E402
import preflight  # noqa: E402
import scout  # noqa: E402
import wave  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "preflight_tests"


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


def write_config(root: Path, *, provider_status="available",
                 auth_status="available", extra_models=None,
                 auth_raw="") -> None:
    agents = root / ".agents"
    agents.mkdir(parents=True, exist_ok=True)
    models = {
        "opencode/free-a": {
            "id": "opencode/free-a", "provider": "opencode",
            "auth_status": auth_status, "is_free": True,
        },
        "opencode/free-b": {
            "id": "opencode/free-b", "provider": "opencode",
            "auth_status": auth_status, "is_free": True,
        },
    }
    models.update(extra_models or {})
    config = {
        "models": models,
        "catalog": {"providers": {"opencode": provider_status}},
        "auth_raw": auth_raw,
    }
    (agents / "config.json").write_text(json.dumps(config), encoding="utf-8")


def test_missing_config(checks: Checks) -> None:
    root = TEMP_ROOT / "missing"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    result = preflight.check_models(root, ["opencode/free-a"])
    checks.check("preflight: missing config fails", not result["ok"], result)
    checks.check("preflight: missing config gives action",
                 "scout.py" in " ".join(result.get("errors") or []), result)


def test_pass_and_cache(checks: Checks) -> None:
    root = TEMP_ROOT / "pass"
    shutil.rmtree(root, ignore_errors=True)
    write_config(root)
    result = preflight.check_models(root, ["opencode/free-a", "opencode/free-b"])
    checks.check("preflight: two configured free models pass", result["ok"], result)
    checks.check("preflight: both model results present", len(result["results"]) == 2,
                 result["results"])
    second = preflight.check_models(root, ["opencode/free-a", "opencode/free-b"])
    checks.check("preflight: second call is cached", second.get("cached") is True, second)
    no_cache = preflight.check_models(root, ["opencode/free-a"],
                                      use_cache=False)
    checks.check("preflight: use_cache=False rechecks", no_cache.get("cached") is False,
                 no_cache)


def test_config_change_invalidates(checks: Checks) -> None:
    root = TEMP_ROOT / "invalidate"
    shutil.rmtree(root, ignore_errors=True)
    write_config(root)
    first = preflight.check_models(root, ["opencode/free-a"])
    checks.check("preflight: initial pass", first["ok"], first)

    write_config(root, provider_status="unconfigured")
    second = preflight.check_models(root, ["opencode/free-a"])
    checks.check("preflight: config change invalidates cache",
                 second.get("cached") is False, second)
    checks.check("preflight: unconfigured provider fails", not second["ok"], second)
    checks.check("preflight: unconfigured reason is explicit",
                 "unconfigured" in " ".join(second.get("errors") or []), second)


def test_model_auth_and_missing_model(checks: Checks) -> None:
    root = TEMP_ROOT / "auth"
    shutil.rmtree(root, ignore_errors=True)
    write_config(root, auth_status="unconfigured")
    result = preflight.check_models(root, ["opencode/free-a"])
    checks.check("preflight: model auth unconfigured fails", not result["ok"], result)
    checks.check("preflight: auth reason is explicit",
                 "model auth" in " ".join(result.get("errors") or []), result)

    write_config(root)
    missing = preflight.check_models(root, ["opencode/not-in-catalog"])
    checks.check("preflight: unknown model fails", not missing["ok"], missing)
    checks.check("preflight: unknown model reason",
                 "not in config catalog" in " ".join(missing.get("errors") or []),
                 missing)


def test_stale_cache(checks: Checks) -> None:
    root = TEMP_ROOT / "stale"
    shutil.rmtree(root, ignore_errors=True)
    write_config(root)
    result = preflight.check_models(root, ["opencode/free-a"])
    checks.check("preflight: setup pass", result["ok"], result)
    cache_path = root / ".agents" / "tmp" / "preflight.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    cache["checked_at"] = time.time() - (25 * 60 * 60)
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    stale = preflight.check_models(root, ["opencode/free-a"])
    checks.check("preflight: stale >24h is not trusted", stale.get("cached") is False,
                 stale)
    checks.check("preflight: stale >24h warns and rechecks", stale["ok"] and
                 "stale" in " ".join(stale.get("warnings") or []).lower(), stale)


def test_auth_status_from_opencode_auth_list(checks: Checks) -> None:
    raw = ("OpenCode Go       OpenCode Go                 stored\n"
           "OpenCode Console  Personal                    stored")
    flags = scout.parse_auth_flags(raw)
    checks.check("auth: OpenCode Go stored -> available", flags["has_go_auth"], flags)
    checks.check("auth: console stored -> available", flags["has_console_auth"], flags)
    only_go = scout.parse_auth_flags("OpenCode Go  OpenCode Go  stored")
    checks.check("auth: Go only does not imply Personal",
                 only_go["has_go_auth"] and not only_go["has_console_auth"], only_go)

    root = TEMP_ROOT / "auth-raw"
    shutil.rmtree(root, ignore_errors=True)
    write_config(root, provider_status="unconfigured",
                 auth_raw=("OpenCode Go  OpenCode Go  stored\n"
                           "OpenCode Console  Personal  stored"))
    result = preflight.check_models(root, ["opencode/free-a"])
    checks.check("preflight: stale catalog unconfigured is overridden by auth_raw",
                 result["ok"], result)


def test_chain(checks: Checks) -> None:
    root = TEMP_ROOT / "chain"
    shutil.rmtree(root, ignore_errors=True)
    write_config(root)
    good = preflight.check_chain(root, ["opencode/free-a", "opencode/not-in-catalog"])
    checks.check("preflight: chain passes with one available model", good["ok"], good)
    checks.check("preflight: broken fallback warns",
                 bool(good.get("warnings")), good)
    checks.check("preflight: available primary recorded",
                 good.get("available_models") == ["opencode/free-a"], good)
    bad = preflight.check_chain(root, ["opencode/not-in-catalog"])
    checks.check("preflight: empty chain fails", not bad["ok"], bad)


def test_job_preflight_aborts(checks: Checks) -> None:
    root = TEMP_ROOT / "job"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    plan = {
        "job_id": "J1", "objective": "preflight",
        "subtasks": [{
            "id": "S1", "title": "x", "objective": "fix add",
            "boundaries": "only calc.py", "input_context": "calc.py",
            "acceptance_criteria": "exit 0", "verify_command": "python check.py",
            "target_files": ["calc.py"], "sandbox_files": ["calc.py"],
            "level": 2, "max_rounds": 1, "depends_on": [],
        }],
    }
    old = os.environ.get("SWARM_WAVE_ROLES")
    os.environ["SWARM_WAVE_ROLES"] = "security=opencode/free-a"
    try:
        job.run_job(plan, root=root, mode="meeting", reviewer_kind="wave",
                    use_router=False)
        blocked = False
        error = ""
    except ValueError as exc:
        blocked = True
        error = str(exc)
    finally:
        if old is None:
            os.environ.pop("SWARM_WAVE_ROLES", None)
        else:
            os.environ["SWARM_WAVE_ROLES"] = old
    checks.check("preflight: job meeting aborts before run", blocked, error)
    checks.check("preflight: job names missing roles",
                 "qa" in error and "perf" in error and "docs" in error, error)


def test_roles_and_classification(checks: Checks) -> None:
    missing = preflight.missing_roles({"security": "m1"}, wave.ROLE_ORDER)
    checks.check("preflight: missing 3 reviewer roles",
                 missing == ["qa", "perf", "docs"], missing)
    checks.check("preflight: all roles present",
                 preflight.missing_roles(wave.normalize_roles(), wave.ROLE_ORDER) == [],
                 wave.normalize_roles())
    checks.check("preflight: provider.no-route is unavailable",
                 preflight.classify_error("provider.no-route") == "unavailable",
                 preflight.classify_error("provider.no-route"))
    checks.check("preflight: timeout is retryable",
                 preflight.classify_error("request timed out") == "timeout",
                 preflight.classify_error("request timed out"))
    checks.check("preflight: quota is retryable",
                 preflight.classify_error("429 rate limit") == "quota",
                 preflight.classify_error("429 rate limit"))
    checks.check("preflight: review reject stays reject",
                 preflight.classify_error("reviewer rejected: unsafe") == "reject",
                 preflight.classify_error("reviewer rejected: unsafe"))


def main() -> int:
    checks = Checks()
    for name, test in (
        ("missing config", test_missing_config),
        ("pass/cache", test_pass_and_cache),
        ("config invalidation", test_config_change_invalidates),
        ("auth/missing model", test_model_auth_and_missing_model),
        ("stale cache", test_stale_cache),
        ("chain", test_chain),
        ("auth list", test_auth_status_from_opencode_auth_list),
        ("job preflight", test_job_preflight_aborts),
        ("roles/classification", test_roles_and_classification),
    ):
        print(f"[{name}] ...")
        test(checks)

    print("")
    print(f"PREFLIGHT: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
