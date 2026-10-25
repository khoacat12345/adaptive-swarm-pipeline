#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scout — doc catalog model + auth, anh xa vai tro, sinh .agents/config.json.

QUY TAC CUNG:
  - Scout chay 1 lan khi cai dat hoac doi may. TUYET DOI khong chay trong luc thuc thi task.
  - Mac dinh KHONG probe (khong ban token). Chi probe khi co co --verify.

Nguon catalog (theo thu tu uu tien):
  1. --catalog-file PATH          : JSON day du metadata (offline / CI)
  2. OpenCode CLI `opencode models`
  3. Config file: <config_dir>/opencode.json (provider + apiKey + model khai bao)
  4. Builtin snapshot             : danh sach model doc tu may nay, co ngay chup

Anh xa vai tro la HEURISTIC (heuristic-v1), dua tren ten ho model + metadata quan sat duoc,
KHONG phai bang do thuc nghiem. Phase 4 (eval harness) se thay bang so do that.

CLI:
  python .agents/scout.py                 # preview, khong ghi
  python .agents/scout.py --write         # ghi config, mac dinh CHI model mien phi
  python .agents/scout.py --write --allow-paid           # opt-in ro rang cho model tra phi
  python .agents/scout.py --write --verify              # + micro-ping moi vai tro (1 request/vai tro)
  python .agents/scout.py --verify --verify-roles coder
  python .agents/scout.py --json
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger  # noqa: E402
import safe_writer  # noqa: E402

CONFIG_FILENAME = "config.json"
CONFIG_VERSION = "0.5.0"
SCHEMA_VERSION = 1
STALE_DAYS = 30

ROLES = ("coder", "reviewer", "security", "architect", "test_writer")
ROLE_LABEL = {
    "coder": "Coder",
    "reviewer": "Reviewer",
    "security": "Security",
    "architect": "Architect",
    "test_writer": "Test Writer",
}
CHAIN_SIZE = 3          # primary + toi da 2 du phong
MIN_CHAIN = 2           # DoD: moi vai tro >= 2 model

MICRO_PING_PROMPT = "Reply with exactly: OK"
DEFAULT_VERIFY_TEMPLATE = 'opencode run -m {model} "{prompt}"'
CLI_TIMEOUT = 90

# --------------------------------------------------------------------------
# Metadata da kiem chung (pricing doc tu catalog OpenCode bang cong cu models)
# Chi ghi nhung gi da xac minh. Truong khong biet -> de null, KHONG bia.
# --------------------------------------------------------------------------

CURATED_METADATA: dict[str, dict] = {
    "opencode/deepseek-v4.1-flash": {
        "pricing": {"input": 0.30, "output": 1.20, "cache_read": 0.006, "cache_write": 0.0},
        "supports_cache": True,
    },
    "opencode/deepseek-v4-pro": {
        "pricing": {"input": 1.74, "output": 3.84, "cache_read": 0.145, "cache_write": 0.0},
        "supports_cache": True,
    },
    "opencode/claude-haiku-5-5": {
        "pricing": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
        "supports_cache": True,
    },
    "opencode/claude-sonnet-5-5": {
        "pricing": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
        "supports_cache": True,
    },
    "opencode/claude-opus-5-5": {
        "pricing": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
        "supports_cache": True,
    },
    "opencode/claude-fable-5-1": {
        "pricing": {"input": 10.00, "output": 50.00, "cache_read": 0.25, "cache_write": 12.50},
        "supports_cache": True,
    },
}

# --------------------------------------------------------------------------
# Heuristic: ho model -> nang luc uoc luong (0..1)
# --------------------------------------------------------------------------

FAMILY_RULES: list[tuple[str, str]] = [
    ("codex", "gpt-codex"),
    ("claude", "claude"), ("fable", "claude"),
    ("gemini", "gemini"), ("gemma", "gemma"),
    ("gpt", "gpt"), ("-o3", "gpt"), ("-o4", "gpt"),
    ("deepseek", "deepseek"),
    ("qwen", "qwen"),
    ("glm", "glm"),
    ("grok", "grok"),
    ("kimi", "kimi"),
    ("minimax", "minimax"),
    ("mistral", "mistral"), ("magistral", "mistral"),
    ("nemotron", "nemotron"),
    ("llama", "llama"),
    ("longcat", "longcat"),
    ("mimo", "mimo"),
    ("muse", "muse"),
    ("beam", "opencode-internal"), ("pickle", "opencode-internal"),
]

FAMILY_BASE = {
    "claude":             {"code": 0.85, "reasoning": 0.88, "long_context": 0.85, "tooling": 0.90},
    "gpt":                {"code": 0.85, "reasoning": 0.88, "long_context": 0.85, "tooling": 0.90},
    "gpt-codex":          {"code": 0.95, "reasoning": 0.85, "long_context": 0.80, "tooling": 0.90},
    "gemini":             {"code": 0.82, "reasoning": 0.85, "long_context": 0.95, "tooling": 0.85},
    "gemma":              {"code": 0.60, "reasoning": 0.55, "long_context": 0.70, "tooling": 0.50},
    "deepseek":           {"code": 0.92, "reasoning": 0.85, "long_context": 0.80, "tooling": 0.80},
    "qwen":               {"code": 0.88, "reasoning": 0.78, "long_context": 0.80, "tooling": 0.75},
    "glm":                {"code": 0.85, "reasoning": 0.82, "long_context": 0.80, "tooling": 0.75},
    "grok":               {"code": 0.85, "reasoning": 0.85, "long_context": 0.85, "tooling": 0.80},
    "kimi":               {"code": 0.90, "reasoning": 0.80, "long_context": 0.90, "tooling": 0.75},
    "minimax":            {"code": 0.80, "reasoning": 0.78, "long_context": 0.85, "tooling": 0.70},
    "mistral":            {"code": 0.80, "reasoning": 0.72, "long_context": 0.75, "tooling": 0.70},
    "nemotron":           {"code": 0.78, "reasoning": 0.80, "long_context": 0.75, "tooling": 0.70},
    "longcat":            {"code": 0.72, "reasoning": 0.70, "long_context": 0.85, "tooling": 0.60},
    "mimo":               {"code": 0.75, "reasoning": 0.70, "long_context": 0.75, "tooling": 0.60},
    "muse":               {"code": 0.70, "reasoning": 0.70, "long_context": 0.70, "tooling": 0.60},
    "opencode-internal":  {"code": 0.65, "reasoning": 0.65, "long_context": 0.70, "tooling": 0.60},
    "unknown":            {"code": 0.70, "reasoning": 0.70, "long_context": 0.70, "tooling": 0.60},
}

