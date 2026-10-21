#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 6A - Job plan + duyet tren dashboard + chay tuan tu.

Luong:
    job.py --plan plan.json --submit --wait
      -> ghi .agents/tmp/plan.json + plan_decision.json (pending) + token
      -> in URL dashboard kem token
      -> cho nguoi dung bam Duyet/Tu choi tren 127.0.0.1
      -> approved: chay tung subtask (moi subtask 1 Work Order + 1 transaction)
      -> rejected/timeout: khong ghi gi

Moi subtask tu duoc router W0-W8 chon model theo loai viec + quota that.

Plan schema:
{
  "job_id": "J1",
  "objective": "...",
  "subtasks": [
    {"id": "S1", "title": "...", "objective": "...", "boundaries": "...",
     "input_context": "...", "acceptance_criteria": "...",
     "verify_command": "python check.py", "target_files": ["a.py"],
     "sandbox_files": ["a.py", "check.py"], "level": 1, "max_rounds": 1,
     "depends_on": []}
  ]
}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ledger                        # noqa: E402
import notify                        # noqa: E402
import recipe_c                      # noqa: E402
import router                        # noqa: E402
import workers                       # noqa: E402
import work_order as work_order_module  # noqa: E402

MAX_SUBTASKS = 12
DEFAULT_TIMEOUT = 600


# --------------------------------------------------------------------------
# Duong dan + doc/ghi
# --------------------------------------------------------------------------
def _dir(root) -> Path:
    return Path(root).resolve() / ".agents" / "tmp"


def plan_path(root) -> Path:
    return _dir(root) / "plan.json"


def decision_path(root) -> Path:
    return _dir(root) / "plan_decision.json"


def token_path(root) -> Path:
    return _dir(root) / "plan_token.txt"


def state_path(root) -> Path:
    return _dir(root) / "job_state.json"


def control_path(root) -> Path:
    return _dir(root) / "control.json"


