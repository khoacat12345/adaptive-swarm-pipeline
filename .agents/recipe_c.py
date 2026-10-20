#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recipe C - dong co pipeline (Task 1.5) + circuit breaker (Task 1.6).

Luong:

    Lead tao Work Order (da validate)
      -> lap round 1..max_rounds (=2):
           Coder sinh patch
           Format Ladder ap patch vao BAN NHAP (.agents/draft/...)   [bac 0/1/2]
           SystemBot verify BAN NHAP trong shadow dir                [may that]
           Reviewer doc diff (chi Level 2, va chi doc)                [read-only]
           Lead apply an toan: backup -> ghi -> verify -> rollback   [safe_writer]
         neu verify fail -> feedback -> round tiep
      -> het luot -> ABORT + bao cao, file goc khong doi

Vi sao verify 2 lan: lan 1 tren shadow de *khong cham vao du an* khi con sai
(cat duoc vong rollback), lan 2 tren file that de chot hop dong "may that
chung minh". Lan 2 luon co rollback nen du co sai thi file goc van sach.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import format_ladder
import clarify
import ledger
import memory
import permissions
import plan_validator
import safe_writer
import secret_scan
import system_bot
import work_order as work_order_module
import workers
from workers import unified_diff

STATUS_APPLIED = "applied"
STATUS_ABORTED = "aborted"
STATUS_REJECTED = "rejected"
STATUS_DRY_RUN = "dry-run"

VERIFY_TAIL_LINES = 8

# Tran cung cho moi run (Task 3.7). Khong recipe nao duoc vuot.
BUDGET_MAX_REQUESTS = 8
BUDGET_MAX_SECONDS = 300


# --------------------------------------------------------------------------
def _tail(text_lines, limit: int = VERIFY_TAIL_LINES) -> str:
    lines = [str(line) for line in (text_lines or [])]
    return "\n".join(lines[-limit:])


def _read_source(path: Path) -> tuple[str, str]:
    """Doc file nguon (giu kieu xuong dong, do encoding) — xem workers.read_source_file."""
    return workers.read_source_file(path)


def _feedback(stage: str, error: str, detail: str, round_index: int) -> str:
    return (
        f"VONG {round_index} THAT BAI o buoc [{stage}].\n"
        f"Loi: {error}\n"
        f"{detail}\n"
        "Hay sua lai cho dung. Luu y: file <= 150 dong moi dung format 'whole'; "
        "file dai hon phai dung 'replace' (khoi SEARCH phai khop nguyen van) hoac 'symbol'."
    )


