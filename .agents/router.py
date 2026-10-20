#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Router theo LOAI CONG VIEC chung (Phase 5).

Khong hardcode ten du an hay domain. Router chi doc tin hieu DO DUOC:

  * so file dich / do sau phu thuoc
  * tu khoa rui ro (auth, token, migration, payment, secret...)
  * kich thuoc context (token)
  * modality (co anh / UI)
  * so lan verify fail, reviewer reject

Tu do chon chuoi [main, escalate] cho 9 loai viec W0-W8. Duy tri nguyen tac:
main va escalate phai KHAC HO (chong diem mu), va moi nang cap deu co ly do.

CLI:
    python .agents/router.py --order scratch/order.json
    python .agents/router.py --order scratch/order.json --json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Model ID dung trong he thong (opencode free + opencode-go).
MODELS = {
    "deepseek-flash-free": "opencode/deepseek-v4.1-flash-free",
    "glm-flash-free": "opencode/glm-5.3-flash-free",
    "deepseek-v4-pro": "opencode-go/deepseek-v4-pro",
    "kimi-k2.7-code": "opencode-go/kimi-k2.7-code",
    "glm-5.3": "opencode-go/glm-5.3",
    "claude-haiku": "opencode-go/claude-haiku-5-5",
    "qwen3.8-max": "opencode-go/qwen3.8-max",
    "kimi-k3": "opencode-go/kimi-k3",
    "gpt-6-luna": "opencode-go/gpt-6-luna",
    "minimax-m3": "opencode-go/minimax-m3",
}

FAMILY = {
    "opencode/deepseek-v4.1-flash-free": "deepseek",
    "opencode/glm-5.3-flash-free": "glm",
    "opencode-go/deepseek-v4-pro": "deepseek",
    "opencode-go/kimi-k2.7-code": "kimi",
    "opencode-go/glm-5.3": "glm",
    "opencode-go/claude-haiku-5-5": "anthropic",
    "opencode-go/qwen3.8-max": "qwen",
    "opencode-go/kimi-k3": "kimi",
    "opencode-go/gpt-6-luna": "openai",
    "opencode-go/minimax-m3": "minimax",
}

WORK_TYPES = {
    "W0": "phan-loai/dinh-tuyen",
    "W1": "sua-nho-1-file",
    "W2": "logic-phuc-tap/da-file",
    "W3": "kiem-toan/bao-mat",
    "W4": "thiet-ke/chia-viec",
    "W5": "viet-test/kiem-chung",
    "W6": "giao-dien/multimodal",
    "W7": "tai-lieu/ngu-canh-lon",
    "W8": "trong-tai/bat-dong",
}

# main/escalate: uu tien ngon-bo-re (free -> re -> trung -> dat).
ROUTES = {
    "W0": {"main": MODELS["glm-flash-free"], "escalate": None,
           "why": "rules-first, 0 request; chi dung model khi rules moi ho"},
    "W1": {"main": MODELS["deepseek-flash-free"], "escalate": MODELS["glm-flash-free"],
           "why": "sua nho: free, do 10/10, it token nhat"},
    "W2": {"main": MODELS["deepseek-v4-pro"], "escalate": MODELS["kimi-k2.7-code"],
           "why": "LiveCodeBench #1; escalate model code long-horizon"},
    "W3": {"main": MODELS["glm-5.3"], "escalate": MODELS["claude-haiku"],
           "why": "CyberGym #1; escalate reviewer khac ho de doc lap"},
    "W4": {"main": MODELS["qwen3.8-max"], "escalate": MODELS["glm-5.3"],
           "why": "reasoning 87.8 + long-horizon; escalate reasoning khac ho"},
    "W5": {"main": MODELS["kimi-k2.7-code"], "escalate": MODELS["glm-5.3"],
           "why": "SWE 78.2 + long-horizon; escalate de bat lech logic"},
    "W6": {"main": MODELS["gpt-6-luna"], "escalate": MODELS["qwen3.8-max"],
           "why": "re + quota lon gap 38x; escalate reasoning khi UI co logic"},
    "W7": {"main": MODELS["claude-haiku"], "escalate": MODELS["minimax-m3"],
           "why": "nhanh/re; escalate vi 1M context"},
    "W8": {"main": MODELS["qwen3.8-max"], "escalate": MODELS["glm-5.3"],
           "why": "trong tai khi bat dong: reasoning cao nhat"},
}

RISK_WORDS = (
    "auth", "token", "secret", "password", "credential", "migration", "migrate",
    "payment", "billing", "crypto", "encrypt", "decrypt", "permission", "role",
    "sql", "injection", "traversal", "sandbox", "rpc", "session", "oauth",
)
TEST_WORDS = ("test", "pytest", "unittest", "fixture", "assert", "kiem chung", "kịch bản")
PLAN_WORDS = ("plan", "chia viec", "kien truc", "architecture", "decompose", "roadmap")
UI_EXT = (".tsx", ".jsx", ".css", ".scss", ".html", ".vue", ".svelte", ".sass")
DOC_EXT = (".md", ".rst", ".txt", ".adoc")
TEST_PREFIX = ("test_", "tests/", "spec_", "_test.")


