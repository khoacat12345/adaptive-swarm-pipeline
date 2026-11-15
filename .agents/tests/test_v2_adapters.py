#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 4 (F04) acceptance tests for runtime protocol and adapters.

Run directly:

    python .agents/tests/test_v2_adapters.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2 import adapters as adapter_mod  # noqa: E402
from v2 import runtime as runtime_mod  # noqa: E402
from v2.adapters import (  # noqa: E402
    CapabilityRejected,
    ClaudeCodeRuntime,
    GenericCliRuntime,
    MockRuntime,
    OpenCodeRuntime,
    assert_capabilities,
    build_runtime_registry,
    default_runtimes,
    select_runtime,
)
from v2.doctor import _check_runtimes  # noqa: E402
from v2.runtime import (  # noqa: E402
    AgentRuntime,
    RuntimeCapabilities,
    RuntimeRegistry,
    RuntimeResult,
    TaskRuntime,
)
from v2 import swarm_cli  # noqa: E402


class RuntimeProtocolTests(unittest.TestCase):
    def test_agent_runtime_exposes_task_lifecycle_methods(self) -> None:
        for method in ("start_task", "send_message", "wait", "cancel"):
            self.assertTrue(
                hasattr(AgentRuntime, method),
                f"AgentRuntime is missing {method}",
            )

    def test_task_runtime_protocol_is_runtime_checkable(self) -> None:
        class FakeTaskRuntime:
            name = "fake"

            def capabilities(self):
                return RuntimeCapabilities()

            def health(self):
                return {"ok": True}

            def start_task(self, task, **kwargs):
                return "h1"

            def send_message(self, handle, message):
                return {"queued_messages": 1}

            def wait(self, handle, *, timeout=None):
                return RuntimeResult("ok")

            def cancel(self, handle, *, reason=""):
                return {"state": "CANCELLED"}

        self.assertIsInstance(FakeTaskRuntime(), TaskRuntime)

    def test_runtime_capabilities_include_required_flags(self) -> None:
        caps = RuntimeCapabilities(
            headless=True,
            tools=True,
            json_output=True,
            streaming=True,
            reasoning=True,
            max_parallel=4,
        )
        payload = caps.as_dict()
        for key in (
            "headless", "tools", "json_output", "streaming",
            "reasoning", "max_parallel",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["max_parallel"], 4)

    def test_runtime_result_validation(self) -> None:
        self.assertEqual(RuntimeResult("ok").validate().status, "ok")
        with self.assertRaises(Exception):
            RuntimeResult("nonsense").validate()


class RuntimeRegistryTests(unittest.TestCase):
    def test_register_get_and_duplicate_rejection(self) -> None:
        registry = RuntimeRegistry()
        runtime = MockRuntime()
        registry.register(runtime)
        self.assertIs(registry.get("mock"), runtime)
        self.assertEqual(registry.names(), ["mock"])
        with self.assertRaises(Exception):
            registry.register(MockRuntime())
        with self.assertRaises(Exception):
            registry.get("missing")

    def test_requiring_filters_capabilities(self) -> None:
        registry = RuntimeRegistry()
        registry.register(MockRuntime())
        registry.register(
            GenericCliRuntime(
                ["tool"],
                capabilities=RuntimeCapabilities(
                    headless=True, tools=False, reasoning=True, messaging=True,
                ),
            )
        )
        names = [item.name for item in registry.requiring(tools=True)]
        self.assertEqual(names, ["mock"])
        names = [item.name for item in registry.requiring(reasoning=True)]
        self.assertEqual(names, ["generic-cli", "mock"])

    def test_registry_health_contains_each_runtime(self) -> None:
        registry = RuntimeRegistry()
        registry.register(MockRuntime())
        health = registry.health()
        self.assertIn("mock", health)
        self.assertTrue(health["mock"]["ok"])

    def test_select_runtime_by_name_and_tier(self) -> None:
        registry = RuntimeRegistry()
        registry.register(MockRuntime())
        selected = select_runtime(registry, runtime_name="mock", tier="E3")
        self.assertEqual(selected.name, "mock")

    def test_select_runtime_rejects_missing_capability(self) -> None:
        registry = RuntimeRegistry()
        registry.register(
            GenericCliRuntime(
                ["tool"],
                capabilities=RuntimeCapabilities(
                    headless=True, tools=True, reasoning=False, messaging=True,
                ),
            )
        )
        with self.assertRaises(CapabilityRejected):
            select_runtime(registry, runtime_name="generic-cli", tier="E4")

    def test_assert_capabilities_rejects_non_reasoning_model_for_e4(self) -> None:
        runtime = MockRuntime()
        with self.assertRaises(CapabilityRejected):
            assert_capabilities(
                runtime,
                tier="E4",
                model_info={"capabilities": {"reasoning": 0}},
                model="no-reasoning",
            )
        # The same runtime is valid for E3.
        self.assertIsNotNone(assert_capabilities(runtime, tier="E3"))