SLOW_TOKENS = ("flash", "lite", "mini", "nano", "spark", "lightning", "haiku")
STRONG_TOKENS = ("opus", "pro", "ultra", "max", "sol", "terra", "astra")
REASON_TOKENS = ("thinking", "reasoning", "reason", "r1")
CODE_TOKENS = ("code", "coder", "codex")

ROLE_WEIGHTS = {
    "coder":       {"code": 0.60, "long_context": 0.20, "reasoning": 0.10, "tooling": 0.10},
    "reviewer":    {"reasoning": 0.45, "code": 0.30, "long_context": 0.15, "tooling": 0.10},
    "security":    {"reasoning": 0.60, "code": 0.30, "long_context": 0.10, "tooling": 0.0},
    "architect":   {"long_context": 0.45, "reasoning": 0.30, "code": 0.15, "tooling": 0.10},
    "test_writer": {"code": 0.55, "reasoning": 0.25, "tooling": 0.20, "long_context": 0.0},
}

ROLE_FAMILY_BONUS = {
    "coder":       {"deepseek": 0.14, "qwen": 0.12, "kimi": 0.10, "gpt-codex": 0.08, "glm": 0.06},
    "reviewer":    {"claude": 0.10, "gpt": 0.08, "gemini": 0.08, "grok": 0.05},
    "security":    {"claude": 0.08, "gpt": 0.08, "deepseek": 0.06, "glm": 0.06, "nemotron": 0.06},
    "architect":   {"gemini": 0.10, "claude": 0.08, "gpt": 0.08, "grok": 0.05},
    "test_writer": {"gpt-codex": 0.08, "deepseek": 0.08, "qwen": 0.06, "claude": 0.06},
}

# Vai tro bat buoc khac ho voi coder de tranh diem mu tuong quan
MUST_DIFFER_FROM_CODER = ("reviewer", "test_writer")

# Trong so vai tro khi giai bai toan gan primary. Ly do: sai o khau code thi loi
# lan ra toan bo san pham, con sai o khau viet test thi con cuu duoc -> coder duoc
# uu tien cao nhat. Day khong phai "so thich": no la thu tu chi phi khi sai.
ROLE_PRIORITY = {
    "coder": 1.0,
    "reviewer": 0.9,
    "security": 0.9,
    "architect": 0.8,
    "test_writer": 0.7,
}

COST_RANK = {"free": 0, "cheap": 1, "unknown": 2, "standard": 3, "premium": 4}

ENV_PROVIDER_KEYS = {
    "ANTHROPIC_API_KEY": "anthropic",
    "OPENAI_API_KEY": "openai",
    "GEMINI_API_KEY": "google",
    "GOOGLE_GENERATIVE_AI_API_KEY": "google",
    "DEEPSEEK_API_KEY": "deepseek",
    "GROQ_API_KEY": "groq",
    "OPENROUTER_API_KEY": "openrouter",
    "MISTRAL_API_KEY": "mistral",
    "XAI_API_KEY": "xai",
}

# Snapshot doc tu chinh may nay (nguon du phong khi khong goi duoc CLI)
SNAPSHOT_DATE = "2026-10-19"
SNAPSHOT_MODELS = [
    "9router/ag/claude-opus-4-6-thinking", "9router/ag/claude-sonnet-4-6",
    "9router/ag/gemini-3.8-flash", "9router/ag/gemini-3.8-flash-high",
    "9router/apiai/gpt-5.6-sol", "9router/gemini/gemini-2.5-pro",
    "9router/gemini/gemini-3.5-flash-lite", "9router/gemini/gemini-3.6-flash",
    "9router/gemini/gemini-3.7-flash", "9router/gemini/gemini-3.8-flash",
    "9router/gemini/gemma-4-31b-it",
    "opencode/beam-free", "opencode/big-pickle", "opencode/claude-fable-5-1",
    "opencode/claude-haiku-5-5", "opencode/claude-opus-5-5", "opencode/claude-sonnet-5-5",
    "opencode/deepseek-v4-pro", "opencode/deepseek-v4.1-flash", "opencode/deepseek-v4.1-flash-free",
    "opencode/gemini-3.1-pro", "opencode/gemini-3.5-flash-lite", "opencode/gemini-3.8-flash",
    "opencode/gemini-4-argon", "opencode/glm-5.3", "opencode/glm-5.3-flash",
    "opencode/glm-5.3-flash-free", "opencode/gpt-5.3-codex", "opencode/gpt-5.3-codex-spark",
    "opencode/gpt-5.4-mini", "opencode/gpt-5.4-nano", "opencode/gpt-5.5",
    "opencode/gpt-5.5-pro", "opencode/gpt-5.6-terra", "opencode/gpt-6-astra",
    "opencode/gpt-6-luna", "opencode/gpt-6.1-sol", "opencode/grok-4.7",
    "opencode/grok-build-0.1", "opencode/kimi-k2.7-code", "opencode/kimi-k3",
    "opencode/longcat-2.5-preview-free", "opencode/mimo-v2.6-flash-free",
    "opencode/minimax-m3", "opencode/minimax-m3.1-flash-free", "opencode/mistral-large-4",
    "opencode/muse-spark-1.3", "opencode/nemotron-3-ultra-free",
    "opencode/nemotron-3.5-lightning-free", "opencode/nemotron-3.5-super-vl-free",
    "opencode/qwen3.6-plus", "opencode/qwen3.8-flash",
]


