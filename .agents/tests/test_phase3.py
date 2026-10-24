#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 3 - fault tolerance / security / semi-dynamic orchestration tests.

Bon nhom nghiem thu (Definition of Done):

  1. Fallback        : model dau chet -> tu chuyen model 2, task van xong (exit 0).
  2. Secret Scan     : file chua key gia -> bi chan/redact truoc khi gui model.
  3. Plan Validator  : plan 3 nhanh song song / thieu verify_command -> bi tu choi.
  4. Khong hoi quy   : chay lai toan bo smoke suite Phase 1 -> 100% PASS.

Kem theo: budget cap, session policy, cost report (Task 3.4 / 3.7 / 3.8).

    python .agents/tests/test_phase3.py            # tat ca
    python .agents/tests/test_phase3.py --fast     # bo qua smoke suite (~60s)
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
TESTS_DIR = Path(__file__).resolve().parent
for _path in (AGENTS_DIR, TESTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import cost_report                     # noqa: E402
import fallback                        # noqa: E402
import plan_validator                  # noqa: E402
import recipe_c                        # noqa: E402
import secret_scan                     # noqa: E402
import work_order as work_order_module  # noqa: E402
import workers                         # noqa: E402

PY = f'"{sys.executable}"'
FAKE_KEY = "sk-ant-api03-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"
TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "phase3_tests"

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
CHECK_PY = "from calc import add\nassert add(2, 3) == 5\nprint('OK')\n"


class Checks:
    def __init__(self) -> None:
        self.total = 0
        self.failures: list[str] = []

    def check(self, name: str, condition, detail: str = "") -> bool:
        self.total += 1
        passed = bool(condition)
        if not passed:
            self.failures.append(f"{name}: {detail}" if detail else name)
        return passed

    @property
    def passed(self) -> int:
        return self.total - len(self.failures)


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------
class DeadCoder(workers.Coder):
    """Model chet: gia lap HTTP 429 / het quota."""

    def __init__(self, kind: str = "quota") -> None:
        super().__init__()
        self.kind = kind
        self.name = "dead"

    def patch(self, work_order, *, current_content, feedback, round_index):
        self.calls += 1
        raise workers.WorkerError("gia lap 429 Too Many Requests", kind=self.kind)


class StaticCoder(workers.Coder):
    """Model song: sua dung ngay lan dau."""

    def __init__(self, content: str) -> None:
        super().__init__()
        self.content = content
        self.name = "static"

    def patch(self, work_order, *, current_content, feedback, round_index):
        self.calls += 1
        return {"format": "whole", "content": self.content}


def make_project(name: str, *, calc: str = BUGGY) -> Path:
    root = TEMP_ROOT / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    (root / "calc.py").write_text(calc, encoding="utf-8")
    (root / "check.py").write_text(CHECK_PY, encoding="utf-8")
    return root


def make_order(**overrides) -> work_order_module.WorkOrder:
    data = {
        "task_id": "P3",
        "title": "phase 3",
        "objective": "Sua ham add trong calc.py de tra ve tong a + b thay vi hieu",
        "boundaries": "Chi duoc sua calc.py, khong tao file moi",
        "input_context": "calc.py chua ham add bi sai dau",
        "acceptance_criteria": "check.py chay exit 0, add(2, 3) == 5",
        "verify_command": f"{PY} check.py",
        "target_files": ["calc.py"],
        "sandbox_files": ["calc.py", "check.py"],
        "level": 1,
        "max_rounds": 2,
    }
    data.update(overrides)
    return work_order_module.WorkOrder(**data)


# --------------------------------------------------------------------------
# 1. Fallback chain + passive health
# --------------------------------------------------------------------------
def test_fallback(checks: Checks) -> None:
    root = make_project("fallback")
    health_path = root / "health.json"
    health = fallback.HealthRegistry(health_path)

    def factory(model: str):
        return DeadCoder() if model == "fake/dead-model" else StaticCoder(FIXED)

    coder = fallback.FallbackCoder(
        ["fake/dead-model", "scripted/ok-model"], factory=factory, health=health)
    order = make_order()
    report = recipe_c.run(order, coder=coder, reviewer=None, root=root,
                          run_id="p3-fallback-1", log=True)

    checks.check("fallback: task van hoan thanh (exit 0)", report["status"] == "applied",
                 str(report["status"]))
    checks.check("fallback: file duoc sua dung", (root / "calc.py").read_text(encoding="utf-8") == FIXED)
    checks.check("fallback: 1 lan that bai duoc ghi nhan",
                 report["fallback"]["failures"] == 1,
                 json.dumps(report["fallback"]["attempts"]))
    checks.check("fallback: model 2 la model da cuu task",
                 report["fallback"]["attempts"]
                 and report["fallback"]["attempts"][-1]["model"] == "scripted/ok-model")

    cooled, remaining = health.cooled("fake/dead-model")
    checks.check("health: model chet bi cooldown", cooled and remaining > 0, f"{remaining}s")
    saved = json.loads(health_path.read_text(encoding="utf-8"))
    entry = (saved.get("entries") or {}).get("fake/dead-model") or {}
    checks.check("health: ghi du error_type=quota", entry.get("last_error_type") == "quota",
                 json.dumps(entry)[:200])
    checks.check("health: ghi du latency", isinstance(entry.get("last_latency_ms"), int))

    # Lan chay thu 2: model dang cooldown phai bi bo qua, khong dot request.
    # Dung du an moi (file con loi) de khong bi "no-op" vi file da duoc sua lan 1.
    root2 = make_project("fallback-2")
    coder2 = fallback.FallbackCoder(
        ["fake/dead-model", "scripted/ok-model"], factory=factory, health=health)
    report2 = recipe_c.run(make_order(), coder=coder2, reviewer=None, root=root2,
                           run_id="p3-fallback-2", log=False)
    checks.check("health: lan 2 bo qua model cooldown",
                 report2["status"] == "applied"
                 and report2["fallback"]["skipped"]
                 and report2["fallback"]["skipped"][0]["model"] == "fake/dead-model",
                 json.dumps(report2["fallback"]["skipped"]))
    checks.check("health: lan 2 khong dot request cho model chet",
                 coder2.calls == 1, f"calls={coder2.calls}")


def test_fallback_real(checks: Checks) -> None:
    """Fallback that su: model dau la ten chet, model 2 goi CLI that."""
    root = make_project("fallback-real")
    health = fallback.HealthRegistry(root / "health.json")
    coder = fallback.FallbackCoder(
        ["opencode/definitely-dead-model-free", "opencode/deepseek-v4.1-flash-free"],
        factory=lambda model: workers.AgenticCoder(model, timeout=180),
        health=health,
    )
    report = recipe_c.run(make_order(), coder=coder, reviewer=None, root=root,
                          run_id="p3-fallback-real", max_requests=4,
                          max_seconds=280, log=False)
    checks.check("fallback (that): model chet -> model 2 cuu task",
                 report["status"] == "applied", str(report["error"])[:200])
    checks.check("fallback (that): ghi nhan lan that bai",
                 report["fallback"]["failures"] >= 1,
                 json.dumps(report["fallback"]["attempts"])[:300])
    checks.check("fallback (that): file duoc sua dung",
                 (root / "calc.py").read_text(encoding="utf-8") == FIXED)


# --------------------------------------------------------------------------
# 2. Secret scanner
# --------------------------------------------------------------------------
def test_secret_scan(checks: Checks) -> None:
    text = f'API_KEY = "{FAKE_KEY}"\ntoken = "abc"\n'
    redacted, findings = secret_scan.scan_text(text, filename="config.py")
    checks.check("secret: phat hien anthropic key",
                 any(item["kind"] == "anthropic_key" for item in findings), str(findings))
    checks.check("secret: redact thanh [REDACTED_SECRET]",
                 secret_scan.REDACTED in redacted and FAKE_KEY not in redacted)
    checks.check("secret: khong ghi lai key vao finding",
                 all(FAKE_KEY not in json.dumps(item) for item in findings))

    _, jwt_findings = secret_scan.scan_text(
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U")
    checks.check("secret: phat hien JWT",
                 any(item["kind"] == "jwt" for item in jwt_findings))

    checks.check("secret: chan .env", secret_scan.is_sensitive_path("app/.env") is not None)
    checks.check("secret: chan *.pem", secret_scan.is_sensitive_path("certs/server.pem") is not None)
    checks.check("secret: cho phep file thuong", secret_scan.is_sensitive_path("src/app.py") is None)

    # File dich chua key -> Work Order bi tu choi TRUOC khi goi model.
    root = make_project("leak-target", calc=f'KEY = "{FAKE_KEY}"\n' + BUGGY)
    ledger_path = root / ".agents" / "ledger.jsonl"
    try:
        recipe_c.run(make_order(), coder=StaticCoder(FIXED), reviewer=None, root=root,
                     run_id="p3-secret-1", log=True)
    except work_order_module.WorkOrderRejected as exc:
        checks.check("secret: file dich chua key -> tu choi", True)
        checks.check("secret: ly do ro rang",
                     "nhay cam" in str(exc) or "secret" in str(exc).lower(), str(exc)[:200])
    else:
        checks.check("secret: file dich chua key -> tu choi", False, "khong raise")

    events = ledger_module_events(ledger_path)
    checks.check("secret: ghi canh bao vao ledger",
                 any(event.get("event_type") == "secret.blocked" for event in events),
                 str([event.get("event_type") for event in events]))

    # Sandbox file nhay cam bi loai khoi ban sao gui model.
    root2 = make_project("leak-sandbox")
    (root2 / ".env").write_text(f"API_KEY={FAKE_KEY}\n", encoding="utf-8")
    sandbox = TEMP_ROOT / "leak-worker"
    shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True)
    prompts: list[str] = []

    def runner(prompt: str):
        prompts.append(prompt)
        cwd = Path(coder._active_cwd)                      # noqa: F821
        (cwd / "calc.py").write_text(FIXED, encoding="utf-8")
        return "done", {}

    coder = workers.AgenticCoder("stub/model", runner=runner)
    coder.bind_workspace(root2, sandbox)
    order = make_order(sandbox_files=["calc.py", "check.py", ".env"])
    coder.patch(order, current_content=BUGGY, feedback=None, round_index=1)
    checks.check("secret: .env bi loai khoi ban sao tam",
                 not (sandbox / "r1" / ".env").exists())
    checks.check("secret: ghi nhan file bi loai",
                 any(item["file"] == ".env" for item in coder.secrets_blocked),
                 str(coder.secrets_blocked))
    checks.check("secret: prompt khong chua key",
                 all(FAKE_KEY not in prompt for prompt in prompts))


# --------------------------------------------------------------------------
# 3. Plan validator
# --------------------------------------------------------------------------
def test_plan_validator(checks: Checks) -> None:
    good = {
        "plan_id": "ok",
        "nodes": [
            {"id": "lead", "type": "lead"},
            {"id": "coder", "type": "coder"},
            {"id": "systembot", "type": "systembot", "verify_command": "python check.py"},
        ],
        "parallel": [],
    }
    checks.check("plan: plan toi thieu hop le", plan_validator.validate_plan(good)["ok"],
                 str(plan_validator.validate_plan(good)["errors"]))

    three_branches = dict(good)
    three_branches["parallel"] = [["lead", "coder", "systembot"]]
    verdict = plan_validator.validate_plan(three_branches)
    checks.check("plan: 3 nhanh song song -> tu choi", not verdict["ok"], str(verdict["errors"]))
    checks.check("plan: ly do neu ro 'nhanh'",
                 any("nhanh" in error for error in verdict["errors"]), str(verdict["errors"]))

    no_verify = {
        "plan_id": "no-verify",
        "nodes": [
            {"id": "lead", "type": "lead"},
            {"id": "coder", "type": "coder"},
            {"id": "systembot", "type": "systembot"},
        ],
    }
    verdict = plan_validator.validate_plan(no_verify)
    checks.check("plan: thieu verify_command -> tu choi", not verdict["ok"])
    checks.check("plan: ly do neu ro 'verify_command'",
                 any("verify_command" in error for error in verdict["errors"]),
                 str(verdict["errors"]))

    unknown = dict(good)
    unknown["nodes"] = good["nodes"] + [{"id": "hacker", "type": "hacker"}]
    verdict = plan_validator.validate_plan(unknown)
    checks.check("plan: node type la -> tu choi", not verdict["ok"])
    checks.check("plan: ly do neu ro 'type'",
                 any("type" in error for error in verdict["errors"]), str(verdict["errors"]))

    too_many = dict(good)
    too_many["nodes"] = good["nodes"] + [
        {"id": f"qa{i}", "type": "qa"} for i in range(4)]
    verdict = plan_validator.validate_plan(too_many)
    checks.check("plan: > 6 node -> tu choi", not verdict["ok"], str(verdict["errors"]))

    omitted = dict(good)
    omitted["omitted"] = [{"type": "lead"}]
    verdict = plan_validator.validate_plan(omitted)
    checks.check("plan: bo node bat buoc thieu reason -> tu choi",
                 not verdict["ok"], str(verdict["errors"]))

    voiced = {
        "plan_id": "voiced",
        "nodes": [
            {"id": "coder", "type": "coder"},
            {"id": "systembot", "type": "systembot", "verify_command": "python check.py"},
        ],
        "omitted": [{"type": "lead", "reason": "Lead la nguoi goi, khong ton request"}],
    }
    verdict = plan_validator.validate_plan(voiced)
    checks.check("plan: bo node bat buoc co reason -> chap nhan (kem warning)",
                 verdict["ok"] and verdict["warnings"], str(verdict))


# --------------------------------------------------------------------------
# 4. Budget cap + cost report + session policy
# --------------------------------------------------------------------------
def test_budget_and_session(checks: Checks) -> None:
    root = make_project("budget")
    before = (root / "calc.py").read_bytes()
    order = make_order()
    report = recipe_c.run(order, coder=DeadCoder(), reviewer=None, root=root,
                          run_id="p3-budget-1", max_requests=1, max_seconds=300, log=False)
    checks.check("budget: cham tran request -> dung ngay",
                 report["budget"]["exceeded"] == "requests", str(report["budget"]))
    checks.check("budget: bao ro within report", "budget cap" in (report["error"] or ""),
                 str(report["error"]))
    checks.check("budget: file goc khong doi", (root / "calc.py").read_bytes() == before)

    report = recipe_c.run(make_order(), coder=DeadCoder(kind="timeout"), reviewer=None,
                          root=root, run_id="p3-budget-2", max_requests=8,
                          max_seconds=0, log=False)
    checks.check("budget: cham tran thoi gian -> dung ngay",
                 report["budget"]["exceeded"] == "time", str(report["budget"]))

    # Thanh cong dung o request cuoi cung van phai duoc apply (khong bi cat oan).
    root_last = make_project("budget-last")
    report = recipe_c.run(make_order(), coder=StaticCoder(FIXED), reviewer=None,
                          root=root_last, run_id="p3-budget-last", max_requests=1,
                          log=False)
    checks.check("budget: thanh cong o request cuoi van duoc apply",
                 report["status"] == "applied" and not report["budget"]["exceeded"],
                 f"{report['status']} {report['budget']}")

    # Session policy: 2 vong dau giu phien, vong 3 mo phien moi + brief.
    policy = workers.SessionPolicy()
    checks.check("session: vong 1-2 giu phien",
                 policy.reuse(1) and policy.reuse(2))
    checks.check("session: vong 3+ cat phien", policy.is_reset(3) and policy.is_reset(4))
    brief = policy.brief(target="calc.py", draft="def add(a, b):\n    return a - b\n",
                         feedback="verify that bai o dong 2")
    checks.check("session: brief co draft + loi",
                 "PHIEN MOI" in brief and "return a - b" in brief and "verify that bai" in brief)

    root2 = make_project("session")
    sandbox = TEMP_ROOT / "session-worker"
    shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True)
    prompts: list[str] = []

    def runner(prompt: str):
        prompts.append(prompt)
        cwd = Path(coder._active_cwd)                      # noqa: F821
        (cwd / "calc.py").write_text(FIXED, encoding="utf-8")
        return "ok", {}

    coder = workers.AgenticCoder("stub/model", runner=runner)
    coder.bind_workspace(root2, sandbox)
    order3 = make_order(max_rounds=3, level=1)
    for round_index in (1, 2, 3):
        coder.patch(order3, current_content=BUGGY, feedback="loi cu", round_index=round_index)
    modes = [event["mode"] for event in coder.session_events]
    checks.check("session: mode fresh -> resume -> fresh",
                 modes == ["fresh", "resume", "fresh"], str(modes))
    checks.check("session: vong 3 co brief tom tat", "PHIEN MOI" in prompts[2])
    checks.check("session: vong 1 khong co brief", "PHIEN MOI" not in prompts[0])

    # Cost report tu ledger tong hop.
    ledger_file = root / "ledger_test.jsonl"
    events = [
        {"event_type": "recipe.done", "run_id": "r1", "duration_ms": 1000,
         "usage": {"in_tokens": 100, "out_tokens": 50, "cache_read_tokens": 10},
         "result": {"status": "pass"},
         "data": {"status": "applied", "requests": 2, "fallback_attempts": 0,
                  "models": ["m1"]}},
        {"event_type": "recipe.done", "run_id": "r2", "duration_ms": 3000,
         "usage": {"in_tokens": 200, "out_tokens": 80, "cache_read_tokens": 0},
         "result": {"status": "pass"},
         "data": {"status": "applied", "requests": 2, "fallback_attempts": 1,
                  "models": ["m2", "m3"]}},
        {"event_type": "recipe.coder", "run_id": "r2", "duration_ms": 10},
    ]
    ledger_file.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    summary = cost_report.summarize(cost_report.load_events(ledger_file))
    checks.check("cost: gom dung 2 run", summary["totals"]["runs"] == 2,
                 str(summary["totals"]))
    checks.check("cost: tong token dung",
                 summary["totals"]["tokens_in"] == 300 and summary["totals"]["tokens_out"] == 130)
    checks.check("cost: ty le fallback = 25%", summary["totals"]["fallback_rate"] == 0.25,
                 str(summary["totals"]["fallback_rate"]))
    checks.check("cost: bang ASCII render duoc", "TOTAL" in cost_report.render(summary))

    # Clarify Gate: 1 lan hoi duy nhat, gom het cac diem mo ho.
    import clarify
    vague = make_order(objective="sua loi", acceptance_criteria="ok")
    problems = clarify.check(vague, root=root2)
    checks.check("clarify: muc tieu chung chung bi chan", len(problems) >= 1, str(problems))
    checks.check("clarify: gom nhieu loi vao 1 khoi",
                 clarify.build_question(problems).count("\n  ") == len(problems))
    checks.check("clarify: Work Order ro rang di qua",
                 clarify.check(make_order(), root=root2) == [])
    missing = make_order(target_files=["khong_ton_tai.py"])
    checks.check("clarify: thieu file dich bi chan",
                 any("khong ton tai" in problem for problem in clarify.check(missing, root=root2)))