class MockRuntimeTests(unittest.TestCase):
    def test_mock_runtime_deterministic_output_and_messages(self) -> None:
        runtime = MockRuntime(
            script={
                "T1": {
                    "files": {"src/a.py": "answer\n"},
                    "usage": {"requests": 2, "cost_usd": 0.01},
                }
            },
            latency=0.0,
        )
        handle = runtime.start_task(
            {"id": "T1", "target_files": ["src/a.py"]}, tier="E3"
        )
        queued = runtime.send_message(handle, "please continue")
        self.assertEqual(queued["queued_messages"], 1)
        result = runtime.wait(handle)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.files, {"src/a.py": "answer\n"})
        self.assertEqual(result.usage["requests"], 2)
        self.assertEqual(runtime.received[0]["messages"], ["please continue"])

    def test_mock_runtime_failure_is_normalized(self) -> None:
        runtime = MockRuntime(fail=True)
        handle = runtime.start_task({"id": "T1"}, tier="E3")
        result = runtime.wait(handle)
        self.assertEqual(result.status, "failed")
        self.assertIn("mock runtime failure", result.error or "")

    def test_mock_runtime_cancellation(self) -> None:
        runtime = MockRuntime()
        handle = runtime.start_task({"id": "T1"}, tier="E3")
        cancelled = runtime.cancel(handle, reason="operator stop")
        self.assertEqual(cancelled["state"], "CANCELLED")
        result = runtime.wait(handle)
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.error)

    def test_mock_runtime_unknown_handle(self) -> None:
        runtime = MockRuntime()
        with self.assertRaises(Exception):
            runtime.wait("missing")


class CliRuntimeTests(unittest.TestCase):
    def test_generic_cli_runtime_uses_runner_and_parses_files(self) -> None:
        calls = []

        def runner(argv, *, cwd, timeout):
            calls.append((argv, cwd, timeout))
            return {
                "returncode": 0,
                "stdout": '{"files": {"src/a.py": "ok\\n"}}',
                "stderr": "",
            }

        runtime = GenericCliRuntime(
            ["tool", "--prompt", "{prompt}"], runner=runner
        )
        handle = runtime.start_task(
            {"id": "T1", "objective": "write"}, tier="E3"
        )
        result = runtime.wait(handle)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.files, {"src/a.py": "ok\n"})
        self.assertEqual(len(calls), 1)
        self.assertIn("--prompt", calls[0][0])

    def test_generic_cli_failure_captures_stderr(self) -> None:
        runtime = GenericCliRuntime(
            ["tool"],
            runner=lambda argv, **kwargs: {
                "returncode": 2,
                "stdout": "",
                "stderr": "bad input",
            },
        )
        handle = runtime.start_task({"id": "T1"}, tier="E3")
        result = runtime.wait(handle)
        self.assertEqual(result.status, "failed")
        self.assertIn("bad input", result.error or "")

    def test_opencode_and_claude_commands_include_model_and_prompt(self) -> None:
        opencode = OpenCodeRuntime(executable="opencode")
        argv = opencode.build_command("hello", model="provider/model")
        self.assertEqual(argv[:2], ["opencode", "run"])
        self.assertIn("provider/model", argv)
        self.assertIn("hello", argv)

        claude = ClaudeCodeRuntime(executable="claude")
        argv = claude.build_command("hello", model="provider/model")
        self.assertEqual(argv[:2], ["claude", "-p"])
        self.assertIn("provider/model", argv)
        self.assertIn("hello", argv)


class DoctorAndCliTests(unittest.TestCase):
    def test_doctor_runtime_check_includes_mock(self) -> None:
        rows = _check_runtimes(Path("."))
        self.assertTrue(any(row.name == "runtime mock" and row.ok for row in rows))
        self.assertTrue(any(row.name == "runtimes" for row in rows))

    def test_default_registry_has_builtin_runtimes(self) -> None:
        registry = build_runtime_registry()
        names = registry.names()
        self.assertIn("mock", names)
        self.assertIn("opencode", names)
        self.assertIn("claude-code", names)

    def test_default_runtimes_are_protocol_compatible(self) -> None:
        for runtime in default_runtimes():
            self.assertIsInstance(runtime, TaskRuntime)

    def test_run_cli_accepts_runtime_flag(self) -> None:
        parser = swarm_cli._build_parser()
        args = parser.parse_args(["run", "--runtime", "mock"])
        self.assertEqual(args.runtime, "mock")


if __name__ == "__main__":
    unittest.main(verbosity=2)