# --------------------------------------------------------------------------
# 0.5.1 — Doc catalog
# --------------------------------------------------------------------------

def _run_cli(command: str, timeout: int = CLI_TIMEOUT) -> dict:
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": 124, "stdout": "",
                "stderr": f"timeout sau {timeout}s", "duration_ms": int((time.perf_counter() - started) * 1000)}
    except OSError as exc:
        return {"ok": False, "exit_code": 127, "stdout": "", "stderr": str(exc),
                "duration_ms": int((time.perf_counter() - started) * 1000)}


def find_opencode_cli() -> str | None:
    return shutil.which("opencode") or shutil.which("opencode.cmd") or shutil.which("opencode.ps1")


def opencode_config_dirs(root: Path) -> list[Path]:
    dirs = []
    env_config = os.environ.get("OPENCODE_CONFIG")
    if env_config:
        dirs.append(Path(env_config).parent)
    dirs.append(Path.home() / ".config" / "opencode")
    dirs.append(root / ".opencode")
    seen, unique = set(), []
    for d in dirs:
        key = str(d).lower()
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def read_opencode_configs(root: Path) -> dict:
    """Doc moi opencode.json tim duoc -> provider nao co apiKey, model nao duoc khai bao."""
    providers: dict[str, dict] = {}
    files: list[str] = []
    for directory in opencode_config_dirs(root):
        for name in ("opencode.json", "opencode.jsonc"):
            path = directory / name
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            files.append(str(path))
            for provider_id, spec in (data.get("provider") or {}).items():
                spec = spec or {}
                entry = providers.setdefault(provider_id, {"has_api_key": False, "models": [], "base_url": None})
                options = spec.get("options") or {}
                if options.get("apiKey") or options.get("api_key"):
                    entry["has_api_key"] = True
                if options.get("baseURL"):
                    entry["base_url"] = options["baseURL"]
                for model_id in (spec.get("models") or {}):
                    entry["models"].append(model_id)
    return {"providers": providers, "files": files}


def parse_auth_flags(raw: str) -> dict:
    """Identify the two OpenCode subscription families in `auth list`."""
    text = str(raw or "").lower()
    stored = "stored" in text
    return {
        "has_console_auth": stored and (
            "opencode console" in text or "personal" in text),
        "has_go_auth": stored and "opencode go" in text,
    }


def read_auth_status() -> dict:
    """`opencode auth list` -> text. Tra ve raw + credential flags."""
    cli = find_opencode_cli()
    if not cli:
        return {"available": False, "raw": "",
                "has_console_auth": False, "has_go_auth": False}
    result = _run_cli(f'"{cli}" auth list', timeout=30)
    raw = (result["stdout"] or "") + (result["stderr"] or "")
    flags = parse_auth_flags(raw)
    return {
        "available": result["ok"],
        "raw": raw.strip(),
        **flags,
    }


def read_catalog_from_cli() -> dict:
    cli = find_opencode_cli()
    if not cli:
        return {"ok": False, "source": None, "models": [], "error": "khong tim thay opencode CLI"}
    result = _run_cli(f'"{cli}" models', timeout=CLI_TIMEOUT)
    if not result["ok"]:
        return {"ok": False, "source": None, "models": [],
                "error": (result["stderr"] or "").strip()[:200] or f"exit={result['exit_code']}"}
    models = []
    for line in result["stdout"].splitlines():
        line = line.strip()
        if not line or " " in line or "/" not in line:
            continue
        models.append(line)
    return {"ok": bool(models), "source": "opencode-cli", "models": models,
            "error": None, "duration_ms": result["duration_ms"]}


def read_catalog_file(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "source": None, "models": [], "error": f"khong doc duoc {path}: {exc}"}
    if isinstance(data, dict) and "models" in data:
        raw, extra = data["models"], data.get("metadata") or {}
    else:
        raw, extra = data, {}
    models = []
    if isinstance(raw, list):
        for item in raw:
            models.append(item if isinstance(item, str) else item.get("id"))
    elif isinstance(raw, dict):
        models = list(raw.keys())
    models = [m for m in models if isinstance(m, str) and m]
    return {"ok": bool(models), "source": f"catalog-file:{Path(path).name}",
            "models": models, "metadata": extra, "error": None}


def builtin_snapshot() -> dict:
    return {"ok": True, "source": f"builtin-snapshot@{SNAPSHOT_DATE}",
            "models": list(SNAPSHOT_MODELS), "error": None}


def model_family(model_id: str) -> str:
    lowered = model_id.lower()
    for token, family in FAMILY_RULES:
        if token in lowered:
            return family
    return "unknown"