def _blob(work_order) -> str:
    parts = [
        getattr(work_order, "objective", "") or "",
        getattr(work_order, "boundaries", "") or "",
        getattr(work_order, "input_context", "") or "",
        " ".join(getattr(work_order, "target_files", []) or []),
        " ".join(getattr(work_order, "sandbox_files", []) or []),
    ]
    return " ".join(parts).lower()


def classify_work(work_order, *, context_tokens: int = 0, has_image: bool = False,
                  failures: int = 0, reviewer_rejected: bool = False,
                  needs_plan: bool = False) -> tuple[str, dict]:
    """Tra (work_type, signals). Thu tu uu tien duoc ghi trong docstring."""
    targets = [str(item) for item in (getattr(work_order, "target_files", []) or [])]
    level = int(getattr(work_order, "level", 2) or 0)
    blob = _blob(work_order)
    signals = {
        "files": len(targets),
        "level": level,
        "context_tokens": int(context_tokens or 0),
        "has_image": bool(has_image),
        "failures": int(failures or 0),
        "reviewer_rejected": bool(reviewer_rejected),
        "risk": next((word for word in RISK_WORDS if word in blob), None),
        "test": next((word for word in TEST_WORDS if word in blob), None),
        "plan": next((word for word in PLAN_WORDS if word in blob), None),
        "ui": next((ext for ext in UI_EXT if any(t.lower().endswith(ext) for t in targets)), None),
        "docs": next((ext for ext in DOC_EXT if any(t.lower().endswith(ext) for t in targets)), None),
        "is_test_file": any(t.lower().startswith(TEST_PREFIX) or "/test_" in t.lower()
                            for t in targets),
    }

    if signals["reviewer_rejected"] or signals["failures"] >= 2:
        return "W8", signals
    if signals["has_image"] or signals["ui"]:
        return "W6", signals
    if signals["risk"]:
        return "W3", signals
    if signals["is_test_file"] or signals["test"]:
        return "W5", signals
    if needs_plan or (signals["plan"] and not targets):
        return "W4", signals
    if not targets:
        return "W0", signals
    if signals["context_tokens"] > 100_000 or signals["docs"]:
        return "W7", signals
    if signals["files"] >= 2 or level >= 2:
        return "W2", signals
    return "W1", signals


def route(work_order, **signals) -> dict:
    """Tra ke hoach dinh tuyen: work_type, main, escalate, chain, ly do."""
    work_type, detected = classify_work(work_order, **signals)
    entry = ROUTES[work_type]
    chain = [model for model in (entry["main"], entry["escalate"]) if model]
    return {
        "work_type": work_type,
        "label": WORK_TYPES[work_type],
        "main": entry["main"],
        "escalate": entry["escalate"],
        "chain": chain,
        "why": entry["why"],
        "signals": detected,
        "family": [FAMILY.get(model, "?") for model in chain],
    }


def validate_roles(routes: dict | None = None) -> list[str]:
    """Kiem tra bat bien: model hop le, main != escalate, vai kiem toan khac ho."""
    routes = routes or ROUTES
    problems: list[str] = []
    audit = {"W3", "W5", "W8"}
    for name, entry in routes.items():
        main, escalate = entry.get("main"), entry.get("escalate")
        for label, model in (("main", main), ("escalate", escalate)):
            if not model:
                continue
            if not (str(model).startswith("opencode/")
                    or str(model).startswith("opencode-go/")):
                problems.append(f"{name}.{label}: provider khong phai opencode: {model}")
            if str(model) not in FAMILY:
                problems.append(f"{name}.{label}: model chua khai bao family: {model}")
        if escalate and main == escalate:
            problems.append(f"{name}: main trung escalate")
        if name in audit and escalate:
            if FAMILY.get(main) == FAMILY.get(escalate):
                problems.append(f"{name}: vai kiem toan phai khac ho "
                                f"({FAMILY.get(main)} == {FAMILY.get(escalate)})")
    return problems


def _cli(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Route mot Work Order theo W0-W8.")
    parser.add_argument("--order", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true", help="chi validate bang dinh tuyen")
    args = parser.parse_args(argv)

    if args.check:
        problems = validate_roles()
        if problems:
            print("ROUTER KHONG HOP LE:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("ROUTER HOP LE: main/escalate khac ho, model hop le.")
        return 0

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import work_order as work_order_module
    if not args.order:
        print("can --order (hoac dung --check)", file=sys.stderr)
        return 2
    data = json.loads(Path(args.order).read_text(encoding="utf-8"))
    order = work_order_module.WorkOrder.from_dict(data)
    plan = route(order)
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(f"{plan['work_type']} - {plan['label']}")
        print(f"  main    : {plan['main']}")
        print(f"  escalate: {plan['escalate']}")
        print(f"  chain   : {' -> '.join(plan['chain'])}")
        print(f"  ly do   : {plan['why']}")
        print(f"  signals : {plan['signals']}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
