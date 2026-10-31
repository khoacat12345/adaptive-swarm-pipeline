#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Health & cost preflight gate.

It is deliberately read-only: it never calls a model and never writes project
files.  It checks whether the configured model routes are actually usable
*before* recipe_c or job starts.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

CACHE_TTL_SECONDS = 15 * 60
STALE_SECONDS = 24 * 60 * 60
CATALOG_MAX_AGE_DAYS = 7
DEFAULT_9ROUTER_HOST = "127.0.0.1"
DEFAULT_9ROUTER_PORT = 20128


def _agents_dir(root) -> Path:
    base = Path(root or ".").resolve()
    return base if base.name == ".agents" else base / ".agents"


def _config_path(root) -> Path:
    return _agents_dir(root) / "config.json"


def _cache_path(root) -> Path:
    return _agents_dir(root) / "tmp" / "preflight.json"


def _health_path(root) -> Path:
    return _agents_dir(root) / "tmp" / "health.json"


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _model_list_hash(models: list[str]) -> str:
    return _hash_bytes("\n".join(sorted(set(models))).encode("utf-8"))


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def classify_error(error) -> str:
    """Classify a worker error without conflating it with a real reject.

    ``unavailable`` is an infrastructure/model-route failure; it must never be
    counted as a reviewer reject.  ``timeout`` and ``quota`` can be retried or
    moved to the next model by a fallback chain.
    """
    text = str(error or "").lower()
    if any(word in text for word in ("no-route", "no route", "model not found",
                                     "unknown model", "not configured",
                                     "provider unavailable", "unavailable model")):
        return "unavailable"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if any(word in text for word in ("quota", "rate limit", "429", "insufficient")):
        return "quota"
    if any(word in text for word in ("reject", "not approve", "issue found")):
        return "reject"
    return "error"


def missing_roles(roles: dict, required=("security", "qa", "perf", "docs")) -> list[str]:
    return [str(role) for role in required if not str((roles or {}).get(role) or "").strip()]


def _provider_ok(provider: str, config: dict, *, timeout: float = 0.4) -> tuple[bool, str]:
    provider = str(provider or "").lower()
    catalog = config.get("catalog") or {}
    status = str((catalog.get("providers") or {}).get(provider) or "").lower()
    # A stale catalog can say "unconfigured" while `opencode auth list` proves
    # the subscription is stored.  Do not let a stale derived field lock the
    # whole system; auth_raw is the source of truth for these two providers.
    auth_raw = str(config.get("auth_raw") or "").lower()
    stored = "stored" in auth_raw
    if status == "unconfigured" and provider == "opencode-go":
        if stored and "opencode go" in auth_raw:
            status = "available"
    if status == "unconfigured" and provider == "opencode":
        if stored and ("opencode console" in auth_raw or "personal" in auth_raw):
            status = "available"
    if status in ("unconfigured", "unavailable", "disabled", "missing"):
        return False, f"provider {provider} is {status}"

    # 9router is normally a local proxy.  A stale catalog says "available"
    # even when the proxy process is down, so probe it explicitly.
    if provider == "9router":
        host = os.environ.get("SWARM_9ROUTER_HOST", DEFAULT_9ROUTER_HOST)
        try:
            port = int(os.environ.get("SWARM_9ROUTER_PORT", DEFAULT_9ROUTER_PORT))
        except ValueError:
            port = DEFAULT_9ROUTER_PORT
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True, f"9router reachable at {host}:{port}"
        except OSError:
            return False, f"9router proxy unreachable at {host}:{port}"

    return True, status or "available"


def _health_ok(model: str, root) -> tuple[bool, str]:
    data = _load_json(_health_path(root))
    entry = (data.get("entries") or {}).get(model) or {}
    if not isinstance(entry, dict):
        return True, ""
    until = float(entry.get("cooldown_until") or 0)
    now = time.time()
    if until > now:
        return False, f"model in cooldown for {int(until - now)}s"
    return True, ""