def load_plan(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_plan_state(root) -> dict:
    """Trang thai cho dashboard: plan + decision (KHONG tra token)."""
    state: dict = {"plan": None, "decision": None,
                   "root": str(Path(root).resolve())}
    try:
        state["plan"] = json.loads(plan_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    try:
        state["decision"] = json.loads(decision_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return state


# --------------------------------------------------------------------------
# State + control (dashboard <-> job)
# --------------------------------------------------------------------------
DEFAULT_CONTROL = {"paused": False, "stop": False, "opinion": ""}
_LOCK = threading.Lock()


def _write_json(path: Path, payload: dict) -> None:
    """Ghi JSON atomic: tmp + os.replace (khong de file do dang)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_state(root) -> dict:
    try:
        return json.loads(state_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(root, payload: dict) -> dict:
    payload = dict(payload)
    payload["updated_at"] = ledger.utc_now_iso()
    _write_json(state_path(root), payload)
    return payload


def read_control(root) -> dict:
    try:
        data = json.loads(control_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONTROL)
    return {**DEFAULT_CONTROL, **data} if isinstance(data, dict) else dict(DEFAULT_CONTROL)


def write_control(root, payload: dict) -> dict:
    payload = dict(payload)
    payload["updated_at"] = ledger.utc_now_iso()
    _write_json(control_path(root), payload)
    return payload


def _refresh_plan_md(root) -> None:
    """Best-effort refresh of the long-form plan file."""
    try:
        import plan_md
        plan_state = read_plan_state(root)
        plan = plan_state.get("plan")
        if plan:
            plan_md.write_markdown(root, plan, plan_state.get("decision"), read_state(root))
    except Exception:                       # noqa: BLE001
        pass


def _existing_token(root, job_id: str | None) -> str:
    """Reuse the token when the same job is re-submitted (stale dashboard tabs)."""
    try:
        old_plan = json.loads(plan_path(root).read_text(encoding="utf-8"))
        old_token = token_path(root).read_text(encoding="utf-8").strip()
    except (OSError, json.JSONDecodeError):
        return ""
    if old_token and str(old_plan.get("job_id") or "") == str(job_id or ""):
        return old_token
    return ""


def _require_token(root, token: str) -> None:
    try:
        expected = token_path(root).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError("no plan submitted yet") from exc
    if not token or not secrets.compare_digest(str(token), expected):
        raise PermissionError("token mismatch")


def set_control(root, action: str, *, token: str, value: str | None = None) -> dict:
    """Dashboard -> job: pause / resume / stop / opinion (token bat buoc)."""
    _require_token(root, token)
    action = str(action or "").lower()
    with _LOCK:
        control = read_control(root)
        if action == "pause":
            control["paused"] = True
        elif action == "resume":
            control["paused"] = False
        elif action == "stop":
            control["stop"] = True
        elif action == "opinion":
            control["opinion"] = str(value or "")[:2000]
        else:
            raise ValueError(f"unknown action: {action}")
        write_control(root, control)
    ledger.append_event("control.updated", run_id=f"job_{read_state(root).get('job_id')}",
                        result={"status": "info"},
                        data={"action": action, "paused": control["paused"],
                              "stop": control["stop"],
                              "opinion": control["opinion"][:200]}, root=root)
    return control


def wait_for_resume(root, *, timeout: float = 900, poll: float = 1.0) -> str:
    """Cho nguoi dung Resume tren dashboard. Tra resumed / stop / timeout."""
    deadline = time.time() + max(1.0, float(timeout))
    while time.time() < deadline:
        control = read_control(root)
        if control.get("stop"):
            return "stop"
        if not control.get("paused"):
            return "resumed"
        time.sleep(poll)
    return "timeout"


# --------------------------------------------------------------------------
# Drafts (read-only for the dashboard)
# --------------------------------------------------------------------------
def drafts_root(root) -> Path:
    return Path(root).resolve() / ".agents" / "draft"


def list_drafts(root, job_id: str) -> list[dict]:
    base = drafts_root(root)
    items: list[dict] = []
    if not base.exists():
        return items
    if not re.fullmatch(r"[\w.-]+", str(job_id or "")):
        return items
    for path in base.glob(f"{job_id}-*/**/*"):
        if not path.is_file():
            continue
        items.append({"path": str(path.relative_to(base)).replace("\\", "/"),
                      "size": path.stat().st_size})
    return sorted(items, key=lambda item: item["path"])


def read_draft(root, relative: str) -> str:
    base = drafts_root(root).resolve()
    target = (base / relative).resolve()
    if base != target and base not in target.parents:
        raise ValueError("path escapes draft directory")
    if not target.is_file():
        raise FileNotFoundError(relative)
    return target.read_text(encoding="utf-8", errors="replace")
def validate_plan(plan: dict) -> list[str]:
    problems: list[str] = []
    if not isinstance(plan, dict):
        return ["plan phai la object JSON"]
    if not str(plan.get("job_id") or "").strip():
        problems.append("thieu job_id")
    if not str(plan.get("objective") or "").strip():
        problems.append("thieu objective")
    subtasks = plan.get("subtasks")
    if not isinstance(subtasks, list) or not subtasks:
        return problems + ["can >= 1 subtask"]
    if len(subtasks) > MAX_SUBTASKS:
        problems.append(f"qua nhieu subtask ({len(subtasks)} > {MAX_SUBTASKS})")

    ids: list[str] = []
    for index, item in enumerate(subtasks):
        label = f"subtask[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{label}: phai la object")
            continue
        sub_id = str(item.get("id") or "").strip()
        if not sub_id:
            problems.append(f"{label}: thieu id")
        elif sub_id in ids:
            problems.append(f"{label}: id trung lap {sub_id}")
        else:
            ids.append(sub_id)
        for field in ("objective", "verify_command", "target_files"):
            if not item.get(field):
                problems.append(f"{label}: thieu {field}")
        for field in ("target_files", "sandbox_files"):
            for relative in item.get(field) or []:
                path = Path(str(relative))
                if path.is_absolute() or ".." in path.parts:
                    problems.append(f"{label}: path escapes project: {relative}")

    for item in subtasks:
        if not isinstance(item, dict):
            continue
        for dep in item.get("depends_on") or []:
            if dep not in ids:
                problems.append(f"{item.get('id')}: depends_on {dep} khong ton tai")
    if _has_cycle(subtasks):
        problems.append("depends_on bi vong lap")

    # Validate tung subtask nhu Work Order that (verify qua SystemBot, level/file).
    for item in subtasks:
        if not isinstance(item, dict) or not item.get("verify_command"):
            continue
        try:
            to_order(item, plan)
        except Exception as exc:                    # noqa: BLE001
            problems.append(f"{item.get('id')}: {str(exc)[:200]}")
    return problems


def _has_cycle(subtasks: list[dict]) -> bool:
    graph = {item.get("id"): list(item.get("depends_on") or [])
             for item in subtasks if isinstance(item, dict)}
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        for dep in graph.get(node, []):
            if dep in graph and visit(dep):
                return True
        state[node] = 2
        return False

    return any(visit(node) for node in graph)


def topo_order(subtasks: list[dict]) -> list[dict]:
    """Thu tu kha thi, giu thu tu goc khi khong co phu thuoc."""
    by_id = {item["id"]: item for item in subtasks}
    done: set[str] = set()
    ordered: list[dict] = []
    while len(ordered) < len(subtasks):
        progressed = False
        for item in subtasks:
            if item["id"] in done:
                continue
            if all(dep in done for dep in (item.get("depends_on") or [])):
                ordered.append(item)
                done.add(item["id"])
                progressed = True
        if not progressed:
            raise ValueError("depends_on bi vong lap")
    return ordered


def to_order(subtask: dict, plan: dict):
    data = {
        "task_id": f"{plan.get('job_id')}-{subtask['id']}",
        "title": subtask.get("title") or subtask["id"],
        "objective": subtask.get("objective") or plan.get("objective", ""),
        "boundaries": subtask.get("boundaries") or f"Chi sua {', '.join(subtask['target_files'])}",
        "input_context": subtask.get("input_context") or ", ".join(subtask.get("sandbox_files") or subtask["target_files"]),
        "acceptance_criteria": subtask.get("acceptance_criteria")
        or f"verify exit 0: {subtask['verify_command']}",
        "verify_command": subtask["verify_command"],
        "target_files": list(subtask["target_files"]),
        "sandbox_files": list(subtask.get("sandbox_files") or subtask["target_files"]),
        "level": int(subtask.get("level", 1)),
        "max_rounds": int(subtask.get("max_rounds", 1)),
    }
    return work_order_module.create(**data)


# --------------------------------------------------------------------------
# Duyet
# --------------------------------------------------------------------------
def submit_plan(plan: dict, root, *, token: str | None = None, source: str | None = None,
                log: bool = True, force: bool = False) -> dict:
    problems = validate_plan(plan)
    if problems:
        raise ValueError("plan khong hop le: " + "; ".join(problems[:8]))
    if source:
        plan["_source"] = str(source)
    directory = _dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    token = token or _existing_token(root, plan.get("job_id")) or secrets.token_hex(8)

    with _LOCK:
        if not force:
            phase = read_state(root).get("phase")
            if phase in ("running", "paused"):
                raise ValueError("a job is running; use --force to replace it")
        _write_json(plan_path(root), plan)
        token_path(root).write_text(token, encoding="utf-8")
        _write_json(decision_path(root),
                    {"status": "pending", "at": ledger.utc_now_iso()})
        write_control(root, dict(DEFAULT_CONTROL))
        write_state(root, {
            "job_id": plan.get("job_id"),
            "phase": "awaiting_approval",
            "index": 0,
            "total": len(plan["subtasks"]),
            "current": None,
            "subtasks": [{"id": item.get("id"), "status": "pending",
                          "files": item.get("target_files") or []}
                         for item in plan["subtasks"]],
            "team": {},
        })

    if log:
        ledger.append_event("plan.submitted", run_id=f"job_{plan.get('job_id')}",
                            result={"status": "info"},
                            data={"job_id": plan.get("job_id"),
                                  "subtasks": [item.get("id") for item in plan["subtasks"]]},
                            root=root)
    notify.notify("plan.submitted", {"job_id": plan.get("job_id"),
                                     "subtasks": len(plan["subtasks"])}, root=root)
    _refresh_plan_md(root)
    return {"job_id": plan.get("job_id"), "token": token,
            "plan_path": str(plan_path(root)), "subtasks": len(plan["subtasks"])}


def decide(root, decision: str, *, token: str, log: bool = True) -> dict:
    decision = str(decision or "").lower()
    if decision not in ("approve", "reject"):
        raise ValueError("decision phai la approve hoac reject")
    expected = ""
    try:
        expected = token_path(root).read_text(encoding="utf-8").strip()
    except OSError:
        raise ValueError("chua co plan nao duoc submit")
    if not token or not secrets.compare_digest(str(token), expected):
        raise PermissionError("token khong dung")
    with _LOCK:
        state = read_plan_state(root)
        current = (state.get("decision") or {}).get("status")
        if current not in (None, "pending"):
            raise ValueError(f"plan da duoc quyet dinh: {current}")
        status = "approved" if decision == "approve" else "rejected"
        payload = {"status": status, "at": ledger.utc_now_iso(),
                   "job_id": (state.get("plan") or {}).get("job_id")}
        _write_json(decision_path(root), payload)
        job_state = read_state(root)
        if job_state:
            write_state(root, {**job_state, "phase": status})
    if log:
        ledger.append_event("plan.approved" if status == "approved" else "plan.rejected",
                            run_id=f"job_{payload.get('job_id')}",
                            result={"status": "pass" if status == "approved" else "fail"},
                            data={"job_id": payload["job_id"]}, root=root)
    notify.notify("plan.approved" if status == "approved" else "plan.rejected",
                  {"job_id": payload.get("job_id")}, root=root)
    _refresh_plan_md(root)
    return payload


_RUN_LOCK = threading.Lock()
_RUN_PROC: subprocess.Popen | None = None


def start_run(root, *, token: str, reviewer: str = "heuristic",
              model: str | None = None) -> dict:
    """Start the already-approved plan in a background process."""
    global _RUN_PROC
    _require_token(root, token)
    reviewer = str(reviewer or "heuristic").lower()
    if reviewer not in ("heuristic", "wave", "none"):
        raise ValueError("reviewer phai la heuristic, wave hoac none")
    with _RUN_LOCK:
        if _RUN_PROC is not None and _RUN_PROC.poll() is None:
            raise ValueError("a job is already running")
        state = read_state(root)
        if state.get("phase") in ("running", "paused"):
            raise ValueError("a job is already running")
        decision = (read_plan_state(root).get("decision") or {}).get("status")
        if decision != "approved":
            raise ValueError("plan is not approved")
        try:
            plan = json.loads(plan_path(root).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("no readable plan to resume") from exc
        if not isinstance(plan, dict) or not plan.get("subtasks"):
            raise ValueError("no readable plan to resume")
        out_dir = _dir(root)
        out_dir.mkdir(parents=True, exist_ok=True)
        log_path = out_dir / ("run_" + time.strftime("%Y%m%d-%H%M%S") + ".log")
        cmd = [sys.executable, str(Path(__file__).resolve()), "--resume",
               "--root", str(Path(root).resolve()), "--reviewer", reviewer]
        if model:
            cmd += ["--model", str(model)]
        creationflags = 0x08000000 if os.name == "nt" else 0
        with log_path.open("w", encoding="utf-8") as log:
            _RUN_PROC = subprocess.Popen(
                cmd, cwd=str(Path(root).resolve()), stdout=log,
                stderr=subprocess.STDOUT, creationflags=creationflags)
        return {"ok": True, "pid": _RUN_PROC.pid, "log": str(log_path),
                "reviewer": reviewer, "job_id": plan.get("job_id")}


def wait_for_decision(root, *, timeout: float = DEFAULT_TIMEOUT,
                      poll: float = 1.0) -> str:
    deadline = time.time() + max(1.0, float(timeout))
    while time.time() < deadline:
        state = read_plan_state(root)
        status = (state.get("decision") or {}).get("status")
        if status in ("approved", "rejected"):
            return status
        time.sleep(poll)
    return "timeout"


def ensure_dashboard(root, port: int):
    """Khoi dong dashboard local neu port con trong. Tra (server, started)."""
    import dashboard
    try:
        server = dashboard.make_server(port, root=root)
    except OSError:
        return None, False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, True


# --------------------------------------------------------------------------
# Chay job
# --------------------------------------------------------------------------
def _default_coder(chain: list[str]):
    import fallback
    if len(chain) <= 1:
        return workers.AgenticCoder(chain[0])
    health = fallback.HealthRegistry(HERE / "tmp" / "health.json")
    return fallback.FallbackCoder(chain, factory=lambda model: workers.AgenticCoder(model),
                                  health=health)


def run_job(plan: dict, *, root, coder_factory=None, reviewer=None,
            verify_timeout: int | None = None, log: bool = True,
            use_router: bool = True, allow_escalate: bool = False,
            force: bool = False, dry_run: bool = False,
            reviewer_kind: str = "heuristic", model: str | None = None) -> dict:
    problems = validate_plan(plan)
    if problems:
        raise ValueError("plan khong hop le: " + "; ".join(problems[:8]))

    import quota
    tracker = quota.QuotaTracker(root=root).refresh() if use_router else None
    started = time.perf_counter()
    records: list[dict] = []
    status = "applied"
    ordered = topo_order(plan["subtasks"])

    state = write_state(root, {
        "job_id": plan.get("job_id"),
        "phase": "running",
        "index": 0,
        "total": len(ordered),
        "current": None,
        "subtasks": [{"id": item["id"], "status": "pending",
                      "files": item.get("target_files") or []} for item in ordered],
        "team": {"reviewer": getattr(reviewer, "kind", None) or
                 {"wave": "wave-reviewer", "none": "none"}.get(reviewer_kind, "heuristic")},
    })

    def update_state(**changes) -> dict:
        nonlocal state
        state = write_state(root, {**state, **changes})
        return state

    if log:
        ledger.append_event("job.start", run_id=f"job_{plan.get('job_id')}", result={"status": "info"},
                            data={"job_id": plan.get("job_id"),
                                  "subtasks": len(plan["subtasks"])}, root=root)

    review = (reviewer if reviewer is not None
              else workers.build_reviewer(reviewer_kind))

    for index, item in enumerate(ordered, start=1):
        control = read_control(root)
        if control.get("stop"):
            status = "stopped"
            update_state(phase="stopped", current=None)
            break
        if control.get("paused"):
            update_state(phase="paused", current=item["id"], index=index)
            waited = wait_for_resume(root, timeout=3600)
            if waited != "resumed":
                status = "stopped" if waited == "stop" else "paused"
                update_state(phase="stopped" if waited == "stop" else "paused")
                break
            update_state(phase="running")

        if tracker is not None and hasattr(tracker, "refresh"):
            tracker.refresh()
        update_state(phase="running", index=index, current=item["id"])
        order = to_order(item, plan)
        opinion = str(control.get("opinion") or "").strip()
        if opinion:
            order.input_context = (f"USER DIRECTION (must follow): {opinion}\n"
                                   f"{order.input_context}")
            write_control(root, {**control, "opinion": ""})
        route_plan = (router.route(order, quota=tracker, allow_escalate=allow_escalate)
                      if use_router else None)
        if route_plan and route_plan.get("exhausted") and not force:
            records.append({"id": item["id"], "status": "blocked",
                            "error": "out of quota", "route": route_plan})
            status = "blocked"
            update_state(phase="stopped", current=None)
            break

        chain = [model] if model else (route_plan["chain"] if route_plan else [])
        coder = (coder_factory(order, chain) if coder_factory
                 else _default_coder(chain))
        sub_started = time.perf_counter()
        try:
            report = recipe_c.run(order, coder=coder, reviewer=review,
                                  root=root, verify_timeout=verify_timeout, log=log,
                                  dry_run=dry_run, route_plan=route_plan)
        except Exception as exc:                    # noqa: BLE001
            records.append({"id": item["id"], "status": "error",
                            "error": str(exc)[:300], "route": route_plan})
            status = "error"
            update_state(phase="stopped", current=None)
            break
        records.append({
            "id": item["id"],
            "task_id": order.task_id,
            "status": report.get("status"),
            "requests": report.get("requests"),
            "seconds": round(time.perf_counter() - sub_started, 1),
            "file": report.get("file"),
            "route": route_plan,
            "error": report.get("error"),
        })
        entry = records[-1]
        team = state.get("team") or {}
        if chain:
            team = {"coder": chain[0],
                    "fallback": chain[1] if len(chain) > 1 else None,
                    "reviewer": getattr(review, "kind", "reviewer"),
                    "verify": order.verify_command}
        subtasks_state = [dict(sub) for sub in state.get("subtasks", [])]
        for sub in subtasks_state:
            if sub["id"] == item["id"]:
                sub.update({"status": entry["status"], "file": entry.get("file"),
                            "requests": entry.get("requests"), "chain": list(chain)})
        update_state(subtasks=subtasks_state, team=team)
        if report.get("status") not in ("applied", "dry-run"):
            status = "stopped"
            update_state(phase="stopped", current=None)
            break

    payload = {
        "job_id": plan.get("job_id"),
        "status": status,
        "subtasks": records,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    final_phase = ("done" if status in ("applied", "dry-run")
                   else "paused" if status == "paused" else "stopped")
    update_state(phase=final_phase, status=status, current=None)
    if log:
        ledger.append_event("job.done", run_id=f"job_{plan.get('job_id')}",
                            result={"status": "pass" if status == "applied" else "fail",
                                    "error": None if status == "applied" else status},
                            data={"job_id": plan.get("job_id"), "status": status,
                                  "subtasks": [{"id": r["id"], "status": r["status"]}
                                               for r in records]},
                            root=root)
    notify.notify("job.done", {"job_id": plan.get("job_id"), "status": status,
                               "seconds": round(payload["duration_ms"] / 1000, 1)},
                  root=root)
    _refresh_plan_md(root)
    return payload


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Job plan + duyet dashboard + chay tuan tu.")
    parser.add_argument("--plan", default=None)
    parser.add_argument("--resume", action="store_true",
                        help="chay plan da duoc approve trong tmp/plan.json (khong submit lai)")
    parser.add_argument("--root", default=".")
    parser.add_argument("--token", default=None, help="token co dinh (mac dinh: random)")
    parser.add_argument("--decision", choices=("approve", "reject"), default=None,
                        help="duyet ngay tren CLI (headless), bo qua dashboard")
    parser.add_argument("--wait", action="store_true", help="cho dashboard duyet")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--allow-escalate", action="store_true")
    parser.add_argument("--reviewer", default="heuristic",
                        choices=("heuristic", "wave", "none"))
    parser.add_argument("--model", default=None,
                        help="override the coder model (useful for free dry-runs)")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--open", action="store_true",
                        help="tu khoi dong dashboard neu can va mo trinh duyet")
    args = parser.parse_args(argv)

    try:
        if args.resume:
            try:
                plan = json.loads(plan_path(args.root).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"cannot read saved plan: {exc}")
                return 2
            try:
                token = token_path(args.root).read_text(encoding="utf-8").strip()
            except OSError:
                token = ""
            info = {"job_id": plan.get("job_id"), "token": token,
                    "subtasks": len(plan.get("subtasks") or [])}
            print(f"job     : {info['job_id']} (resume, {info['subtasks']} subtask)")
        else:
            if not args.plan:
                print("--plan is required unless --resume")
                return 2
            plan = load_plan(args.plan)
            info = submit_plan(plan, args.root, token=args.token, source=args.plan,
                               force=args.force)
            print(f"job     : {info['job_id']} ({info['subtasks']} subtask)")
    except OSError as exc:
        print(f"cannot read plan: {args.plan} ({exc})")
        print("example: python .agents/job.py --plan scratch/swarm_demo/plan.json --root . --wait --open")
        return 2
    url = f"http://127.0.0.1:{args.port}/?token={info['token']}"
    print(f"dashboard: {url}")

    server = None
    if args.open:
        server, started = ensure_dashboard(args.root, args.port)
        print(f"dashboard: {'started' if started else 'already running'} on port {args.port}")
        try:
            webbrowser.open(url)
        except Exception:                       # noqa: BLE001
            pass

    try:
        if args.decision:
            decide(args.root, args.decision, token=info["token"])
        elif args.wait:
            print("waiting for approval on the dashboard...")
            result = wait_for_decision(args.root, timeout=args.timeout)
            if result != "approved":
                print(f"NOT RUNNING: {result}")
                return 2

        state = read_plan_state(args.root)
        if (state.get("decision") or {}).get("status") != "approved":
            print("plan is not approved (use --wait or --decision approve)")
            return 2

        report = run_job(plan, root=args.root, allow_escalate=args.allow_escalate,
                         force=args.force, dry_run=args.dry_run,
                         reviewer_kind=args.reviewer, model=args.model)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"status  : {report['status']} ({report['duration_ms']}ms)")
            for item in report["subtasks"]:
                chain = " -> ".join((item.get("route") or {}).get("chain") or [])
                print(f"  {item['id']:<6} {item['status']:<10} {chain}")
        return 0 if report["status"] in ("applied", "dry-run") else 1
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    sys.exit(main())