def estimate_capabilities(model_id: str, family: str) -> dict:
    caps = dict(FAMILY_BASE.get(family, FAMILY_BASE["unknown"]))
    lowered = model_id.lower()
    if any(t in lowered for t in REASON_TOKENS):
        caps["reasoning"] = min(1.0, caps["reasoning"] + 0.06)
    if "codex" in lowered or family == "gpt-codex":
        caps["code"] = min(1.0, caps["code"] + 0.05)
    if any(t in lowered for t in CODE_TOKENS):
        caps["code"] = min(1.0, caps["code"] + 0.02)
    if "opus" in lowered:
        caps["code"] = min(1.0, caps["code"] + 0.05)
        caps["reasoning"] = min(1.0, caps["reasoning"] + 0.05)
    if any(t in lowered for t in STRONG_TOKENS):
        caps["reasoning"] = min(1.0, caps["reasoning"] + 0.03)
    if any(t in lowered for t in SLOW_TOKENS):
        caps["code"] = max(0.0, caps["code"] - 0.05)
        caps["reasoning"] = max(0.0, caps["reasoning"] - 0.05)
    return {k: round(v, 3) for k, v in caps.items()}


def build_model_entry(model_id: str, provider: str, auth_status: str, curated: dict) -> dict:
    family = model_family(model_id)
    caps = estimate_capabilities(model_id, family)
    meta = dict(CURATED_METADATA.get(model_id, {}))
    meta.update(curated or {})

    pricing = meta.get("pricing")
    is_free = bool(meta.get("is_free")) or model_id.endswith("-free") or (
        isinstance(pricing, dict)
        and (pricing.get("input") in (0, 0.0)) and (pricing.get("output") in (0, 0.0))
    )

    if is_free:
        cost_tier = "free"
    elif isinstance(pricing, dict) and pricing.get("input") is not None:
        price = float(pricing["input"])
        cost_tier = "cheap" if price <= 0.5 else ("standard" if price <= 2.0 else "premium")
    else:
        lowered = model_id.lower()
        cost_tier = "cheap" if (any(t in lowered for t in SLOW_TOKENS) or family in
                                ("longcat", "mimo", "nemotron", "gemma")) else "unknown"

    return {
        "id": model_id,
        "provider": provider,
        "family": family,
        "auth_status": auth_status,
        "context_window": meta.get("context_window"),
        "pricing": pricing,
        "is_free": is_free,
        "cost_tier": cost_tier,
        "supports_cache": meta.get("supports_cache"),
        "capabilities": caps,
        "metadata_source": "curated+catalog" if meta else "heuristic-v1",
        "scoring": "heuristic-v1",
    }


def collect_catalog(root: Path, catalog_file: str | None, use_cli: bool,
                    include_unconfigured: bool, free_only: bool = False) -> dict:
    """Tra ve {source, models:[entry], providers:{}, warnings:[], stats:{}}"""
    warnings: list[str] = []
    local = read_opencode_configs(root)
    auth = read_auth_status()

    if catalog_file:
        primary = read_catalog_file(Path(catalog_file))
    elif use_cli:
        primary = read_catalog_from_cli()
    else:
        primary = {"ok": False, "source": None, "models": [], "error": "bo qua CLI (--no-cli)"}

    if not primary.get("ok"):
        if primary.get("error"):
            warnings.append(f"Khong doc duoc catalog tu CLI: {primary['error']}")
        snapshot = builtin_snapshot()
        warnings.append(
            f"Dung snapshot du phong ({SNAPSHOT_DATE}) -- model co the da doi ten hoac bi go. "
            "Chay lai scout khi co mang."
        )
        primary = snapshot

    provider_auth: dict[str, str] = {}
    for model_id in primary["models"]:
        provider_auth.setdefault(model_id.split("/")[0], "unconfigured")
    if auth.get("has_console_auth"):
        provider_auth["opencode"] = "available"
    if auth.get("has_go_auth"):
        # `opencode auth list` is authoritative: OpenCode Go may be stored even
        # when no local provider entry exists in opencode.json.
        provider_auth["opencode-go"] = "available"
    for provider_id, spec in local["providers"].items():
        if spec.get("has_api_key"):
            provider_auth[provider_id] = "available"
    for env_key, provider_id in ENV_PROVIDER_KEYS.items():
        if os.environ.get(env_key):
            provider_auth[provider_id] = "available"
    for provider_id in provider_auth:
        if provider_auth[provider_id] == "unconfigured" and provider_id in local["providers"]:
            provider_auth[provider_id] = "unknown"

    extra_meta = primary.get("metadata") or {}
    entries: list[dict] = []
    dropped: dict[str, int] = {}
    for model_id in primary["models"]:
        provider = model_id.split("/")[0]
        status = provider_auth.get(provider, "unknown")
        if status == "unconfigured" and not include_unconfigured:
            dropped[provider] = dropped.get(provider, 0) + 1
            continue
        entries.append(build_model_entry(model_id, provider, status, extra_meta.get(model_id)))

    if dropped:
        warnings.append(
            "Da loai model cua provider chua cau hinh API key: "
            + ", ".join(f"{k}({v})" for k, v in dropped.items())
            + " -- dung --include-unconfigured de giu lai"
        )

    if free_only:
        paid = [e for e in entries if not e["is_free"]]
        entries = [e for e in entries if e["is_free"]]
        if paid:
            warnings.append(
                f"--free-only: da loai {len(paid)} model khong mien phi, con {len(entries)} model free"
            )

    for provider_id, spec in local["providers"].items():
        if spec.get("base_url") and "127.0.0.1" in spec["base_url"]:
            warnings.append(
                f"Provider '{provider_id}' tro vao endpoint local ({spec['base_url']}) -- "
                "chi hoat dong khi proxy dang chay"
            )
    if not entries:
        warnings.append("Khong co model nao dung duoc -- kiem tra lai auth/config truoc khi chay task")

    return {
        "source": primary["source"],
        "models": entries,
        "providers": provider_auth,
        "warnings": warnings,
        "stats": {
            "catalog_total": len(primary["models"]),
            "usable": len(entries),
            "dropped_unconfigured": sum(dropped.values()),
            "free_only": free_only,
            "providers": {p: c for p, c in sorted(provider_auth.items())},
        },
        "auth_raw": auth.get("raw", ""),
        "config_files": local["files"],
    }