def ledger_module_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


# --------------------------------------------------------------------------
# 5. Khong hoi quy: smoke suite Phase 1
# --------------------------------------------------------------------------
def test_no_regression(checks: Checks, *, fast: bool = False) -> None:
    if fast:
        print("[skip] smoke suite (--fast)")
        return
    script = AGENTS_DIR / "tests" / "smoke_suite.py"
    started = subprocess.run([sys.executable, str(script)], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=900)
    tail = (started.stdout or "")[-600:]
    checks.check("khong hoi quy: smoke suite Phase 1 exit 0",
                 started.returncode == 0,
                 f"exit={started.returncode} tail={tail}")


# --------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Phase 3 acceptance tests.")
    parser.add_argument("--fast", action="store_true", help="bo qua smoke suite")
    parser.add_argument("--real", action="store_true", help="chay them fallback voi model that")
    args = parser.parse_args(argv)

    checks = Checks()
    print("[1/4] fallback chain + health ...")
    test_fallback(checks)
    if args.real:
        print("[1b/4] fallback voi model that (CLI opencode) ...")
        test_fallback_real(checks)
    print("[2/4] secret scanner ...")
    test_secret_scan(checks)
    print("[3/4] plan validator ...")
    test_plan_validator(checks)
    print("[4/4] budget / session / cost + khong hoi quy ...")
    test_budget_and_session(checks)
    test_no_regression(checks, fast=args.fast)

    total = checks.total
    passed = checks.passed
    print("")
    print(f"PHASE 3: {passed}/{total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