def check_models(root, models, *, use_cache: bool = True, strict: bool = True,
                 require_all: bool = True) -> dict:
    """Check route/provider/health for the given model IDs.

    The function never calls an LLM.  It is intentionally cheap enough to run
    before every recipe/job invocation.
    """
    model_ids = [str(model) for model in models or [] if str(model).strip()]
    model_ids = list(dict.fromkeys(model_ids))
    config_path = _config_path(root)
    try:
        config_bytes = config_path.read_bytes()
    except OSError:
        return {
            "ok": False, "checked_at": time.time(), "models": model_ids,
            "results": {}, "errors": [f"missing {config_path}; run scout.py --write"],
            "warnings": [], "cached": False,
        }
    config = _load_json(config_path)
    config_hash = _hash_bytes(config_bytes)
    models_hash = _model_list_hash(model_ids)
    cache_path = _cache_path(root)
    now = time.time()

    stale_warning = None
    if use_cache:
        cached = _load_json(cache_path)
        if (cached.get("config_hash") == config_hash
                and cached.get("models_hash") == models_hash
                and isinstance(cached.get("results"), dict)):
            age = now - float(cached.get("checked_at") or 0)
            if 0 <= age <= CACHE_TTL_SECONDS:
                cached["cached"] = True
                return cached
            if age > STALE_SECONDS:
                # Never trust a day-old health snapshot, but never lock the
                # user out either.  Recompute now and leave a visible warning.
                stale_warning = (
                    f"preflight cache was stale ({age / 3600:.0f}h); rechecked"
                )

    known = config.get("models") or {}
    catalog = config.get("catalog") or {}
    providers = catalog.get("providers") or {}
    results: dict[str, dict] = {}
    errors: list[str] = []
    warnings: list[str] = []
    if stale_warning:
        warnings.append(stale_warning)
    for model in model_ids:
        entry = known.get(model)
        if not isinstance(entry, dict):
            results[model] = {"ok": False, "status": "missing",
                              "reason": "model not present in config catalog"}
            errors.append(f"{model}: not in config catalog")
            continue
        provider = str(entry.get("provider") or model.split("/", 1)[0])
        provider_ready, provider_reason = _provider_ok(provider, config)
        auth_status = str(entry.get("auth_status") or "").lower()
        auth_ok = auth_status not in ("missing", "unconfigured", "unavailable", "no-auth")
        # Scout now keeps free models when the provider has no API key and
        # marks their auth_status as available.  The provider catalog still says
        # "unconfigured"; do not let that stale provider flag reject the only
        # working free routes.  Never bypass a dead local proxy.
        if (not provider_ready and entry.get("is_free") and auth_ok
                and provider in ("opencode", "opencode-go")
                and provider_reason.endswith("is unconfigured")):
            provider_ready = True
            provider_reason = "free model; provider key not required"
        health_ok, health_reason = _health_ok(model, root)
        ok = provider_ready and auth_ok and health_ok
        status = "available" if ok else "unavailable"
        if not provider_ready:
            reason = provider_reason
        elif not auth_ok:
            reason = f"model auth is {auth_status or 'missing'}"
        else:
            reason = health_reason
        results[model] = {"ok": ok, "status": status,
                          "provider": provider, "auth_status": auth_status or None,
                          "reason": reason}
        if not provider_ready:
            errors.append(f"{model}: {provider_reason}")
        if not auth_ok:
            errors.append(f"{model}: model auth is {auth_status or 'missing'}")
        if not health_ok:
            errors.append(f"{model}: {health_reason}")

    if not model_ids:
        errors.append("no model requested")
    if not providers:
        warnings.append("config has no provider catalog")
    age = config_age_days(config, now)
    if age is not None and age > CATALOG_MAX_AGE_DAYS:
        warnings.append(
            f"model catalog is {age:.0f} days old; a model removed upstream still "
            "passes this gate (re-run scout.py --write to refresh, or use --live)")

    payload = {
        "ok": not errors,
        "checked_at": now,
        "config_hash": config_hash,
        "models_hash": models_hash,
        "models": model_ids,
        "results": results,
        "errors": errors,
        "warnings": warnings,
        "cached": False,
        "cacheable": not any(
            str((results.get(model) or {}).get("provider") or "").lower() == "9router"
            for model in model_ids
        ),
    }
    if errors and require_all:
        payload["ok"] = False
    # A failed probe is not cached: a stopped proxy or fixed auth must recover
    # immediately, not after the 15-minute TTL.  9router is a local proxy, so
    # it is probed every run even when the rest of the catalog is cacheable.
    if payload["ok"] and payload["cacheable"]:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        except OSError:
            pass
    return payload