# --------------------------------------------------------------------------
# 0.5.2 — Anh xa vai tro (heuristic-v1)
# --------------------------------------------------------------------------

COST_PENALTY_PER_RANK = {"capability-first": 0.0, "balanced": 0.03, "cost-first": 0.06}
DIVERSITY_BONUS = 0.05   # tru diem khi 2 primary trung ho model


def role_score(model: dict, role: str, cost_policy: str = "balanced") -> float:
    weights = ROLE_WEIGHTS[role]
    caps = model["capabilities"]
    score = sum(weights.get(key, 0.0) * caps.get(key, 0.0) for key in weights)
    score += ROLE_FAMILY_BONUS.get(role, {}).get(model["family"], 0.0)
    scale = COST_PENALTY_PER_RANK.get(cost_policy, 0.03)
    score -= min(COST_RANK.get(model["cost_tier"], 2) * scale, scale * 4)
    return round(score, 4)


def _why(model: dict, role: str, score: float, cost_policy: str = "balanced", extra: str = "") -> str:
    bits = [f"{role}-score={score:.3f}", f"family={model['family']}",
            f"code={model['capabilities']['code']:.2f}",
            f"reasoning={model['capabilities']['reasoning']:.2f}",
            f"long_ctx={model['capabilities']['long_context']:.2f}",
            f"cost={model['cost_tier']}",
            f"cost_policy={cost_policy}"]
    if extra:
        bits.append(extra)
    return "; ".join(bits)


def assign_primaries(models: list[dict], scored: dict[str, dict[str, float]]) -> dict[str, str]:
    """Chon primary cho 5 vai tro sao cho: (a) 5 primary thuoc 5 HO KHAC NHAU,
    (b) tong diem la cao nhat co the.

    Vi sao phai khac ho: reviewer/security trung ho voi nhau thi gia tri phan bien
    doc lap bi triet tieu (cung mot ho = cung mot kieu diem mu).

    Neu so ho co san < so vai tro thi lui ve greedy (cho phep trung ho) de van
    lap duoc day du 5 vai tro.
    """
    families = sorted({m["family"] for m in models})
    family_of = {m["id"]: m["family"] for m in models}

    # best[(role, family)] = (diem cao nhat, model_id tot nhat trong ho do)
    best: dict[tuple[str, str], tuple[float, str]] = {}
    for role in ROLES:
        for family in families:
            options = [(scored[role][m["id"]], m["id"]) for m in models if m["family"] == family]
            if options:
                best[(role, family)] = max(options)

    if len(families) >= len(ROLES):
        # DFS co gioi han: moi vai tro chi xet 6 ho manh nhat cua no -> 6^5 to hop, tuc thoi.
        shortlist = {
            role: [family for _, family in sorted(
                ((best[(role, family)][0], family) for family in families if (role, family) in best),
                reverse=True,
            )[:6]]
            for role in ROLES
        }
        winner: dict[str, str] = {}

        def dfs(index: int, chosen: list[str], total: float) -> None:
            nonlocal winner
            if index == len(ROLES):
                if not winner or total > winner["_total"]:  # type: ignore[index]
                    winner = {r: f for r, f in zip(ROLES, chosen)}
                    winner["_total"] = total              # type: ignore[assignment]
                return
            role = ROLES[index]
            for family in shortlist[role]:
                if family in chosen:
                    continue
                dfs(index + 1, chosen + [family],
                    total + best[(role, family)][0] * ROLE_PRIORITY[role])

        dfs(0, [], 0.0)
        if winner:
            return {role: best[(role, winner[role])][1] for role in ROLES}

    # Du phong: it ho hon vai tro -> greedy, cho phep trung ho nhung tranh khi co the.
    primary: dict[str, str] = {}
    used_models: set[str] = set()
    used_families: list[str] = []
    for strict in (True, False):
        while True:
            choice = None
            for role in ROLES:
                if role in primary:
                    continue
                for model in models:
                    model_id, family = model["id"], model["family"]
                    if model_id in used_models and len(used_models) < len(ROLES):
                        continue
                    if strict and role in MUST_DIFFER_FROM_CODER and "coder" in primary:
                        if family == family_of[primary["coder"]]:
                            continue
                    score = scored[role][model_id]
                    if family in used_families:
                        score -= DIVERSITY_BONUS
                    if choice is None or score > choice[0]:
                        choice = (score, role, model_id)
            if choice is None:
                break
            _, role, model_id = choice
            primary[role] = model_id
            used_models.add(model_id)
            used_families.append(family_of[model_id])
    return primary


