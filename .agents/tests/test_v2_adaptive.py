#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adaptive toolkit acceptance (P0-P2 lightweight primitives)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2.adaptive import (  # noqa: E402
    Action,
    ActionPolicy,
    ContextPacker,
    GatewayModel,
    Lesson,
    LessonStore,
    PromptOptimizer,
    RepoMapper,
    adaptive_event,
    apply_mode,
    run_benchmark,
    select_gateway_model,
    select_speaker,
)

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "v2_adaptive_tests"


class Checks:
    def __init__(self):
        self.total = 0
        self.failures = []

    def check(self, name, condition, detail=""):
        self.total += 1
        if not condition:
            self.failures.append(f"{name}: {detail}" if detail else name)

    @property
    def passed(self):
        return self.total - len(self.failures)


def rmtree(path):
    def onerror(func, target, exc_info):
        try:
            Path(target).chmod(0o700)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=onerror)


def make_project() -> Path:
    root = TEMP_ROOT / "project"
    rmtree(root)
    (root / "src").mkdir(parents=True)
    (root / "src" / "calc.py").write_text(
        "class Calculator:\n"
        "    def add(self, a, b):\n"
        "        return a - b\n"
        "\n"
        "def helper(value):\n"
        "    return value\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("demo\n", encoding="utf-8")
    return root


def test_repo_map(checks):
    root = make_project()
    result = RepoMapper(root, max_tokens=300).build("fix Calculator.add arithmetic")
    checks.check("repo-map: finds relevant symbols",
                 "Calculator" in result.text and "add" in result.text)
    checks.check("repo-map: reports files", result.files >= 1)
    checks.check("repo-map: respects token budget", result.approx_tokens <= 320,
                 str(result.approx_tokens))
    checks.check("repo-map: excludes contents", "return a - b" not in result.text)


def test_aci(checks):
    root = make_project()
    policy = ActionPolicy(root)
    read = policy.execute(Action("read", {"path": "src/calc.py"}))
    checks.check("aci: read works", read.status == "ok" and "Calculator" in read.output)
    listing = policy.execute(Action("list", {"path": "."}))
    checks.check("aci: list works", listing.status == "ok" and "src" in listing.output)
    search = policy.execute(Action("search", {"query": "Calculator"}))
    checks.check("aci: search works", search.status == "ok" and "calc.py" in search.output)
    escape = policy.execute(Action("read", {"path": "../outside"}))
    checks.check("aci: path escape blocked", escape.status == "failed")
    shell = policy.execute(Action("bash", {"command": "whoami"}))
    checks.check("aci: arbitrary action blocked", shell.status == "blocked")
    verify = policy.execute(Action("verify", {"command": 'python -c "print(1)"'}))
    checks.check("aci: guarded verify works", verify.status == "ok", verify.output)


def test_lessons(checks):
    rmtree(TEMP_ROOT / "lessons")
    store = LessonStore(TEMP_ROOT / "lessons" / "lessons.jsonl")
    store.record(Lesson(
        signature="timeout reading large file",
        cause="context too large",
        fix="split file or use repo map",
        files=["src/calc.py"],
        tags=["timeout", "context"],
    ))
    store.record(Lesson(
        signature="missing verify",
        cause="no machine check",
        fix="add verify_command",
        files=["README.md"],
        tags=["verify"],
    ))
    hits = store.query(files=["src/calc.py"], text="timeout")
    checks.check("lessons: retrieves relevant lesson", hits and hits[0].signature.startswith("timeout"))
    checks.check("lessons: hints are compact", "split file" in store.hints(files=["src/calc.py"]))
    reloaded = LessonStore(TEMP_ROOT / "lessons" / "lessons.jsonl")
    checks.check("lessons: survives reload", len(reloaded.lessons) == 2)


def test_context(checks):
    packer = ContextPacker(token_budget=80)
    packer.add("current phase", label="working")
    packer.add("recent failure lesson", tier="recall", label="memory")
    packer.add("constitution principles", tier="archival", label="constitution")
    packed = packer.pack()
    checks.check("context: working first", packed["text"].find("## working") < packed["text"].find("## recall"))
    checks.check("context: budget respected", packed["approx_tokens"] <= 90)


def test_events(checks):
    event = adaptive_event(
        run_id="run", event_type="observation", phase_id="P1",
        action="read", observation="ok", status="pass",
    )
    checks.check("events: schema version", event["schema_version"] == "2.1-adaptive")
    checks.check("events: action is recorded", event["action"] == "read")
    failed = False
    try:
        adaptive_event(run_id="run", event_type="unknown")
    except ValueError:
        failed = True
    checks.check("events: unknown type fails", failed)


def test_optimizer(checks):
    store = LessonStore(TEMP_ROOT / "lessons" / "lessons.jsonl")
    proposal = PromptOptimizer(store).propose(role="coder", files=["src/calc.py"])
    checks.check("optimizer: uses lessons", "timeout" in proposal.hint)
    checks.check("optimizer: marks baseline requirement", "benchmark" in proposal.baseline_note)


def test_gateway(checks):
    candidates = [
        GatewayModel("paid/x", "deepseek", free=False, estimated_cost=2.0),
        GatewayModel("free/x", "glm", free=True, estimated_cost=0.0),
        GatewayModel("bad/x", "glm", free=True, healthy=False),
    ]
    choice = select_gateway_model(candidates, exclude_families={"deepseek"})
    checks.check("gateway: free healthy model selected", choice.model == "free/x")
    failed = False
    try:
        select_gateway_model(candidates, exclude_families={"deepseek", "glm"})
    except ValueError:
        failed = True
    checks.check("gateway: no match fails closed", failed)


def test_modes(checks):
    mode = apply_mode("strict-security", {"max_phases": 2})
    checks.check("modes: strict preset", mode["network_for_verify"] is False and mode["test_integrity"])
    checks.check("modes: preserves base", mode["max_phases"] == 2)
    failed = False
    try:
        apply_mode("unknown")
    except ValueError:
        failed = True
    checks.check("modes: unknown fails", failed)


def test_speaker(checks):
    role = select_speaker(
        findings=[{"role": "security", "verdict": "reject", "severity": "blocker"}],
        available=["security", "qa", "docs"],
    )
    checks.check("speaker: blocker role selected", role == "security")
    fallback = select_speaker(findings=[], available=["qa", "docs"])
    checks.check("speaker: fallback works", fallback == "qa")


def test_benchmark(checks):
    root = make_project()
    results = run_benchmark(
        [{"name": "print", "command": 'python -c "print(1)"'}],
        cwd=root,
    )
    checks.check("benchmark: executes a guarded command", results and results[0].passed)
    blocked = run_benchmark(
        [{"name": "danger", "command": "rm -rf /"}],
        cwd=root,
    )
    checks.check("benchmark: destructive command blocked",
                 blocked and blocked[0].exit_code == 126)


def main():
    rmtree(TEMP_ROOT)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    checks = Checks()
    tests = [
        ("repo map", test_repo_map),
        ("ACI", test_aci),
        ("failures", test_lessons),
        ("context", test_context),
        ("events", test_events),
        ("optimizer", test_optimizer),
        ("gateway", test_gateway),
        ("modes", test_modes),
        ("speaker", test_speaker),
        ("benchmark", test_benchmark),
    ]
    for index, (name, fn) in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {name} ...")
        fn(checks)
    print("")
    print(f"V2 ADAPTIVE: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    rmtree(TEMP_ROOT)
    return 0 if checks.passed == checks.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
