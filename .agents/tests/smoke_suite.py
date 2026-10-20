#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1 - smoke suite (Task 1.7).

Chay trong du an tam (moi task 1 thu muc rieng) de khong bao gio cham vao ma
nguon that. Do 4 thu:

  1. Engine checks  : classifier / work order / format ladder / permissions
  2. 10 task that   : 5 bug logic, 3 refactor, 2 bay (file dai, indent la)
  3. Circuit breaker: 2 vong sai lien tiep -> abort, file goc nguyen ven
  4. Toan ven       : hash ma nguon .agents/ truoc va sau khi chay

Doi tuong do la *engine*, nen Coder dung ban scripted (tat dinh). Muon do model
that thi dung `recipe_c.py --coder opencode` (xem README).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for _path in (AGENTS_DIR, TESTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import classifier                      # noqa: E402
import format_ladder                   # noqa: E402
import ledger                          # noqa: E402
import permissions                     # noqa: E402
import recipe_c                        # noqa: E402
import system_bot                      # noqa: E402
import work_order as work_order_module  # noqa: E402
import workers                         # noqa: E402

try:                                   # chay duoc ca `python file.py` va `python -m`
    from . import tasks as task_defs
except ImportError:                    # pragma: no cover
    import tasks as task_defs          # type: ignore[no-redef]

GATE_PASS_RATE = 0.8
GATE_APPLY_FAIL = 0.1
TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode"


# --------------------------------------------------------------------------
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


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_tree(*roots: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if "__pycache__" in relative or relative.startswith(("backups/", "draft/", "tmp/", "eval/")):
                continue
            if path.suffix in (".pyc", ".pyo") or ".bak" in path.name:
                continue
            hashes[f"{root.name}/{relative}"] = _sha(path)
    return hashes


# --------------------------------------------------------------------------
# Engine checks (Task 1.1 - 1.4)
# --------------------------------------------------------------------------
def engine_checks(workspace: Path) -> Checks:
    checks = Checks()

    # --- classifier -----------------------------------------------------
    cases = [
        ("Tai sao ham nay tra ve None?", 0),
        ("Sua typo trong readme.md", 1),
        ("Sua gia tri return trong calc.py", 1),
        ("Ham tinh toan trong calc.py bi sai logic", 2),
        ("Sua check token trong auth.py", 2),
        ("Sua a.py va b.py", 3),
    ]
    for prompt, expected in cases:
        outcome = classifier.classify(prompt)
        checks.check(f"classifier: {prompt[:32]}", outcome["level"] == expected,
                     f"duoc {outcome['level']}, can {expected}")

    ambiguous = "Lam on xu ly cai do giup toi"
    outcome = classifier.classify(ambiguous)
    checks.check("classifier: mo ho -> Level 2 (khong ton request)",
                 outcome["level"] == 2 and outcome["decided_by"] == "rules-default", outcome)
    outcome = classifier.classify(ambiguous, llm=lambda _prompt: "1")
    checks.check("classifier: mo ho + LLM -> Level 1",
                 outcome["level"] == 1 and outcome["decided_by"] == "llm", outcome)
    checks.check("classifier: prompt LLM <= 200 token", outcome["llm_prompt_tokens"] <= 200,
                 outcome["llm_prompt_tokens"])
    outcome = classifier.classify(ambiguous, llm=lambda _prompt: "khong biet")
    checks.check("classifier: LLM tra loi rac -> Level 2",
                 outcome["level"] == 2 and outcome["decided_by"] == "llm-fallback", outcome)

    # --- work order -----------------------------------------------------
    base = dict(objective="x", boundaries="y", input_context="z",
                acceptance_criteria="w", target_files=["a.py"])

    def rejected(**overrides) -> tuple[bool, str]:
        try:
            work_order_module.create(**{**base, **overrides})
        except work_order_module.WorkOrderRejected as exc:
            return True, str(exc)
        return False, ""

    ok, detail = rejected(verify_command="")
    checks.check("work_order: thieu verify_command -> tu choi", ok and "verify_command" in detail, detail)
    ok, detail = rejected(verify_command="rm -rf /")
    checks.check("work_order: verify bi SystemBot chan -> tu choi", ok and "SystemBot" in detail, detail)
    ok, detail = rejected(verify_command="python t.py", max_rounds=9)
    checks.check("work_order: max_rounds > cap -> tu choi", ok and "max_rounds" in detail, detail)
    ok, detail = rejected(verify_command="python t.py", max_rounds=5)
    checks.check("work_order: max_rounds > 4 (Phase 3 cap) -> tu choi", ok and "max_rounds" in detail, detail)
    three = work_order_module.create(**{**base, "verify_command": "python test.py", "max_rounds": 3})
    checks.check("work_order: max_rounds=3 hop le (session policy vong 3)",
                 three.is_valid() and three.max_rounds == 3)
    ok, detail = rejected(verify_command="python t.py", target_files=["../a.py"])
    checks.check("work_order: target ../ -> tu choi", ok and "target_files" in detail, detail)
    valid = work_order_module.create(**{**base, "verify_command": "python test.py"})
    checks.check("work_order: hop le", valid.is_valid() and valid.max_rounds == 2)

    # --- Level 3 (da file) ----------------------------------------------
    ok, detail = rejected(verify_command="python t.py", level=3)
    checks.check("work_order: level 3 can >= 2 file -> tu choi",
                 ok and "target_files" in detail, detail)
    ok, detail = rejected(verify_command="python t.py", level=2,
                          target_files=["a.py", "b.py"])
    checks.check("work_order: level 2 voi 2 file -> tu choi (phai la level 3)",
                 ok and "level" in detail, detail)
    multi = work_order_module.create(**{**base, "verify_command": "python test.py",
                                        "level": 3, "target_files": ["a.py", "b.py"]})
    checks.check("work_order: level 3 voi 2 file hop le",
                 multi.is_valid() and len(multi.target_files) == 2, multi.problems())
    clone = multi.for_file("b.py")
    checks.check("work_order: for_file thu hep dung 1 file + giu ghi chu",
                 clone.target_files == ["b.py"] and "a.py" in clone.notes
                 and "b.py" in clone.notes, clone.notes)
    classified = classifier.classify("Sua a.py va b.py")
    checks.check("classifier: 2 file -> Level 3 (multi-file)", classified["level"] == 3,
                 classified)
    checks.check("work_order: prompt coder co du 5 truong",
                 all(label in valid.to_prompt() for label in
                     ("MUC TIEU", "RANH GIOI", "NGU CANH", "TIEU CHI", "LENH VERIFY")))

    # --- format ladder ---------------------------------------------------
    small = "def add(a, b):\n    return a - b\n"
    ladder = format_ladder.apply_patch(small, {"format": "whole",
                                               "content": "def add(a, b):\n    return a + b\n"})
    checks.check("ladder: bac 0 whole (file ngan)", ladder["level"] == 0 and "+" in ladder["content"], ladder)

    long_before = "\n".join(f"x{i} = {i}" for i in range(200)) + "\ndef f(a):\n    return a - 1\n"
    long_after = long_before.replace("return a - 1", "return a + 1")
    ladder = format_ladder.apply_patch(long_before, {"format": "whole", "content": long_after})
    checks.check("ladder: file >150 dong -> tu ha bac 0 -> 1",
                 ladder["level"] == 1 and ladder["downgraded"] and "return a + 1" in ladder["content"], ladder)

    body = "def add(a, b):\n    return a - b\n\ndef mul(a, b):\n    return a * b\n"
    ladder = format_ladder.apply_patch(body, {"format": "replace", "edits": [
        {"search": "    return a - b", "replace": "    return a + b"}]})
    checks.check("ladder: bac 1 replace khop chinh xac",
                 ladder["level"] == 1 and "a + b" in ladder["content"], ladder)

    tabbed = "def add(a, b):\n\treturn a - b\n\ndef mul(a, b):\n\treturn a * b\n"
    ladder = format_ladder.apply_patch(tabbed, {"format": "replace", "edits": [
        {"search": "    return a - b   \n", "replace": "    return a + b"}]})
    checks.check("ladder: chuan hoa khoang trang (tab vs space)",
                 "\treturn a + b" in ladder["content"], ladder["content"])

    try:
        format_ladder.apply_patch(body, {"format": "replace", "edits": [
            {"search": "    return a / b", "replace": "    return 0"}]})
        checks.check("ladder: search khong ton tai -> loi", False, "khong raise")
    except format_ladder.PatchFailed as exc:
        checks.check("ladder: search khong ton tai -> loi", "khong tim thay" in str(exc), str(exc))

    duplicate = "def f():\n    return 1\n\ndef g():\n    return 1\n"
    try:
        format_ladder.apply_patch(duplicate, {"format": "replace", "edits": [
            {"search": "    return 1", "replace": "    return 2"}]})
        checks.check("ladder: search mo ho -> tu choi", False, "khong raise")
    except format_ladder.PatchFailed as exc:
        checks.check("ladder: search mo ho -> tu choi", "mo ho" in str(exc), str(exc))

    ladder = format_ladder.apply_patch(body, {"format": "symbol", "edits": [
        {"name": "mul", "replace": "def mul(a, b):\n    return a * b + 0\n"}]})
    checks.check("ladder: bac 2 symbol", ladder["level"] == 2 and "a * b + 0" in ladder["content"], ladder)

    klass = "class A:\n    def m(self):\n        return 1\n"
    ladder = format_ladder.apply_patch(klass, {"format": "symbol", "edits": [
        {"name": "m", "replace": "def m(self):\n    return 2\n"}]})
    checks.check("ladder: symbol giu indent cua method",
                 "        return 2" in ladder["content"], ladder["content"])

    try:
        format_ladder.apply_patch(body, {"format": "whole",
                                         "content": "def add(a, b):\n    return a +\n"})
        checks.check("ladder: chan code sai cu phap", False, "khong raise")
    except format_ladder.PatchFailed as exc:
        checks.check("ladder: chan code sai cu phap", "sai cu phap" in str(exc), str(exc))

    ladder = format_ladder.apply_patch("a = 1\nb = 2\nc = 3\n", {"format": "replace", "edits": [
        {"search": "a = 1", "replace": "a = 10"},
        {"search": "c = 3", "replace": "c = 30"}]})
    checks.check("ladder: nhieu edit trong 1 file",
                 "a = 10" in ladder["content"] and "c = 30" in ladder["content"], ladder["content"])

    # --- ca bien: xuong dong / encoding / ky tu dac biet -----------------
    crlf = "def add(a, b):\r\n    return a - b\r\n"
    ladder = format_ladder.apply_patch(crlf, {"format": "replace", "edits": [
        {"search": "    return a - b", "replace": "    return a + b"}]})
    out = ladder["content"]
    checks.check("ladder: giu nguyen kieu xuong dong CRLF",
                 out.count("\r\n") == out.count("\n") and "a + b" in out, repr(out))

    bom = "\ufeffdef add(a, b):\n    return a - b\n"
    ladder = format_ladder.apply_patch(bom, {"format": "replace", "edits": [
        {"search": "    return a - b", "replace": "    return a + b"}]})
    checks.check("ladder: khong nhan doi BOM", ladder["content"].count("\ufeff") == 1,
                 repr(ladder["content"][:6]))

    form_feed = 'x = 1\ns = "a\x0cb"\n'
    ladder = format_ladder.apply_patch(form_feed, {"format": "replace", "edits": [
        {"search": "x = 1", "replace": "x = 2"}]})
    checks.check("ladder: khong cat ky tu form feed trong string",
                 "\x0c" in ladder["content"] and ladder["content"].count("\n") == 2,
                 repr(ladder["content"]))

    ladder = format_ladder.apply_patch("", {"format": "whole", "content": "x = 1\n"})
    checks.check("ladder: file rong + whole", ladder["content"] == "x = 1\n", ladder)

    latin = workspace / "cp1252.py"
    latin.write_bytes(b"# vi du: ti\xeang vi\xe2t\nx = 1\n")
    text, encoding = recipe_c._read_source(latin)
    checks.check("source: doc duoc file cp1252 (khong crash)",
                 encoding in ("cp1252", "latin-1") and "x = 1" in text, (encoding, text[:20]))

    # end-to-end: file CRLF phai o lai CRLF sau khi sua (neu khong, ca file bi doi)
    crlf_root = workspace / "_crlf"
    shutil.rmtree(crlf_root, ignore_errors=True)
    crlf_root.mkdir(parents=True)
    (crlf_root / "calc.py").write_bytes(b"def add(a, b):\r\n    return a - b\r\n")
    (crlf_root / "test_calc.py").write_bytes(
        b"import calc\r\nif calc.add(2, 3) != 5:\r\n    raise SystemExit(1)\r\nprint('OK')\r\n")
    order = work_order_module.create(
        task_id="crlf", level=1, objective="sua add thanh phep cong", boundaries="chi calc.py",
        input_context="calc.py", acceptance_criteria="test_calc.py exit 0",
        verify_command=f'"{sys.executable}" test_calc.py',
        target_files=["calc.py"], sandbox_files=["calc.py", "test_calc.py"])
    report = recipe_c.run(order, coder=workers.ScriptedCoder({"crlf": [
        {"format": "replace", "edits": [{"search": "    return a - b", "replace": "    return a + b"}]}]}),
        reviewer=None, root=crlf_root, run_id="edge-crlf")
    data = (crlf_root / "calc.py").read_bytes()
    checks.check("recipe: file CRLF o lai CRLF sau khi sua",
                 report["status"] == "applied" and data.count(b"\r\n") == data.count(b"\n")
                 and data.count(b"\n") >= 2, (report["status"], data))

    # --- agentic coder: model sua file trong ban sao tam -------------------
    agent_root = workspace / "_agentic"
    agent_sandbox = workspace / "_agentic_worker"
    shutil.rmtree(agent_root, ignore_errors=True)
    shutil.rmtree(agent_sandbox, ignore_errors=True)
    agent_root.mkdir(parents=True)
    (agent_root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (agent_root / "helper.py").write_text("X = 1\n", encoding="utf-8")

    order = work_order_module.create(
        task_id="agentic", level=1, objective="sua add thanh phep cong",
        boundaries="chi calc.py", input_context="calc.py, helper.py",
        acceptance_criteria="verify exit 0",
        verify_command=f'"{sys.executable}" test_calc.py',
        target_files=["calc.py"], sandbox_files=["calc.py", "helper.py"])

    holder: dict = {}

    def fake_agent(prompt):
        copies = sorted(Path(holder["coder"].cwd or ".").glob("r*/calc.py"))
        if not copies:
            raise AssertionError("khong tim thay ban sao tam")
        copies[-1].write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        return "da sua add", {}

    agentic = workers.AgenticCoder("fake/model", runner=fake_agent)
    holder["coder"] = agentic
    agentic.bind_workspace(agent_root, agent_sandbox)
    patch = agentic.patch(order, current_content=(agent_root / "calc.py").read_text(encoding="utf-8"),
                          feedback=None, round_index=1)
    checks.check("agentic: doc lai file sau khi model sua -> patch whole",
                 patch["format"] == "whole" and "a + b" in patch["content"],
                 patch.get("format"))
    checks.check("agentic: ban sao tam co ca file phu",
                 (agent_sandbox / "r1" / "helper.py").exists())
    checks.check("agentic: khong sua file du an that",
                 "a - b" in (agent_root / "calc.py").read_text(encoding="utf-8"))

    # --- guard: worker tu sua file du an thi phai bi khoi phuc -------------
    guard_root = workspace / "_guard"
    shutil.rmtree(guard_root, ignore_errors=True)
    guard_root.mkdir(parents=True)
    (guard_root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (guard_root / "test_calc.py").write_text(
        "import calc\nassert calc.add(2, 3) == 5\nprint('OK')\n", encoding="utf-8")

    class TamperCoder(workers.Coder):
        def __init__(self, project_root):
            super().__init__()
            self.name = "tamper-coder"
            self.root = Path(project_root)

        def patch(self, work_order, *, current_content, feedback, round_index):
            self.calls += 1
            (self.root / work_order.target_files[0]).write_text("HACKED = 1\n", encoding="utf-8")
            return {"format": "whole",
                    "content": current_content.replace("a - b", "a + b")}

    order = work_order_module.create(
        task_id="tamper", level=1, objective="sua add", boundaries="chi calc.py",
        input_context="calc.py", acceptance_criteria="verify exit 0",
        verify_command=f'"{sys.executable}" test_calc.py',
        target_files=["calc.py"], sandbox_files=["calc.py", "test_calc.py"])
    report = recipe_c.run(order, coder=TamperCoder(guard_root), reviewer=None,
                          root=guard_root, run_id="edge-guard")
    final = (guard_root / "calc.py").read_text(encoding="utf-8")
    checks.check("guard: worker sua file that -> khoi phuc, van apply dung",
                 report["status"] == "applied" and report["worker_edits_detected"] == 1
                 and "a + b" in final and "HACKED" not in final,
                 (report["status"], report["worker_edits_detected"], final))

    # --- permissions ------------------------------------------------------
    root = workspace / "_perm"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "proj.py").write_text("x = 1\n", encoding="utf-8")
    gate = permissions.PermissionGate(root, log=False)
    draft = gate.draft_path("t1", "proj.py")
    checks.check("permission: coder ghi duoc .agents/draft",
                 gate.check_write(permissions.ROLE_CODER, draft)["allowed"])
    checks.check("permission: coder KHONG ghi duoc file du an",
                 not gate.check_write(permissions.ROLE_CODER, root / "proj.py")["allowed"])
    checks.check("permission: coder KHONG thoat duoc bang ..",
                 not gate.check_write(permissions.ROLE_CODER,
                                      gate.draft_dir / ".." / ".." / "proj.py")["allowed"])
    checks.check("permission: reviewer la read-only",
                 not gate.check_write(permissions.ROLE_REVIEWER, root / "proj.py")["allowed"])
    checks.check("permission: lead ghi duoc file du an",
                 gate.check_write(permissions.ROLE_LEAD, root / "proj.py")["allowed"])
    checks.check("permission: lead KHONG ghi truc tiep vao .agents",
                 not gate.check_write(permissions.ROLE_LEAD, gate.agents_dir / "config.json")["allowed"])
    try:
        permissions.FindingsSink(root, log=False).write_file(root / "proj.py")
        checks.check("permission: reviewer chi co sink ledger", False, "khong raise")
    except permissions.PermissionDenied:
        checks.check("permission: reviewer chi co sink ledger", True)
    checks.check("permission: nhan dien file nhay cam",
                 permissions.is_sensitive(".env") and permissions.is_sensitive("id_rsa")
                 and not permissions.is_sensitive("main.py"))

    # --- workers: parser + adapter goi model (khong ton request) ---------
    noisy = ('Toi se sua nhu sau:\n```json\n'
             '{"format": "replace", "edits": [{"search": "a = 1", "replace": "a = 2"}]}\n'
             '```\nHet.')
    parsed = workers.parse_patch_json(noisy)
    checks.check("workers: boc JSON tu output co rac",
                 parsed["format"] == "replace" and parsed["edits"][0]["search"] == "a = 1", parsed)
    parsed = workers.parse_patch_json('nen dung {"format":"whole","content":"x = 1\\n"} nhu vay')
    checks.check("workers: nhan JSON khong co fence", parsed["format"] == "whole", parsed)
    try:
        workers.parse_patch_json("khong co json nao ca")
        checks.check("workers: output rac -> loi ro rang", False, "khong raise")
    except ValueError as exc:
        checks.check("workers: output rac -> loi ro rang", "JSON" in str(exc), str(exc))

    fake_coder = workers.OpenCodeCoder(
        "fake/model", runner=lambda _prompt: '{"format":"whole","content":"y = 2\\n"}')
    patch = fake_coder.patch(valid, current_content="y = 1\n", feedback=None, round_index=1)
    checks.check("workers: OpenCodeCoder qua runner gia",
                 patch["format"] == "whole" and fake_coder.calls == 1, patch)

    fake_review = workers.OpenCodeReviewer(
        "fake/model", runner=lambda _prompt: '{"verdict":"reject","reasons":["thieu test"]}')
    sink = permissions.FindingsSink(workspace, log=False)
    findings = fake_review.review(valid, diff="+x", new_content="x", old_content="",
                                  level=2, sink=sink)
    checks.check("workers: OpenCodeReviewer ghi findings vao sink (khong ghi file)",
                 findings["verdict"] == "reject" and len(sink.written) == 1, findings)

    broken_review = workers.OpenCodeReviewer(
        "fake/model", runner=lambda _prompt: (_ for _ in ()).throw(RuntimeError("het quota")))
    findings = broken_review.review(valid, diff="+x", new_content="x", old_content="",
                                    level=2, sink=None)
    checks.check("workers: reviewer loi -> reject (fail-safe)", findings["verdict"] == "reject", findings)

    model = recipe_c._model_from_config("coder")
    checks.check("config: doc duoc model coder tu config.json",
                 "/" in model and model.count("/") >= 1, model)
    return checks


# --------------------------------------------------------------------------
# 1 task
# --------------------------------------------------------------------------
def run_task(task: dict, workspace: Path) -> dict:
    root = workspace / task["id"]
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    for name, content in task["files"].items():
        (root / name).write_text(content, encoding="utf-8")
    original_hashes = {name: _sha(root / name) for name in task["files"]}

    started = time.perf_counter()
    outcome = classifier.classify(task["prompt"], paths=task["paths"])
    order = work_order_module.create(
        task_id=task["id"], title=task["title"], level=outcome["level"],
        objective=task["prompt"],
        boundaries=f"Chi sua {task['target']}. Khong tao file moi, khong sua file test.",
        input_context=", ".join(task["sandbox_files"]),
        acceptance_criteria=f"lenh verify tra exit code 0: {task['verify']}",
        verify_command=task["verify"],
        target_files=[task["target"]],
        sandbox_files=task["sandbox_files"],
        max_rounds=2,
    )
    coder = workers.ScriptedCoder({task["id"]: task["patches"]})
    reviewer = workers.HeuristicReviewer()
    report = recipe_c.run(order, coder=coder, reviewer=reviewer, root=root,
                          run_id=f"smoke01-{task['id'].lower()}")

    verify = system_bot.run_command(task["verify"], cwd=root, root=root,
                                    run_id=f"smoke01-check-{task['id'].lower()}", log=False)
    duration = time.perf_counter() - started

    expect = task["expect"]
    final_hashes = {name: _sha(root / name) for name in task["files"]}
    test_untouched = all(final_hashes[name] == original_hashes[name]
                         for name in task["files"] if name != task["target"])

    checks = {}
    checks["status"] = report["status"] == expect["status"]
    checks["level"] = outcome["level"] == task["expect_level"]
    checks["patch_level"] = (expect.get("patch_level") is None
                             or report["patch_level"] == expect["patch_level"])
    checks["rounds"] = report["rounds"] >= expect.get("min_rounds", 1)
    checks["downgraded"] = (not expect.get("downgraded")) or report["patch_downgraded"]
    checks["test_files"] = test_untouched
    if expect["status"] == "applied":
        checks["verify"] = verify["exit_code"] == 0 and report["file_changed"]
        checks["clean"] = not report["rolled_back"] and bool(report["apply"])
    else:
        checks["verify"] = final_hashes[task["target"]] == original_hashes[task["target"]]
        checks["clean"] = report["apply"] is None

    return {
        "task_id": task["id"],
        "kind": task["kind"],
        "title": task["title"],
        "level": outcome["level"],
        "expected_level": task["expect_level"],
        "rounds": report["rounds"],
        "patch_level": report["patch_level"],
        "downgraded": report["patch_downgraded"],
        "status": report["status"],
        "verify_exit": verify["exit_code"],
        "requests": report["requests"],
        "coder_calls": report["coder_calls"],
        "reviewer_calls": report["reviewer_calls"],
        "duration_s": duration,
        "checks": checks,
        "passed": all(checks.values()),
        "failed_checks": [name for name, ok in checks.items() if not ok],
        "feedback_rounds": len(report["feedback"]),
        "dir": str(root),
        "report": report,
    }


# --------------------------------------------------------------------------
# Circuit breaker
# --------------------------------------------------------------------------
def breaker_check(workspace: Path) -> dict:
    task = dict(task_defs.T01)
    task["id"] = "X1"
    task["patches"] = [
        {"format": "replace", "edits": [{"search": "    for i in range(n - 1):",
                                         "replace": "    for i in range(n - 2):"}]},
        {"format": "replace", "edits": [{"search": "    for i in range(n - 1):",
                                         "replace": "    for i in range(n - 3):"}]},
    ]
    root = workspace / "X1"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    for name, content in task["files"].items():
        (root / name).write_text(content, encoding="utf-8")
    before = _sha(root / task["target"])

    order = work_order_module.create(
        task_id="X1", title=task["title"], level=2,
        objective=task["prompt"],
        boundaries="Chi sua calc.py",
        input_context="calc.py, test_calc.py",
        acceptance_criteria="test_calc.py exit 0",
        verify_command=task["verify"],
        target_files=[task["target"]],
        sandbox_files=task["sandbox_files"],
        max_rounds=2,
    )
    coder = workers.ScriptedCoder({"X1": task["patches"]})
    report = recipe_c.run(order, coder=coder, reviewer=workers.HeuristicReviewer(),
                          root=root, run_id="smoke01-x1")
    after = _sha(root / task["target"])

    checks = {
        "abort sau 2 vong": report["status"] == recipe_c.STATUS_ABORTED and report["rounds"] == 2,
        "khong cham file that": report["apply"] is None,
        "file nguyen ven": before == after,
        "co bao cao loi": bool(report["error"]),
    }
    return {"name": "Circuit breaker (2 vong sai -> abort)", "checks": checks,
            "passed": all(checks.values()),
            "detail": f"rounds={report['rounds']} status={report['status']}"}


# --------------------------------------------------------------------------
def print_table(results: list[dict]) -> None:
    header = (f"{'ID':<4} {'KIND':<9} {'LVL':<4} {'CLS':<4} {'RDS':<4} "
              f"{'BAC':<9} {'STATUS':<8} {'VERIFY':<7} {'TIME':<7} RESULT")
    print(header)
    print("-" * len(header))
    for item in results:
        level = str(item["level"]) + ("" if item["level"] == item["expected_level"] else "!")
        bac = item["patch_level"] or "-"
        if item["downgraded"]:
            bac += "*"
        verify = "pass" if item["verify_exit"] == 0 else f"exit{item['verify_exit']}"
        print(f"{item['task_id']:<4} {item['kind']:<9} {level:<4} "
              f"{'ok' if item['level'] == item['expected_level'] else 'X':<4} "
              f"{item['rounds']:<4} {bac:<9} {item['status']:<8} {verify:<7} "
              f"{item['duration_s']:<7.2f} {'PASS' if item['passed'] else 'FAIL'}")
        if not item["passed"]:
            print(f"     -> hong: {', '.join(item['failed_checks'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 1 smoke suite.")
    parser.add_argument("--json", action="store_true", help="in ket qua day du dang JSON")
    parser.add_argument("--clean", action="store_true", help="xoa thu muc tam sau khi chay")
    args = parser.parse_args(argv)

    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="swarm_smoke_", dir=str(TEMP_ROOT)))
    project_root = Path(__file__).resolve().parents[2]

    before = _hash_tree(AGENTS_DIR, project_root / "agents")
    started = time.perf_counter()

    print("=== ADAPTIVE SWARM PIPELINE - PHASE 1 SMOKE SUITE ===")
    print(f"workspace: {workspace}")
    print(f"python   : {sys.version.split()[0]}")
    print()

    engine = engine_checks(workspace)
    print(f"[1/4] Engine checks (classifier/work_order/ladder/permissions): "
          f"{engine.passed}/{engine.total}")
    for failure in engine.failures:
        print(f"      FAIL {failure}")
    print()

    results = [run_task(task, workspace) for task in task_defs.TASKS]
    print_table(results)
    print()

    breaker = breaker_check(workspace)
    print(f"[2/4] {breaker['name']}: {'PASS' if breaker['passed'] else 'FAIL'} "
          f"({breaker['detail']})")
    for name, ok in breaker["checks"].items():
        if not ok:
            print(f"      FAIL {name}")
    print()

    passed = sum(1 for item in results if item["passed"])
    total = len(results)
    pass_rate = passed / total if total else 0.0

    applies = [item for item in results if item["report"]["apply"] is not None]
    apply_fails = sum(1 for item in applies if not item["report"]["apply"]["verified"])
    apply_fail_rate = apply_fails / len(applies) if applies else 0.0

    total_requests = sum(item["requests"] for item in results)
    total_coder = sum(item["coder_calls"] for item in results)
    total_reviewer = sum(item["reviewer_calls"] for item in results)
    avg_seconds = sum(item["duration_s"] for item in results) / total

    ledger_ok = 0
    ledger_events = 0
    for item in results:
        info = ledger.validate_ledger(root=Path(item["dir"]))
        ledger_events += info["total_events"]
        if info["invalid_events"] == 0 and info["exists"]:
            ledger_ok += 1

    after = _hash_tree(AGENTS_DIR, project_root / "agents")
    source_intact = before == after
    corrupted = [item["task_id"] for item in results
                 if not item["checks"]["verify"] or not item["checks"]["test_files"]]

    gates = {
        "pass_rate": pass_rate >= GATE_PASS_RATE,
        "apply_fail": apply_fail_rate < GATE_APPLY_FAIL,
        "no_corruption": not corrupted,
        "source_intact": source_intact,
        "breaker": breaker["passed"],
        "engine": not engine.failures,
        "ledger": ledger_ok == total,
    }

    def verdict(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    print("[3/4] Ket qua")
    print(f"      Pass rate       : {passed}/{total} ({pass_rate * 100:.1f}%)   "
          f"gate >= {GATE_PASS_RATE * 100:.0f}%   {verdict(gates['pass_rate'])}")
    print(f"      Apply fail      : {apply_fails}/{len(applies)} "
          f"({apply_fail_rate * 100:.1f}%)   gate < {GATE_APPLY_FAIL * 100:.0f}%   "
          f"{verdict(gates['apply_fail'])}")
    print(f"      Calls / task    : {total_coder / total:.1f} coder + "
          f"{total_reviewer / total:.1f} reviewer = {total_requests / total:.1f}")
    print(f"      LLM that        : 0 (coder scripted + reviewer heuristic); "
          f"muc tieu thiet ke 1-2 request/task")
    print(f"      Giay / task     : {avg_seconds:.2f}s (chua tinh do tre model)")
    print(f"      File hong       : {len(corrupted)}/{total}   {verdict(gates['no_corruption'])}")
    print()
    print("[4/4] Toan ven")
    print(f"      Ma nguon .agents/ + agents/ : {len(before)} file, "
          f"{'khong doi' if source_intact else 'DA DOI'}   {verdict(gates['source_intact'])}")
    print(f"      Ledger hop le               : {ledger_ok}/{total} "
          f"({ledger_events} event, 0 invalid)   {verdict(gates['ledger'])}")

    overall = all(gates.values())
    print()
    print(f"KET LUAN: {'DAT' if overall else 'KHONG DAT'} "
          f"(round {time.perf_counter() - started:.1f}s)")
    if not overall:
        print("Gate khong dat: " + ", ".join(name for name, ok in gates.items() if not ok))

    if args.json:
        payload = {
            "workspace": str(workspace),
            "engine": {"total": engine.total, "passed": engine.passed,
                       "failures": engine.failures},
            "tasks": [{key: value for key, value in item.items() if key != "report"}
                      for item in results],
            "breaker": breaker,
            "gates": gates,
            "metrics": {
                "pass_rate": pass_rate, "apply_fail_rate": apply_fail_rate,
                "calls_per_task": {"coder": total_coder / total,
                                   "reviewer": total_reviewer / total,
                                   "total": total_requests / total},
                "llm_requests_measured": 0,
                "seconds_per_task": avg_seconds,
                "ledger_events": ledger_events,
            },
            "overall": overall,
        }
        (workspace / "report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON: {workspace / 'report.json'}")

    if args.clean:
        shutil.rmtree(workspace, ignore_errors=True)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