def _verify_draft(work_order, *, gate, root: Path, run_id: str, round_index: int,
                  target_rel: str, content: str, draft_path: Path,
                  verify_timeout: int | None, log: bool,
                  encoding: str = "utf-8") -> dict:
    """Chay verify tren BAN NHAP. Khong cham vao file that.

    - Co `sandbox_files`: copy cac file can thiet vao shadow dir, thay file dich
      bang ban nhap, roi chay verify_command tai do => verify ngu nghia that.
    - Khong co: chi py_compile ban nhap => verify cu phap (re hon).
    """
    started = time.perf_counter()
    sandbox_files = [item for item in (work_order.sandbox_files or []) if item != target_rel]

    if sandbox_files:
        shadow = gate.agents_dir / "tmp" / run_id / f"shadow_r{round_index}"
        if shadow.exists():
            shutil.rmtree(shadow, ignore_errors=True)
        for relative in [target_rel] + sandbox_files:
            source = root / relative
            if not source.exists():
                return {"mode": "sandbox", "result": "error", "exit_code": None,
                        "error": f"thieu file trong sandbox: {relative}",
                        "duration_ms": int((time.perf_counter() - started) * 1000)}
            destination = shadow / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        try:
            (shadow / target_rel).write_text(content, encoding=encoding, newline="")
        except (OSError, UnicodeEncodeError) as exc:
            return {"mode": "sandbox", "result": "error", "exit_code": None,
                    "error": f"khong ghi duoc ban nhap ({encoding}): {exc}",
                    "duration_ms": int((time.perf_counter() - started) * 1000)}

        command = work_order.render_verify_command(target_rel)
        bot = system_bot.run_command(
            command, cwd=shadow, timeout=verify_timeout or system_bot.DEFAULT_TIMEOUT,
            root=root, run_id=run_id, log=log,
        )
        return {
            "mode": "sandbox",
            "command": command,
            "result": bot["result"],
            "exit_code": bot["exit_code"],
            "duration_ms": bot["duration_ms"],
            "output_tail": bot["output_tail"][-VERIFY_TAIL_LINES:],
            "error": None if bot["exit_code"] == 0 else f"exit_code={bot['exit_code']}",
        }

    bot = system_bot.run_command(
        f'"{sys.executable}" -m py_compile "{draft_path}"',
        cwd=root, timeout=verify_timeout or 120, root=root, run_id=run_id, log=log,
    )
    return {
        "mode": "syntax",
        "command": "py_compile",
        "result": bot["result"],
        "exit_code": bot["exit_code"],
        "duration_ms": bot["duration_ms"],
        "output_tail": bot["output_tail"][-VERIFY_TAIL_LINES:],
        "error": None if bot["exit_code"] == 0 else f"exit_code={bot['exit_code']}",
    }


