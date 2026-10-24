#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 5 acceptance - LLM adapter boundary and DI."""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
for _path in (AGENTS_DIR, AGENTS_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import workers  # noqa: E402
import work_order as work_order_module  # noqa: E402
from providers.base_adapter import AgentReply, LLMAdapter  # noqa: E402
from providers.mock_adapter import MockAdapter  # noqa: E402
from providers.opencode_adapter import OpenCodeAdapter  # noqa: E402
from providers.registry import ProviderRegistry  # noqa: E402

PY = f'"{sys.executable}"'


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


def order():
    return work_order_module.create(
        task_id="ADAPTER1", title="adapter", objective="Sua ham add",
        boundaries="Chi sua calc.py", input_context="calc.py",
        acceptance_criteria="check.py exit 0", verify_command=f"{PY} check.py",
        target_files=["calc.py"], sandbox_files=["calc.py", "check.py"],
        level=2, max_rounds=1,
    )


def test_contract(checks: Checks) -> None:
    reply = AgentReply("hello", {"x": 1}, "m", {"in_tokens": 2}, "raw")
    checks.check("adapter: AgentReply fields",
                 reply.text == "hello" and reply.data == {"x": 1}
                 and reply.model == "m" and reply.usage["in_tokens"] == 2,
                 reply)
    checks.check("adapter: LLMAdapter is abstract", "complete" in LLMAdapter.__abstractmethods__,
                 LLMAdapter.__abstractmethods__)
    try:
        LLMAdapter()
        abstract = False
    except TypeError:
        abstract = True
    checks.check("adapter: cannot instantiate base", abstract, "base instantiated")


def test_mock(checks: Checks) -> None:
    mock = MockAdapter([json.dumps({"verdict": "approve", "reasons": []}), "plain"])
    first = mock.complete([{"role": "user", "content": "one"}])
    second = mock.complete([{"role": "user", "content": "two"}], {"type": "object"})
    checks.check("mock: json data parsed", first.data == {"verdict": "approve", "reasons": []}, first)
    checks.check("mock: plain text", second.text == "plain" and second.data is None, second)
    checks.check("mock: calls/history", mock.calls == 2 and len(mock.history) == 2,
                 (mock.calls, mock.history))
    checks.check("mock: deterministic last response", mock.complete([]).text == "plain",
                 mock.complete([]).text)
    mock.reset()
    checks.check("mock: reset", mock.calls == 0 and not mock.history, mock.calls)


def test_opencode(checks: Checks) -> None:
    calls = []

    def runner(binary, model, prompt, *, agent=None, cwd=None, timeout=300):
        calls.append({"binary": binary, "model": model, "prompt": prompt,
                      "agent": agent, "cwd": cwd, "timeout": timeout})
        return json.dumps({"format": "whole", "content": "x"}), {"in_tokens": 3}

    adapter = OpenCodeAdapter("fake-binary", model="model-x", runner=runner)
    reply = adapter.complete([{"role": "user", "content": "fix it"}], {"type": "object"})
    checks.check("opencode: result text", "format" in reply.text, reply)
    checks.check("opencode: result data", reply.data and reply.data.get("content") == "x", reply)
    checks.check("opencode: usage", reply.usage.get("in_tokens") == 3, reply)
    checks.check("opencode: runner args", calls and calls[0]["binary"] == "fake-binary"
                 and calls[0]["model"] == "model-x", calls)
    checks.check("opencode: schema instruction", "JSON" in calls[0]["prompt"], calls[0]["prompt"])


def test_registry(checks: Checks) -> None:
    registry = ProviderRegistry()
    checks.check("registry: built-ins", {"opencode", "mock"} <= set(registry.names()),
                 registry.names())
    mock = registry.build("mock", responses=["ok"])
    checks.check("registry: build mock", isinstance(mock, MockAdapter), type(mock))
    old = os.environ.get("SWARM_PROVIDER")
    try:
        os.environ["SWARM_PROVIDER"] = "mock"
        checks.check("registry: env provider", isinstance(registry.from_env(), MockAdapter),
                     registry.from_env())
    finally:
        if old is None:
            os.environ.pop("SWARM_PROVIDER", None)
        else:
            os.environ["SWARM_PROVIDER"] = old


def test_workers_di(checks: Checks) -> None:
    patch_text = json.dumps({"format": "whole", "content": "def add(a, b):\n    return a + b\n"})
    coder = workers.OpenCodeCoder("model-x", adapter=MockAdapter([patch_text]))
    patch = coder.patch(order(), current_content="def add(a,b):\n    return a-b\n",
                        feedback=None, round_index=1)
    checks.check("di: coder uses mock adapter", patch.get("format") == "whole"
                 and patch.get("content", "").endswith("return a + b\n"), patch)
    checks.check("di: coder did not call CLI", coder.adapter.calls == 1, coder.adapter.calls)

    review_text = json.dumps({"verdict": "approve", "reasons": []})
    reviewer = workers.RoleReviewer("security", "model-x", adapter=MockAdapter([review_text]))
    result = reviewer.review(order(), diff="+x", new_content="x", old_content="",
                             level=2, sink=None)
    checks.check("di: reviewer uses mock adapter", result.get("verdict") == "approve", result)
    checks.check("di: reviewer adapter call", reviewer.adapter.calls == 1, reviewer.adapter.calls)

    built = workers.build_reviewer("llm", model="model-x", adapter=MockAdapter([review_text]))
    checks.check("di: build_reviewer forwards adapter", isinstance(built.adapter, MockAdapter),
                 type(getattr(built, "adapter", None)))


def test_core_ast(checks: Checks) -> None:
    forbidden: list[str] = []
    files = sorted((AGENTS_DIR / "core").glob("*.py"))
    checks.check("ast: core has files", bool(files), files)
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                if name == "subprocess" or name.startswith("subprocess."):
                    forbidden.append(f"{path.name}: import {name}")
                if "opencode" in name.lower():
                    forbidden.append(f"{path.name}: import {name}")
    checks.check("ast: core has no subprocess/opencode imports", not forbidden, forbidden)
    providers = ["base_adapter.py", "opencode_adapter.py", "mock_adapter.py"]
    for name in providers:
        checks.check(f"providers: {name}", (AGENTS_DIR / "providers" / name).exists(), name)


def main() -> int:
    checks = Checks()
    for name, test in (
        ("contract", test_contract), ("mock", test_mock),
        ("opencode", test_opencode), ("registry", test_registry),
        ("workers DI", test_workers_di), ("core AST", test_core_ast),
    ):
        print(f"[{name}] ...")
        test(checks)
    print("")
    print(f"ADAPTER: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