def check_chain(root, models, *, use_cache: bool = True) -> dict:
    """Check every model in a coder chain.

    A chain is usable when at least one model is available.  Broken fallbacks
    are reported as warnings, not fatal, because the primary may still work.
    Reviewer panels use ``check_models(require_all=True)`` instead.
    """
    result = check_models(root, models, use_cache=use_cache, strict=False,
                          require_all=False)
    available = [model for model in result.get("models") or []
                 if (result.get("results") or {}).get(model, {}).get("ok")]
    result["available_models"] = available
    result["chain_ok"] = bool(available)
    result["ok"] = bool(available)
    if not available:
        result.setdefault("errors", []).append("no model in the coder chain is available")
    elif result.get("errors"):
        result.setdefault("warnings", []).append(
            "some coder fallback models are unavailable; primary/fallback may still work")
    return result


def render(result: dict) -> str:
    lines = ["health preflight: " + ("PASS" if result.get("ok") else "FAIL")]
    for model in result.get("models") or []:
        item = (result.get("results") or {}).get(model) or {}
        mark = "ok" if item.get("ok") else "FAIL"
        lines.append(f"  [{mark}] {model}  {item.get('reason') or item.get('status') or ''}")
    for error in result.get("errors") or []:
        lines.append(f"  error: {error}")
    if result.get("cached"):
        lines.append("  (cached)")
    return "\n".join(lines)


def config_age_days(config: dict, now: float | None = None) -> float | None:
    """Age of the scouted model catalog, in days.  ``None`` when unparseable."""
    raw = str((config or {}).get("scouted_at") or "").strip()
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0.0, ((now or time.time()) - stamp.timestamp()) / 86400)


def _opencode_binary(root) -> str:
    config = _load_json(_config_path(root))
    configured = str(((config.get("platform") or {}).get("opencode_cli")) or "").strip()
    try:
        # On Windows `shutil.which("opencode")` can return an npm .CMD shim
        # while the actual executable is under node_modules.  Workers knows how
        # to unwrap that shim and is the canonical resolver.
        import workers
        return workers.resolve_binary(configured or None)
    except Exception:                            # noqa: BLE001
        return configured or os.environ.get("OPENCODE_BIN") or "opencode"


def live_models(root, *, timeout: float = 30.0) -> set[str] | None:
    """Model IDs the OpenCode CLI exposes right now.

    Returns ``None`` when the CLI cannot be queried, so callers can degrade to a
    warning instead of a false failure.
    """
    binary = _opencode_binary(root)
    try:
        proc = subprocess.run([binary, "models"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    ids = {line.strip() for line in str(proc.stdout or "").splitlines()
           if "/" in line and " " not in line.strip()}
    return ids or None


def check_live(root, models, *, use_cache: bool = True,
               require_live: bool = False) -> dict:
    """Health check plus a cross-check against the live CLI catalog.

    The scouted catalog is a snapshot: a model removed upstream keeps looking
    ``available`` and the gate passes, then the call fails with ``no-route``.
    This is the check that actually catches that.
    """
    result = check_models(root, models, use_cache=use_cache)
    live = live_models(root)
    if live is None:
        message = "live catalog unavailable; model existence was not cross-checked"
        if require_live:
            result["ok"] = False
            result.setdefault("errors", []).append(message)
        else:
            result.setdefault("warnings", []).append(message)
        return result
    # `opencode models` exposes both personal (`opencode/`) and subscription
    # (`opencode-go/`) IDs.  Missing a Go model was exactly the failure mode
    # that made a reviewer route look healthy until call time.
    live_prefixes = ("opencode/", "opencode-go/")
    missing = [model for model in result.get("models") or []
               if model.startswith(live_prefixes) and model not in live]
    for model in missing:
        entry = result.setdefault("results", {}).setdefault(model, {})
        entry.update({"ok": False, "status": "unavailable",
                      "reason": "not in live `opencode models` catalog"})
        result.setdefault("errors", []).append(
            f"{model}: not in live `opencode models` catalog")
    if missing:
        result["ok"] = False
    return result


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Read-only health gate. Never calls a model.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--live", action="store_true",
                        help="also cross-check the live `opencode models` catalog")
    parser.add_argument("--roles", default=None,
                        help="comma list role=model (default: wave reviewer roles)")
    args = parser.parse_args(argv)

    try:
        import wave
        roles = wave.normalize_roles(args.roles)
    except Exception:                            # noqa: BLE001
        roles = {}
    models = list(roles.values())
    if not models:
        print("no models to check (pass --roles role=model,...)")
        return 2
    result = (check_live(args.root, models, require_live=True)
              if args.live else check_models(args.root, models))
    for role, model in roles.items():
        print(f"  role {role:<9} {model}")
    print(render(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