# --------------------------------------------------------------------------
def run(work_order, *, coder, reviewer=None, root=None, run_id: str | None = None,
        dry_run: bool = False, verify_timeout: int | None = None,
        log: bool = True, max_requests: int | None = None,
        max_seconds: int | None = None, use_memory: bool = True) -> dict:
    """Chay Recipe C cho 1 Work Order. Tra ve bao cao (khong raise nghiep vu)."""
    if not isinstance(work_order, work_order_module.WorkOrder):
        work_order = work_order_module.WorkOrder.from_dict(work_order)

    root_path = Path(root or Path.cwd()).resolve()
    run_id = run_id or ledger.new_run_id("rc")
    max_requests = (BUDGET_MAX_REQUESTS if max_requests is None
                    else min(int(max_requests), BUDGET_MAX_REQUESTS))
    max_seconds = (BUDGET_MAX_SECONDS if max_seconds is None
                   else min(int(max_seconds), BUDGET_MAX_SECONDS))

    # Mo run TRUOC moi buoc: timeline luon bat dau bang "nhan task", ke ca khi
    # clarify / plan / secret chan o buoc sau.
    if log:
        ledger.append_event(
            "recipe.start", run_id=run_id,
            result={"status": "info"},
            data={"task_id": work_order.task_id, "level": work_order.level,
                  "target": (work_order.target_files[0]
                             if len(work_order.target_files) == 1 else None),
                  "max_rounds": work_order.max_rounds,
                  "verify_command": work_order.verify_command, "dry_run": dry_run},
            root=root_path,
        )

    # Clarify Gate: gom moi diem mo ho vao DUNG 1 lan hoi, truoc khi ton request.
    problems = clarify.check(work_order, root=root_path)
    if problems:
        if log:
            ledger.append_event(
                "clarify.required", run_id=run_id,
                result={"status": "fail", "error": clarify.build_question(problems)[:400]},
                data={"problems": problems}, root=root_path,
            )
        raise clarify.ClarifyRequired(problems)

    # Plan Validator: ke hoach phai hop le truoc khi chay (Task 3.3).
    plan = plan_validator.build_recipe_c_plan(
        work_order, include_reviewer=reviewer is not None)
    verdict = plan_validator.validate_plan(plan)
    if not verdict["ok"]:
        raise work_order_module.WorkOrderRejected(
            [f"plan bi tu choi: {error}" for error in verdict["errors"]])
    if log:
        ledger.append_event(
            "plan.validated", run_id=run_id,
            result={"status": "pass"},
            data={"plan_id": plan["plan_id"],
                  "nodes": [node["type"] for node in plan["nodes"]],
                  "warnings": verdict["warnings"]}, root=root_path,
        )

    gate = permissions.PermissionGate(root_path, run_id=run_id, log=log)
    sink = permissions.FindingsSink(root_path, run_id=run_id, log=log)

    # Worker ngoai (opencode CLI) chi duoc chay trong thu muc tam NGOAI du an:
    #  - khong doc duoc file du an (noi dung file da nam trong prompt)
    #  - neu model van ghi duoc (qua shell) thi cung khong roi vao du an
    if hasattr(coder, "bind_workspace"):
        coder_root = Path(tempfile.mkdtemp(prefix=f"swarm_worker_{run_id}_",
                                           dir=str(Path(tempfile.gettempdir()) / "opencode")))
        coder.bind_workspace(root_path, coder_root)

    if len(work_order.target_files) != 1:
        raise work_order_module.WorkOrderRejected(
            [f"Phase 1 chi ho tro 1 target file, nhan duoc {len(work_order.target_files)}"]
        )
    target_rel = work_order.target_files[0]
    target_path = gate.project_path(target_rel)
    if not target_path.exists():
        raise work_order_module.WorkOrderRejected([f"file dich khong ton tai: {target_rel}"])

    # Snapshot byte-level cac file thuoc pham vi task: neu worker tu sua file
    # (agent co the dung tool edit/shell), ta phat hien va khoi phuc nguyen byte.
    guard_files = [target_rel] + [item for item in (work_order.sandbox_files or [])
                                 if item != target_rel]
    # Secret Scanner (Task 3.5): chan file nhay cam + file dich chua secret TRUOC
    # khi bat ky noi dung nao roi khoi may. Sandbox file co secret chi bi loai
    # khoi ban sao gui model (AgenticCoder tu bo qua), verify van chay local.
    secret_findings = secret_scan.scan_paths(guard_files, root_path)
    blocked_reasons: list[str] = []
    for finding in secret_findings:
        if finding["kind"] == "sensitive_path":
            blocked_reasons.append(
                f"file nhay cam khong duoc gui model: {finding['file']} ({finding['preview']})")
        elif finding["file"] == target_rel:
            blocked_reasons.append(
                f"file dich chua secret ({finding['kind']}) tai dong {finding['line']}: "
                f"{finding['file']}")
    if blocked_reasons:
        if log:
            ledger.append_event(
                "secret.blocked", run_id=run_id,
                result={"status": "blocked",
                        "error": "chan gui secret ra model"},
                data={"findings": secret_findings[:20], "files": guard_files},
                root=root_path,
            )
        raise work_order_module.WorkOrderRejected(blocked_reasons)

    snapshots: dict[str, bytes] = {}
    for relative in guard_files:
        guarded = gate.project_path(relative)
        if guarded.exists():
            snapshots[relative] = guarded.read_bytes()

    def restore_tampered() -> list[str]:
        tampered: list[str] = []
        for relative, data in snapshots.items():
            guarded = gate.project_path(relative)
            current = guarded.read_bytes() if guarded.exists() else None
            if current != data:
                tampered.append(relative)
                safe_writer.atomic_write(guarded, data)
        if tampered:
            report["worker_edits_detected"] += len(tampered)
            report["worker_edited_files"] = sorted(set(report["worker_edited_files"]) | set(tampered))
            if log:
                ledger.append_event(
                    "recipe.guard.restore", run_id=run_id,
                    result={"status": "blocked",
                            "error": "worker tu sua file ngoai pipeline -> da khoi phuc"},
                    data={"files": tampered}, root=root_path,
                )
        return tampered

    original_sha = safe_writer.sha256_file(target_path)

    started = time.perf_counter()
    report = {
        "task_id": work_order.task_id,
        "level": work_order.level,
        "file": target_rel,
        "status": None,
        "source_encoding": None,
        "max_rounds": work_order.max_rounds,
        "rounds": 0,
        "dry_run": dry_run,
        "applied": False,
        "rolled_back": False,
        "patch_level": None,
        "patch_declared": None,
        "patch_attempts": 0,
        "patch_downgraded": False,
        "draft_verify": None,
        "review": None,
        "apply": None,
        "requests": 0,
        "coder_calls": 0,
        "reviewer_calls": 0,
        "feedback": [],
        "original_sha256": original_sha,
        "final_sha256": original_sha,
        "file_changed": False,
        "error": None,
        "duration_ms": 0,
        "worker_edits_detected": 0,
        "worker_edited_files": [],
        "plan_ok": True,
        "hints": [],
        "budget": {
            "max_requests": max_requests,
            "max_seconds": max_seconds,
            "requests_used": 0,
            "elapsed_s": 0.0,
            "exceeded": None,
        },
        "fallback": {"attempts": [], "skipped": [], "failures": 0, "models": []},
        "secrets_blocked": [],
        "usage": {"in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0, "cost": 0.0},
        "session": [],
    }

    def requests_used() -> int:
        used = int(getattr(coder, "calls", 0) or 0)
        if reviewer is not None:
            used += int(getattr(reviewer, "calls", 0) or 0)
        return used

    def budget_check(*, starting: bool) -> bool:
        """False = da cham tran -> phai dung ngay, rollback (chua apply gi).

        `starting=True` (dau vong): khong duoc BAT DAU khi da dung het tran.
        `starting=False` (sau 1 lan goi): chi dung khi lan goi do da VUOT tran;
        neu vua dung bang tran thi van phai apply ban sua vua verify duoc.
        """
        elapsed = time.perf_counter() - started
        used = requests_used()
        report["budget"]["requests_used"] = used
        report["budget"]["elapsed_s"] = round(elapsed, 2)
        over_requests = used >= max_requests if starting else used > max_requests
        if over_requests:
            report["budget"]["exceeded"] = "requests"
            report["error"] = f"vuot tran chi phi: {used}/{max_requests} requests"
            return False
        if elapsed >= max_seconds:
            report["budget"]["exceeded"] = "time"
            report["error"] = f"vuot tran thoi gian: {elapsed:.0f}s/{max_seconds}s"
            return False
        return True

    # FallbackCoder tu kiem tra ngan sach truoc MOI lan doi model.
    if hasattr(coder, "limit_check"):
        coder.limit_check = lambda: requests_used() < max_requests

    # Doc file nguon: giu nguyen byte-level (xem _read_source).
    gate.assert_can_read(permissions.ROLE_LEAD, target_path)
    try:
        original, source_encoding = _read_source(target_path)
    except (OSError, ValueError) as exc:
        report["status"] = STATUS_REJECTED
        report["error"] = f"khong doc duoc file nguon: {exc}"
        report["duration_ms"] = int((time.perf_counter() - started) * 1000)
        if log:
            ledger.append_event(
                "recipe.done", run_id=run_id, duration_ms=report["duration_ms"],
                result={"status": "fail", "error": report["error"]},
                data={"task_id": work_order.task_id, "status": STATUS_REJECTED},
                root=root_path,
            )
        return report
    report["source_encoding"] = source_encoding

    current_content = original
    feedback = None

    # Memory Engine (Task 4.4): rut kinh nghiem tu ledger, chen vao prompt Coder.
    if use_memory:
        hints = memory.get_suggestions(root=root_path)
        if hints:
            report["hints"] = hints
            if hasattr(coder, "hints"):
                coder.hints = hints
            if log:
                ledger.append_event(
                    "memory.hints", run_id=run_id,
                    result={"status": "info"},
                    data={"hints": hints}, root=root_path,
                )

    def model_label() -> str | None:
        attempts = list(getattr(coder, "attempts", []) or [])
        if attempts:
            return attempts[-1].get("model")
        return getattr(coder, "model", None)

    for round_index in range(1, work_order.max_rounds + 1):
        # Tran chi phi/thoi gian: kiem tra TRUOC khi ton them request nao.
        if not budget_check(starting=True):
            if log:
                ledger.append_event(
                    "budget.exceeded", run_id=run_id,
                    result={"status": "fail", "error": report["error"]},
                    data=dict(report["budget"]), root=root_path,
                )
            break
        report["rounds"] = round_index
        round_started = time.perf_counter()

        # --- 1. Coder sinh patch ------------------------------------------
        try:
            patch = coder.patch(work_order, current_content=current_content,
                                feedback=feedback, round_index=round_index)
        except Exception as exc:                        # noqa: BLE001
            restore_tampered()
            if getattr(exc, "kind", "") == "budget":
                report["budget"]["exceeded"] = "requests"
                report["error"] = str(exc)[:300]
                if log:
                    ledger.append_event(
                        "budget.exceeded", run_id=run_id,
                        result={"status": "fail", "error": report["error"]},
                        data=dict(report["budget"]), root=root_path,
                    )
                break
            feedback = _feedback("coder", str(exc)[:300], "", round_index)
            report["feedback"].append(feedback)
            if log:
                ledger.append_event(
                    "recipe.coder", run_id=run_id,
                    duration_ms=int((time.perf_counter() - round_started) * 1000),
                    result={"status": "fail", "error": str(exc)[:300]},
                    data={"round": round_index, "kind": getattr(exc, "kind", "error"),
                          "model": model_label()}, root=root_path,
                )
            continue

        restore_tampered()          # worker khong duoc de lai dau vet trong du an
        if log:
            ledger.append_event(
                "recipe.coder", run_id=run_id,
                duration_ms=int((time.perf_counter() - round_started) * 1000),
                result={"status": "ok"},
                data={"round": round_index, "model": model_label(),
                      "fallback_attempts": len(getattr(coder, "attempts", []) or [])},
                root=root_path,
            )
        if not budget_check(starting=False):
            # Mot lan fallback co the dot nhieu request: dung ngay, chua apply gi.
            if log:
                ledger.append_event(
                    "budget.exceeded", run_id=run_id,
                    result={"status": "fail", "error": report["error"]},
                    data=dict(report["budget"]), root=root_path,
                )
            break

        # --- 2. Format ladder -> ban nhap -------------------------------
        try:
            ladder = format_ladder.apply_patch(current_content, patch)
        except format_ladder.PatchFailed as exc:
            feedback = _feedback("format_ladder", str(exc)[:400], "", round_index)
            report["feedback"].append(feedback)
            if log:
                ledger.append_event(
                    "recipe.ladder", run_id=run_id,
                    result={"status": "fail", "error": str(exc)[:300]},
                    data={"round": round_index, "history": exc.history}, root=root_path,
                )
            continue

        new_content = ladder["content"]
        report["patch_level"] = ladder["level_name"]
        report["patch_declared"] = format_ladder.LEVEL_NAME[ladder["declared_level"]]
        report["patch_attempts"] = ladder["attempts"]
        report["patch_downgraded"] = ladder["downgraded"]

        # --- 3. Ghi ban nhap (Coder: CHI duoc ghi trong .agents/draft) ---
        draft_path = gate.draft_path(work_order.task_id or "task", target_rel)
        gate.assert_can_write(permissions.ROLE_CODER, draft_path)
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            draft_path.write_text(new_content, encoding=source_encoding, newline="")
        except (OSError, UnicodeEncodeError) as exc:
            feedback = _feedback("ghi-ban-nhap", str(exc)[:300], "", round_index)
            report["feedback"].append(feedback)
            continue

        # --- 4. SystemBot verify ban nhap --------------------------------
        draft_verify = _verify_draft(
            work_order, gate=gate, root=root_path, run_id=run_id,
            round_index=round_index, target_rel=target_rel, content=new_content,
            draft_path=draft_path, verify_timeout=verify_timeout, log=log,
            encoding=source_encoding,
        )
        report["draft_verify"] = {key: value for key, value in draft_verify.items()}
        if draft_verify["exit_code"] != 0:
            feedback = _feedback(
                f"verify-ban-nhap ({draft_verify['mode']})",
                str(draft_verify.get("error") or "verify that bai"),
                _tail(draft_verify.get("output_tail")), round_index,
            )
            report["feedback"].append(feedback)
            if log:
                ledger.append_event(
                    "recipe.draft_verify", run_id=run_id,
                    duration_ms=draft_verify["duration_ms"],
                    result={"status": "fail", "error": draft_verify.get("error")},
                    data={"round": round_index, "mode": draft_verify["mode"],
                          "exit_code": draft_verify["exit_code"],
                          "output_tail": draft_verify.get("output_tail")},
                    root=root_path,
                )
            continue

        if log:
            ledger.append_event(
                "recipe.draft_verify", run_id=run_id,
                duration_ms=draft_verify["duration_ms"],
                result={"status": "pass"},
                data={"round": round_index, "mode": draft_verify["mode"],
                      "exit_code": 0, "ladder_level": ladder["level_name"]},
                root=root_path,
            )

        # --- 5. Reviewer doc diff (Level 2, read-only) -------------------
        if work_order.level >= 2 and reviewer is not None:
            diff = _diff(original, new_content, target_rel)
            review = reviewer.review(
                work_order, diff=diff, new_content=new_content,
                old_content=current_content, level=work_order.level, sink=sink,
            )
            report["review"] = dict(review)
            if review.get("verdict") != "approve":
                feedback = _feedback(
                    "reviewer", "bi tu choi", "; ".join(review.get("reasons") or [])[:400],
                    round_index,
                )
                report["feedback"].append(feedback)
                continue

        # --- 6. Lead apply an toan --------------------------------------
        if dry_run:
            report["status"] = STATUS_DRY_RUN
            report["final_sha256"] = original_sha
            break

        gate.assert_can_write(permissions.ROLE_LEAD, target_path)
        restore_tampered()          # kiem tra lan cuoi truoc khi ghi that
        try:
            apply_report = safe_writer.write_safely(
                target_path, new_content,
                verify_cmd=work_order.render_verify_command(target_rel),
                verify_timeout=verify_timeout or system_bot.DEFAULT_TIMEOUT,
                cwd=root_path, root=root_path, run_id=run_id, encoding=source_encoding,
                reason=f"recipe_c:{work_order.task_id}:r{round_index}", log=log,
            )
        except (OSError, UnicodeEncodeError, RuntimeError) as exc:
            # Ghi that bai truoc khi os.replace -> file goc van nguyen ven.
            feedback = _feedback("apply-write", str(exc)[:300], "", round_index)
            report["feedback"].append(feedback)
            report["error"] = f"ghi file that bai: {exc}"
            continue
        report["apply"] = {
            "written": apply_report["written"],
            "integrity_ok": apply_report["integrity_ok"],
            "verified": apply_report["verified"],
            "rolled_back": apply_report["rolled_back"],
            "sha256": apply_report["sha256"],
        }

        if apply_report["verified"]:
            report["status"] = STATUS_APPLIED
            report["applied"] = True
            report["final_sha256"] = apply_report["final_sha256"]
            report["file_changed"] = apply_report["final_sha256"] != original_sha
            # Lead vua ghi hop le -> moc kiem soat phai la noi dung MOI, neu khong
            # buoc chot ha tang se tuong worker da sua file va khoi phuc ban cu.
            for relative in list(snapshots):
                guarded = gate.project_path(relative)
                if guarded.exists():
                    snapshots[relative] = guarded.read_bytes()
            break

        report["rolled_back"] = report["rolled_back"] or apply_report["rolled_back"]
        verify_info = apply_report.get("verify") or {}
        feedback = _feedback(
            "apply-verify",
            f"exit_code={verify_info.get('exit_code')}",
            _tail(verify_info.get("output_tail")), round_index,
        )
        report["feedback"].append(feedback)

    if report["status"] is None:
        report["status"] = STATUS_ABORTED
        if not report["error"]:
            report["error"] = ("het so vong (circuit breaker) hoac khong co ban sua nao verify duoc")

    # Chot ha tang: file phai dung trang thai mong doi.
    restore_tampered()
    if target_path.exists():
        report["final_sha256"] = safe_writer.sha256_file(target_path)
        report["file_changed"] = report["final_sha256"] != report["original_sha256"]
    report["duration_ms"] = int((time.perf_counter() - started) * 1000)
    report["coder_calls"] = getattr(coder, "calls", 0)
    report["reviewer_calls"] = getattr(reviewer, "calls", 0) if reviewer else 0
    report["requests"] = report["coder_calls"] + report["reviewer_calls"]
    report["budget"]["requests_used"] = report["requests"]
    report["budget"]["elapsed_s"] = round((time.perf_counter() - started), 2)

    # Bao cao fallback / secret / session (Tasks 3.1-3.5).
    stats = coder.stats() if hasattr(coder, "stats") else {}
    attempts = list(stats.get("attempts") or [])
    report["fallback"] = {
        "attempts": attempts,
        "skipped": list(stats.get("skipped") or []),
        "failures": sum(1 for item in attempts if item.get("status") == "fail"),
        "models": list(stats.get("models") or []),
    }
    report["session"] = list(getattr(coder, "session_events", []) or [])
    if not report["session"]:
        children = getattr(coder, "_children", {}) or {}
        for child in children.values():
            report["session"].extend(getattr(child, "session_events", []) or [])
    blocked: list[dict] = list(getattr(coder, "secrets_blocked", []) or [])
    for child in (getattr(coder, "_children", {}) or {}).values():
        blocked.extend(getattr(child, "secrets_blocked", []) or [])
    report["secrets_blocked"] = blocked

    usage = {"in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0, "cost": 0.0}
    for worker in (coder, reviewer):
        source = getattr(worker, "usage", None) or {}
        usage["in_tokens"] += int(source.get("in_tokens", source.get("input", 0)) or 0)
        usage["out_tokens"] += int(source.get("out_tokens", source.get("output", 0)) or 0)
        usage["cache_read_tokens"] += int(source.get("cache_read_tokens", 0) or 0)
        usage["cost"] += float(source.get("cost", 0) or 0)
    report["usage"] = usage

    if report["status"] == STATUS_ABORTED and report["file_changed"]:
        report["error"] = (report["error"] or "") + " | CANH BAO: file da doi nhung pipeline abort"

    if log and report["fallback"]["attempts"]:
        ledger.append_event(
            "recipe.fallback", run_id=run_id,
            result={"status": "info"},
            data={"failures": report["fallback"]["failures"],
                  "models": report["fallback"]["models"],
                  "attempts": report["fallback"]["attempts"],
                  "skipped": report["fallback"]["skipped"]},
            root=root_path,
        )

    if log:
        ledger.append_event(
            "recipe.done", run_id=run_id,
            duration_ms=report["duration_ms"],
            usage=usage,
            result={"status": "pass" if report["status"] != STATUS_ABORTED else "fail",
                    "error": report["error"]},
            data={"task_id": work_order.task_id, "status": report["status"],
                  "rounds": report["rounds"], "patch_level": report["patch_level"],
                  "requests": report["requests"], "applied": report["applied"],
                  "rolled_back": report["rolled_back"],
                  "fallback_attempts": report["fallback"]["failures"],
                  "models": report["fallback"]["models"],
                  "budget_exceeded": report["budget"]["exceeded"],
                  "secrets_blocked": len(blocked),
                  "plan_ok": report["plan_ok"]},
            root=root_path,
        )
    return report


def _diff(old: str, new: str, path: str) -> str:
    return unified_diff(old, new, path=path)


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Recipe C runner.")
    parser.add_argument("--order", required=True, help="file JSON chua Work Order")
    parser.add_argument("--root", default=".")
    parser.add_argument("--coder", default="agentic",
                        choices=("agentic", "opencode", "scripted"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--reviewer", default="heuristic", choices=("heuristic", "llm", "none"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-fallback", action="store_true",
                        help="tat chuoi du phong, ep dung 1 model")
    parser.add_argument("--max-requests", type=int, default=None,
                        help=f"tran request moi run (toi da {BUDGET_MAX_REQUESTS}, chi ha duoc)")
    parser.add_argument("--max-seconds", type=int, default=None,
                        help=f"tran thoi gian moi run (toi da {BUDGET_MAX_SECONDS}s, chi ha duoc)")
    args = parser.parse_args(argv)

    order_data = json.loads(Path(args.order).read_text(encoding="utf-8"))
    order = work_order_module.WorkOrder.from_dict(order_data)
    order.validate()

    import workers
    if args.coder == "scripted":
        coder = workers.ScriptedCoder({order.task_id: order_data.get("scripted_patches") or []})
    elif args.coder == "opencode":
        model = args.model or _model_from_config("coder")
        coder = workers.OpenCodeCoder(model)
    else:
        models = [args.model] if args.model else _chain_from_config("coder")
        if args.no_fallback or len(models) <= 1:
            coder = workers.AgenticCoder(models[0])
        else:
            import fallback
            health_path = Path(__file__).resolve().parent / "tmp" / "health.json"
            health = fallback.HealthRegistry(health_path)
            coder = fallback.FallbackCoder(
                models, factory=lambda model: workers.AgenticCoder(model), health=health)
    reviewer = workers.build_reviewer(args.reviewer, model=_model_from_config("reviewer")
                                      if args.reviewer == "llm" else None)

    report = run(order, coder=coder, reviewer=reviewer, root=args.root, dry_run=args.dry_run,
                 max_requests=args.max_requests, max_seconds=args.max_seconds)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"task   : {report['task_id']} (Level {report['level']})")
        print(f"status : {report['status']}")
        print(f"rounds : {report['rounds']}/{report['max_rounds']}  bac={report['patch_level']}")
        print(f"file   : {report['file']}  changed={report['file_changed']}")
        if report["error"]:
            print(f"error  : {report['error']}")
    return 0 if report["status"] in (STATUS_APPLIED, STATUS_DRY_RUN) else 1


def _chain_from_config(role: str) -> list[str]:
    """Chuoi model cua 1 vai tro tu config.json (primary dung dau)."""
    config_path = Path(__file__).resolve().parent / "config.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        entry = data["roles"][role]
    except Exception as exc:                           # noqa: BLE001
        raise SystemExit(f"khong doc duoc model cho role '{role}' tu {config_path}: {exc}")
    chain: list[str] = []
    for item in entry.get("chain") or []:
        model = str(item["model"]) if isinstance(item, dict) else str(item)
        if model and model not in chain:
            chain.append(model)
    primary = entry.get("primary")
    if primary and str(primary) not in chain:
        chain.insert(0, str(primary))
    if not chain:
        raise SystemExit(f"role '{role}' khong co model nao trong {config_path}")
    return chain


def _model_from_config(role: str) -> str:
    """Doc model primary cua 1 vai tro tu config.json (scout ghi ra)."""
    return _chain_from_config(role)[0]


if __name__ == "__main__":
    sys.exit(main())