def map_roles(models: list[dict], cost_policy: str = "balanced") -> tuple[dict, list[str]]:
    """Tra ve (roles, warnings). Moi vai tro co chain >= MIN_CHAIN neu co du model."""
    warnings: list[str] = []
    if not models:
        return {}, ["Khong co model nao de anh xa vai tro"]

    scored = {role: {m["id"]: role_score(m, role, cost_policy) for m in models} for role in ROLES}
    by_id = {m["id"]: m for m in models}

    # 5 primary thuoc 5 ho khac nhau, tong diem cao nhat (bai toan gan co rang buoc).
    primary = assign_primaries(models, scored)

    roles: dict[str, dict] = {}
    for role in ROLES:
        head_id = primary.get(role)
        if not head_id:
            warnings.append(f"Vai tro '{role}' khong co model phu hop")
            continue
        head = by_id[head_id]
        note = ""
        if role in MUST_DIFFER_FROM_CODER and "coder" in primary:
            note = f"khac ho voi coder ({by_id[primary['coder']]['family']})"

        # Chuoi du phong duoc thiet ke theo DUNG 2 kieu su co da quan sat tren may that:
        #   1. het quota / khong du tien  -> can mot model MIEN PHI (cua thoat chi phi)
        #   2. provider sap / model bi go  -> can mot model cua PROVIDER KHAC (cua thoat outage)
        # Con lai moi lap day bang diem nang luc, uu tien khac ho.
        by_score = sorted(models, key=lambda m: scored[role][m["id"]], reverse=True)
        chain = [head]
        used_ids = {head["id"]}

        def take_one(predicate) -> bool:
            for model in by_score:
                if model["id"] in used_ids or not predicate(model):
                    continue
                chain.append(model)
                used_ids.add(model["id"])
                return True
            return False

        if not head["is_free"]:
            take_one(lambda m: m["is_free"])
        take_one(lambda m: m["provider"] != head["provider"])
        while len(chain) < CHAIN_SIZE:
            if not (take_one(lambda m: m["family"] != head["family"]) or take_one(lambda m: True)):
                break

        if not any(m["is_free"] for m in chain) and any(m["is_free"] for m in models):
            warnings.append(
                f"Vai tro '{role}': chuoi khong co model mien phi nao -> khong co cua thoat khi het quota"
            )

        if len(chain) < MIN_CHAIN:
            warnings.append(
                f"Vai tro '{role}' chi co {len(chain)} model (can >= {MIN_CHAIN}) -- "
                "them provider hoac dung --include-unconfigured"
            )
        roles[role] = {
            "chain": [m["id"] for m in chain],
            "primary": chain[0]["id"],
            "fallbacks": [m["id"] for m in chain[1:]],
            "primary_provider": head["provider"],
            "primary_family": head["family"],
            "primary_cost_tier": head["cost_tier"],
            "why": _why(head, role, scored[role][head_id], cost_policy, note),
        }
    return roles, warnings


# --------------------------------------------------------------------------
# 0.5.3 — Config + preview diff + ghi an toan
# --------------------------------------------------------------------------

def config_path_for(root: Path) -> Path:
    return ledger.resolve_agents_dir(root=root) / CONFIG_FILENAME


