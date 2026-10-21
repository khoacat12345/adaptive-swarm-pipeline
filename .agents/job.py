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
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ledger                        # noqa: E402
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


def load_plan(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_plan_state(root) -> dict:
    """Trang thai cho dashboard: plan + decision (KHONG tra token)."""
    state: dict = {"plan": None, "decision": None}
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
# Validate + topo
# --------------------------------------------------------------------------
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
def submit_plan(plan: dict, root, *, token: str | None = None, log: bool = True) -> dict:
    problems = validate_plan(plan)
    if problems:
        raise ValueError("plan khong hop le: " + "; ".join(problems[:8]))
    directory = _dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    token = token or secrets.token_hex(8)
    plan_path(root).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    token_path(root).write_text(token, encoding="utf-8")
    decision_path(root).write_text(json.dumps(
        {"status": "pending", "at": ledger.utc_now_iso()}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    if log:
        ledger.append_event("plan.submitted", run_id=f"job_{plan.get('job_id')}",
                            result={"status": "info"},
                            data={"job_id": plan.get("job_id"),
                                  "subtasks": [item.get("id") for item in plan["subtasks"]]},
                            root=root)
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
    if not token or token != expected:
        raise PermissionError("token khong dung")
    state = read_plan_state(root)
    current = (state.get("decision") or {}).get("status")
    if current not in (None, "pending"):
        raise ValueError(f"plan da duoc quyet dinh: {current}")
    status = "approved" if decision == "approve" else "rejected"
    payload = {"status": status, "at": ledger.utc_now_iso(),
               "job_id": (state.get("plan") or {}).get("job_id")}
    decision_path(root).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    if log:
        ledger.append_event("plan.approved" if status == "approved" else "plan.rejected",
                            run_id=f"job_{payload.get('job_id')}",
                            result={"status": "pass" if status == "approved" else "fail"},
                            data={"job_id": payload["job_id"]}, root=root)
    return payload


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
            force: bool = False, dry_run: bool = False) -> dict:
    problems = validate_plan(plan)
    if problems:
        raise ValueError("plan khong hop le: " + "; ".join(problems[:8]))

    import quota
    tracker = quota.QuotaTracker(root=root).refresh() if use_router else None
    started = time.perf_counter()
    records: list[dict] = []
    status = "applied"

    if log:
        ledger.append_event("job.start", run_id=f"job_{plan.get('job_id')}", result={"status": "info"},
                            data={"job_id": plan.get("job_id"),
                                  "subtasks": len(plan["subtasks"])}, root=root)

    for item in topo_order(plan["subtasks"]):
        order = to_order(item, plan)
        route_plan = (router.route(order, quota=tracker, allow_escalate=allow_escalate)
                      if use_router else None)
        if route_plan and route_plan.get("exhausted") and not force:
            records.append({"id": item["id"], "status": "blocked",
                            "error": "het quota", "route": route_plan})
            status = "blocked"
            break

        chain = route_plan["chain"] if route_plan else []
        coder = (coder_factory(order, chain) if coder_factory
                 else _default_coder(chain))
        review = reviewer if reviewer is not None else workers.HeuristicReviewer()
        sub_started = time.perf_counter()
        try:
            report = recipe_c.run(order, coder=coder, reviewer=review,
                                  root=root, verify_timeout=verify_timeout, log=log,
                                  dry_run=dry_run, route_plan=route_plan)
        except Exception as exc:                    # noqa: BLE001
            records.append({"id": item["id"], "status": "error",
                            "error": str(exc)[:300], "route": route_plan})
            status = "error"
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
        if report.get("status") not in ("applied", "dry-run"):
            status = "stopped"
            break

    payload = {
        "job_id": plan.get("job_id"),
        "status": status,
        "subtasks": records,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    if log:
        ledger.append_event("job.done", run_id=f"job_{plan.get('job_id')}",
                            result={"status": "pass" if status == "applied" else "fail",
                                    "error": None if status == "applied" else status},
                            data={"job_id": plan.get("job_id"), "status": status,
                                  "subtasks": [{"id": r["id"], "status": r["status"]}
                                               for r in records]},
                            root=root)
    return payload


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Job plan + duyet dashboard + chay tuan tu.")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--root", default=".")
    parser.add_argument("--token", default=None, help="token co dinh (mac dinh: random)")
    parser.add_argument("--decision", choices=("approve", "reject"), default=None,
                        help="duyet ngay tren CLI (headless), bo qua dashboard")
    parser.add_argument("--wait", action="store_true", help="cho dashboard duyet")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--allow-escalate", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--open", action="store_true",
                        help="tu khoi dong dashboard neu can va mo trinh duyet")
    args = parser.parse_args(argv)

    try:
        plan = load_plan(args.plan)
    except OSError as exc:
        print(f"khong doc duoc plan: {args.plan} ({exc})")
        print("vi du: python .agents/job.py --plan scratch/swarm_demo/plan.json --root . --wait --open")
        return 2
    info = submit_plan(plan, args.root, token=args.token)
    url = f"http://127.0.0.1:{args.port}/?token={info['token']}"
    print(f"job     : {info['job_id']} ({info['subtasks']} subtask)")
    print(f"dashboard: {url}")

    server = None
    if args.open:
        server, started = ensure_dashboard(args.root, args.port)
        print(f"dashboard: {'da khoi dong' if started else 'da co san'} tren port {args.port}")
        try:
            webbrowser.open(url)
        except Exception:                       # noqa: BLE001
            pass

    try:
        if args.decision:
            decide(args.root, args.decision, token=info["token"])
        elif args.wait:
            print("dang cho duyet tren dashboard...")
            result = wait_for_decision(args.root, timeout=args.timeout)
            if result != "approved":
                print(f"KHONG CHAY: {result}")
                return 2

        state = read_plan_state(args.root)
        if (state.get("decision") or {}).get("status") != "approved":
            print("plan chua duoc duyet (dung --wait hoac --decision approve)")
            return 2

        report = run_job(plan, root=args.root, allow_escalate=args.allow_escalate,
                         force=args.force, dry_run=args.dry_run)
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