def read_existing_config(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def config_age_days(existing: dict | None) -> float | None:
    if not existing:
        return None
    raw = existing.get("scouted_at")
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400.0


def validate_config(config: dict) -> list[str]:
    problems: list[str] = []
    if config.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema_version khong khop")
    if not config.get("scouted_at"):
        problems.append("thieu scouted_at")
    roles = config.get("roles") or {}
    for role in ROLES:
        entry = roles.get(role)
        if not entry:
            problems.append(f"thieu vai tro '{role}'")
            continue
        chain = entry.get("chain") or []
        if len(chain) < MIN_CHAIN:
            problems.append(f"vai tro '{role}' chi co {len(chain)} model (can >= {MIN_CHAIN})")
        if entry.get("primary") != (chain[0] if chain else None):
            problems.append(f"vai tro '{role}': primary khong khop chain[0]")
    return problems


def render_preview(new: dict, existing: dict | None, path: Path, age_days: float | None) -> list[str]:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("  SCOUT PREVIEW" + ("" if existing is None else "  (config cu da ton tai)"))
    lines.append("=" * 78)
    lines.append(f"  file        : {path}")
    lines.append(f"  catalog     : {new['catalog']['source']}  "
                 f"(tong {new['catalog']['stats']['catalog_total']} model, "
                 f"dung duoc {new['catalog']['stats']['usable']})")
    lines.append(f"  scouted_at  : {new['scouted_at']}")
    lines.append(f"  providers   : {json.dumps(new['catalog']['stats']['providers'], ensure_ascii=False)}")

    if existing is None:
        lines.append("")
        lines.append("  TRANG THAI: TAO MOI (chua co config.json)")
        lines.append("")
        for role in ROLES:
            entry = new["roles"].get(role)
            if not entry:
                continue
            head = (new.get("models") or {}).get(entry["primary"], {})
            lines.append(f"  {ROLE_LABEL[role]:<12} cost={str(head.get('cost_tier', '?')):<8} "
                         f"family={str(head.get('family', '?'))}")
            lines.append(f"  {'':<12} primary  : {entry['primary']}")
            for index, fallback in enumerate(entry.get("fallbacks") or [], start=1):
                lines.append(f"  {'':<12} fallback{index}: {fallback}")
    else:
        lines.append("")
        lines.append("  DIFF THEO VAI TRO")
        lines.append(f"  {'VAI TRO':<12} {'TRUOC':<32} {'SAU':<32}")
        lines.append("  " + "-" * 78)
        old_roles = existing.get("roles") or {}
        for role in ROLES:
            old_primary = ((old_roles.get(role) or {}).get("primary")) or "-"
            new_primary = ((new["roles"].get(role) or {}).get("primary")) or "-"
            mark = "  " if old_primary == new_primary else ">>"
            lines.append(f"{mark}{ROLE_LABEL[role]:<12} {old_primary:<32} {new_primary:<32}")
        for role in ROLES:
            old_chain = (old_roles.get(role) or {}).get("chain") or []
            new_chain = (new["roles"].get(role) or {}).get("chain") or []
            if old_chain != new_chain:
                lines.append(f"    {ROLE_LABEL[role]:<12} chain: {' -> '.join(old_chain)}")
                lines.append(f"    {'':<12}   =>   {' -> '.join(new_chain)}")

    if age_days is not None and age_days > STALE_DAYS:
        lines.append("")
        lines.append(f"  !! CANH BAO: config cu da {age_days:.0f} ngay (> {STALE_DAYS} ngay).")
        lines.append("     Model co the da bi doi ten hoac go bo. Nen chay lai scout --write.")

    if new["warnings"]:
        lines.append("")
        for warning in new["warnings"]:
            lines.append(f"  [!] {warning}")
    lines.append("")
    if (new.get("constraints") or {}).get("free_only"):
        lines.append("  rang buoc cung: --free-only (chi dung model mien phi)")
    else:
        lines.append(f"  cost_policy = {new.get('cost_policy')}  "
                     "(doi bang --cost-policy cost-first neu muon uu tien model free/roi)")
    lines.append("=" * 78)
    return lines


# --------------------------------------------------------------------------
# 0.5.4 — Micro-ping (mac dinh TAT)
# --------------------------------------------------------------------------

def _verify_one(role: str, model_id: str, template: str, timeout: int,
                run_id: str, root: Path) -> dict:
    """Send exactly one micro-ping for one role/model."""
    command = template.format(model=model_id, prompt=MICRO_PING_PROMPT)
    started = time.perf_counter()
    outcome = _run_cli(command, timeout=timeout)
    duration_ms = int((time.perf_counter() - started) * 1000)
    ok = outcome["ok"] and bool((outcome["stdout"] or "").strip())
    payload = {
        "role": role,
        "model": model_id,
        "ok": ok,
        "exit_code": outcome["exit_code"],
        "duration_ms": duration_ms,
        "output_head": (outcome["stdout"] or "").strip()[:160],
        "error": None if ok else ((outcome["stderr"] or "").strip()[:200] or "khong co output"),
    }
    ledger.append_event(
        "scout.verify", run_id=run_id, duration_ms=duration_ms,
        result={"status": "pass" if ok else "fail", "error": payload["error"]},
        data={"role": role, "model": model_id, "exit_code": outcome["exit_code"]},
        root=root,
    )
    return payload


def verify_roles(roles: dict, roles_to_verify: list[str], template: str,
                 timeout: int, run_id: str, root: Path) -> dict:
    """Gui dung 1 request moi vai tro. Tra ve {role: result}."""
    results: dict[str, dict] = {}
    for role in roles_to_verify:
        entry = roles.get(role)
        if not entry:
            continue
        results[role] = _verify_one(role, entry["primary"], template,
                                    timeout, run_id, root)
    return results


def verify_models(models: dict, template: str, timeout: int,
                  run_id: str, root: Path) -> dict:
    """Verify the four meeting/wave reviewer models as well as main roles."""
    return {f"wave:{role}": _verify_one(f"wave:{role}", model, template,
                                        timeout, run_id, root)
            for role, model in (models or {}).items()}


# --------------------------------------------------------------------------
# Build config
# --------------------------------------------------------------------------

def build_config(root: Path, catalog: dict, roles: dict, run_id: str,
                 verify_results: dict | None, cost_policy: str = "balanced") -> dict:
    cli = find_opencode_cli()
    version = _run_cli(f'"{cli}" --version', timeout=20)["stdout"].strip() if cli else None
    return {
        "schema_version": SCHEMA_VERSION,
        "version": CONFIG_VERSION,
        "scouted_at": ledger.utc_now_iso(),
        "generated_by": "scout.py",
        "mapping_method": "heuristic-v1",
        "cost_policy": cost_policy,
        "constraints": {"free_only": bool((catalog.get("stats") or {}).get("free_only"))},
        "mapping_note": (
            "Anh xa vai tro dua tren ten ho model + metadata quan sat duoc, KHONG phai bang do "
            "thuc nghiem. Phase 4 (eval harness) se thay bang ty le pass do duoc."
        ),
        "roles": roles,
        "models": {m["id"]: m for m in catalog["models"]},
        "catalog": {
            "source": catalog["source"],
            "stats": catalog["stats"],
            "providers": catalog["stats"]["providers"],
            "auth_raw": catalog.get("auth_raw"),
            "config_files": catalog.get("config_files"),
        },
        "platform": {
            "os": os.name,
            "python": sys.version.split()[0],
            "opencode_cli": cli,
            "opencode_version": version or None,
        },
        "verify": {
            "enabled": bool(verify_results),
            "mode": "micro-ping-1-request-per-role" if verify_results else "off",
            "results": verify_results or None,
        },
        "warnings": catalog["warnings"],
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scout: doc catalog model + sinh .agents/config.json (chay 1 lan khi setup)")
    parser.add_argument("--root", default=None, help="project root (mac dinh: thu muc cha cua .agents/)")
    parser.add_argument("--write", action="store_true", help="ghi that vao config.json (mac dinh chi preview)")
    parser.add_argument("--verify", action="store_true",
                        help="bat micro-ping: 5 main roles + 4 meeting/wave reviewers")
    parser.add_argument("--verify-roles", default=None, help="chi verify vai tro cu the, vd: coder,reviewer")
    parser.add_argument("--verify-timeout", type=int, default=CLI_TIMEOUT)
    parser.add_argument("--verify-template", default=DEFAULT_VERIFY_TEMPLATE,
                        help="mau lenh ping; {model} va {prompt} se duoc thay")
    parser.add_argument("--cost-policy", default="balanced",
                        choices=("capability-first", "balanced", "cost-first"),
                        help="muc do uu tien chi phi khi chon model (mac dinh: balanced)")
    parser.add_argument("--catalog-file", default=None, help="doc catalog tu file JSON (offline)")
    parser.add_argument("--no-cli", action="store_true", help="khong goi opencode CLI, dung config/snapshot")
    parser.add_argument("--include-unconfigured", action="store_true",
                        help="giu ca model cua provider chua co API key")
    parser.add_argument("--free-only", action="store_true",
                        help="rang buoc cung: chi dung model mien phi (mac dinh)")
    parser.add_argument("--allow-paid", action="store_true",
                        help="opt-in: cho phep ca model tra phi (mac dinh khong)")
    parser.add_argument("--json", action="store_true", help="in config day du ra stdout")
    parser.add_argument("--quiet", action="store_true", help="chi in ket qua cuoi")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else ledger.AGENTS_DIR.parent
    run_id = ledger.new_run_id("scout")
    started = time.perf_counter()

    config_path = config_path_for(root)
    existing = read_existing_config(config_path)
    age_days = config_age_days(existing)

    # New installs are free-only by default.  Paid models require an explicit
    # --allow-paid opt-in; this prevents a fresh clone from silently spending.
    free_only = bool(args.free_only or not args.allow_paid)
    catalog = collect_catalog(root, args.catalog_file, not args.no_cli,
                              args.include_unconfigured, free_only)
    ledger.append_event(
        "scout.catalog.read", run_id=run_id,
        duration_ms=int((time.perf_counter() - started) * 1000),
        result={"status": "ok", "error": None},
        data={"source": catalog["source"], "usable": catalog["stats"]["usable"],
              "total": catalog["stats"]["catalog_total"]},
        root=root,
    )

    roles, role_warnings = map_roles(catalog["models"], args.cost_policy)
    catalog["warnings"] = catalog["warnings"] + role_warnings
    verify_results = None
    if args.verify:
        if args.verify_roles:
            requested = [r.strip() for r in args.verify_roles.split(",") if r.strip()]
            unknown = [r for r in requested if r not in ROLES]
            if unknown:
                print(f"Loi: vai tro khong hop le: {unknown}. Hop le: {list(ROLES)}", file=sys.stderr)
                return 2
        else:
            requested = list(ROLES)
        requested = [r for r in requested if r in roles][:5]   # toi da 5 request
        wave_models = {}
        if not args.verify_roles:
            try:
                import wave as wave_module
                wave_models = wave_module.normalize_roles()
            except Exception:                       # noqa: BLE001
                wave_models = {}
        if not args.quiet:
            total = len(requested) + len(wave_models)
            extra = (f" + {len(wave_models)} wave roles" if wave_models else "")
            print(f"[scout] --verify: se gui {total} request micro-ping "
                  f"({', '.join(requested)}{extra}) ...", file=sys.stderr)
        verify_results = verify_roles(roles, requested, args.verify_template,
                                      args.verify_timeout, run_id, root)
        if wave_models:
            verify_results.update(verify_models(
                wave_models, args.verify_template, args.verify_timeout, run_id, root))
        # Ket qua do duoc phai quay nguoc thanh canh bao: khong de nguoi dung tin
        # vao mot ke hoach ma may that khong goi duoc.
        for role, result in verify_results.items():
            if not result["ok"]:
                catalog["warnings"].append(
                    f"Vai tro '{role}': primary {result['model']} KHONG goi duoc "
                    f"({result['error']}) -- doi model khac ho, nap them quota, "
                    "hoac dung --cost-policy cost-first de uu tien model free"
                )
            if result["duration_ms"] > 15000:
                catalog["warnings"].append(
                    f"Vai tro '{role}': {result['model']} tra loi rat cham "
                    f"({result['duration_ms']} ms)"
                )

    config = build_config(root, catalog, roles, run_id, verify_results, args.cost_policy)
    problems = validate_config(config)

    ledger.append_event(
        "scout.plan", run_id=run_id,
        result={"status": "pass" if not problems else "fail",
                "error": "; ".join(problems) if problems else None},
        data={"roles": {r: (roles.get(r) or {}).get("chain") for r in ROLES},
              "warnings": catalog["warnings"]},
        root=root,
    )

    if not args.json:
        for line in render_preview(config, existing, config_path, age_days):
            print(line)

    def emit(message: str) -> None:
        """Che do --json: stdout chi chua JSON, moi thong bao khac di ra stderr."""
        print(message, file=sys.stderr if args.json else sys.stdout)

    if problems:
        emit("\n[scout] CONFIG KHONG HOP LE, khong ghi:")
        for problem in problems:
            emit(f"  - {problem}")
        return 1

    if args.json:
        print(json.dumps(config, ensure_ascii=False, indent=2))

    if not args.write:
        emit("\n[scout] Chua ghi (che do preview). Them --write de ghi "
             f"{config_path} (ban cu se duoc backup tu dong).")
        return 0

    report = safe_writer.write_safely(
        config_path,
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        root=root, run_id=run_id, reason="scout:write-config",
    )
    emit(f"\n[scout] Da ghi : {report['target']}")
    emit(f"[scout] Backup : {report['backup']['backup_path'] if report['backup'] else '-'}"
         f"  (existed={report['backup']['existed'] if report['backup'] else '-'})")
    emit(f"[scout] sha256 : {(report['sha256'] or '')[:16]}")
    if verify_results:
        passed = sum(1 for r in verify_results.values() if r["ok"])
        emit(f"[scout] Verify : {passed}/{len(verify_results)} vai tro phan hoi OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
