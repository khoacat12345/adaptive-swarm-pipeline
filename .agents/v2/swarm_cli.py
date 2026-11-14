#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-command swarm entrypoint: doctor -> planner -> usage -> execute -> handoff.

    swarm "fix the failing add() test"
    swarm "task" --tier E2 --preset free-only
    swarm "task" --dry-run
    swarm doctor
    swarm planner "task" --out plan.json
    swarm status --last

The CLI never writes the main tree. Execution happens in a guarded worktree and
only ``swarm merge`` (or ``--merge``) applies the handoff patch explicitly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unified.contracts import ContractError, UnifiedRequest  # noqa: E402
from unified.orchestrator import UnifiedOrchestrator  # noqa: E402
from v2.adapters import build_worker_seats  # noqa: E402
from v2.doctor import main as doctor_main  # noqa: E402
from v2.errors import ExecutionError, V2Error  # noqa: E402
from v2.plan import PlanValidationError  # noqa: E402
from v2.planner_adapter import (  # noqa: E402
    PlanningError,
    adapt_file,
    from_goal,
    from_order,
    load_source_plan,
    write_plan,
)
from v2.runner import V2Runner  # noqa: E402

__version__ = "3.0.1"

COMMANDS = {
    "doctor", "planner", "run", "status", "handoff", "merge", "resume",
    "rollback", "inbox", "ask", "reply",
}


def _normalize_argv(argv: Sequence[str]) -> list[str]:
    values = list(argv)
    if not values:
        return values
    if values[0] in COMMANDS or values[0] in {"-h", "--help", "--version"}:
        return values
    return ["run", *values]


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", default=".")
    parser.add_argument("--state-root", default=None)
    parser.add_argument("--worktree-root", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def _resolve_root(args: argparse.Namespace) -> Path:
    return Path(args.root).expanduser().resolve()


def _resolve_state_paths(root: Path, args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    def resolve(value: str | None) -> Path | None:
        if not value:
            return None
        path = Path(value).expanduser()
        return path if path.is_absolute() else (root / path).resolve()

    return resolve(args.state_root), resolve(args.worktree_root)


def _latest_run(runner: V2Runner) -> str:
    runs = sorted(
        (runner.state_root / "runs").glob("*/run_manifest.json"),
        key=lambda path: path.stat().st_mtime,
    )
    if not runs:
        raise ExecutionError(f"no v2 runs under {runner.state_root}")
    return runs[-1].parent.name


def _emit(payload: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    status = payload.get("status") or payload.get("ok")
    print(f"status: {status}")
    for key in ("run_id", "tier", "planner", "worktree", "plan_hash",
                "state_root", "next"):
        if payload.get(key):
            print(f"{key}: {payload[key]}")
    handoff = payload.get("handoff") or {}
    if isinstance(handoff, Mapping):
        for key in ("patch", "report", "worktree"):
            if handoff.get(key):
                print(f"handoff.{key}: {handoff[key]}")
    graph = payload.get("graph") or {}
    if isinstance(graph, Mapping) and graph.get("available"):
        print(f"graph: {graph.get('size') or len(graph.get('tasks') or [])} tasks, "
              f"waves={graph.get('waves')}")
    attempts = payload.get("attempts") or {}
    if isinstance(attempts, Mapping) and attempts.get("total"):
        print(f"attempts: {attempts.get('total')} "
              f"({attempts.get('retries', 0)} retries, "
              f"{attempts.get('lost', 0)} lost)")
    inbox = payload.get("inbox") or {}
    if isinstance(inbox, Mapping) and inbox.get("pending"):
        print(f"inbox: {inbox.get('pending')} pending "
              f"({inbox.get('blocking', 0)} blocking)")


def _emit_inbox(payload: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    pending = payload.get("pending") or 0
    total = payload.get("total") or 0
    print(f"inbox: {pending} pending / {total} total")
    if payload.get("open_gates"):
        print("gates: " + ", ".join(str(item) for item in payload["open_gates"]))
    for item in payload.get("pending_items") or []:
        if not isinstance(item, Mapping):
            continue
        gate = f" [{item.get('gate')}]" if item.get("gate") else ""
        print(f"- {item.get('id')}{gate} {item.get('question')}")


def _enrich_status(runner: V2Runner, run_id: str) -> dict[str, Any]:
    """F01/F02/F03/F05: attach graph, attempts, inbox and quota to status."""
    payload: dict[str, Any] = {}
    try:
        graph = runner.task_graph(run_id, include_work_orders=True).as_dict()
        payload["graph"] = {
            "available": True,
            "size": graph.get("size"),
            "waves": graph.get("waves"),
            "topological_order": graph.get("topological_order"),
            "cycle": graph.get("cycle"),
            "tasks": graph.get("tasks"),
        }
    except Exception as exc:  # noqa: BLE001
        payload["graph"] = {"available": False, "error": str(exc)[:200]}
    try:
        payload["attempts"] = runner.attempts(run_id, limit=50)["summary"]
    except Exception:  # noqa: BLE001
        payload["attempts"] = {}
    try:
        from unified.inbox import inbox_summary

        payload["inbox"] = inbox_summary(runner.root, run_id, state_root=runner.state_root)
    except Exception:  # noqa: BLE001
        payload["inbox"] = {}
    return payload


def _run_task(
    args: argparse.Namespace,
    *,
    seat_factory: Callable[..., Any],
    planner_goal: Callable[..., Any],
    planner_file: Callable[..., Any],
    planner_order: Callable[..., Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Parse the CLI namespace, run the orchestrator, then print the result.

    All state-machine work lives in ``unified.orchestrator``. This function is a
    boundary adapter; the only delivery action it may perform is the explicit
    ``--merge`` the operator asked for.
    """
    request = UnifiedRequest.from_namespace(args)
    orchestrator = UnifiedOrchestrator(
        seat_factory=seat_factory,
        planner_goal=planner_goal,
        planner_file=planner_file,
        planner_order=planner_order or from_order,
    )
    result = orchestrator.run(request)
    payload = result.as_dict()
    if args.merge:
        if result.status != "HANDOFF_READY" or not result.run_id:
            payload["merge"] = {"ok": False, "error": "run is not HANDOFF_READY"}
        else:
            from v2.delivery import merge as merge_handoff

            root = _resolve_root(args)
            state_root, _ = _resolve_state_paths(root, args)
            payload["merge"] = merge_handoff(
                root, result.run_id, state_root=state_root,
                verify_timeout=args.verify_timeout,
            )
    _emit(payload, as_json=args.json)
    return result.exit_code, payload


def _build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog="swarm",
        description="Adaptive Swarm v2 one-command entrypoint",
        parents=[common],
    )
    parser.add_argument(
        "--version", action="version", version=f"swarm {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", parents=[common])
    doctor.add_argument("--no-color", action="store_true")

    planner = sub.add_parser("planner", parents=[common])
    source = planner.add_mutually_exclusive_group(required=True)
    source.add_argument("--goal")
    source.add_argument("--plan")
    source.add_argument("--order", help="v1 order.json (one Work Order)")
    planner.add_argument("--tier", choices=("E1", "E2", "E3", "E4"), default=None)
    planner.add_argument("--target", action="append", default=[])
    planner.add_argument("--verify", default=None)
    planner.add_argument("--e1-opt-in", dest="e1_opt_in", action="store_true")
    planner.add_argument("--allow-new-files", action="store_true")
    planner.add_argument("--council", dest="council", action="store_true",
                         default=None)
    planner.add_argument("--no-council", dest="council", action="store_false")
    planner.add_argument("--planner-model", action="append", default=[])
    planner.add_argument("--council-rounds", type=int, choices=(1, 2), default=1)
    planner.add_argument("--run-id", default="plan-preview")
    planner.add_argument("--base-sha", default="HEAD")
    planner.add_argument("--out", default=None)

    run = sub.add_parser("run", parents=[common])
    run.add_argument("goal", nargs="?")
    run.add_argument("--plan", default=None, help="v1 plan or existing v2 MasterPlan")
    run.add_argument("--order", default=None, help="v1 order.json (one Work Order)")
    run.add_argument("--allow-new-files", action="store_true")
    run.add_argument("--council", dest="council", action="store_true",
                     default=None)
    run.add_argument("--no-council", dest="council", action="store_false")
    run.add_argument("--planner-model", action="append", default=[])
    run.add_argument("--council-rounds", type=int, choices=(1, 2), default=1)
    run.add_argument("--tier", choices=("E1", "E2", "E3", "E4"), default=None)
    run.add_argument("--target", action="append", default=[])
    run.add_argument("--verify", default=None)
    run.add_argument("--e1-opt-in", dest="e1_opt_in", action="store_true")
    run.add_argument("--preset", choices=("free-only", "strict"), default=None)
    run.add_argument("--coder-model", default=None)
    run.add_argument("--coder-kind", choices=("agentic", "opencode"), default="agentic")
    run.add_argument("--reviewer", action="append", default=[])
    run.add_argument("--free", action="store_true")
    run.add_argument("--paid-cost", type=float, default=0.0)
    run.add_argument("--timeout", type=int, default=300)
    run.add_argument("--verify-timeout", type=int, default=300)
    run.add_argument("--max-cost", type=float, default=None,
                     help="lower the plan cost envelope before Usage approval")
    run.add_argument("--max-requests", type=int, default=None,
                     help="lower the plan request envelope before Usage approval")
    run.add_argument("--max-wall-seconds", type=int, default=None,
                     help="lower the plan wall-clock envelope before Usage approval")
    run.add_argument("--run-id", default=None)
    run.add_argument("--base-sha", default="HEAD")
    run.add_argument("--approved-by", default="swarm")
    run.add_argument("--approve-usage", action="store_true")
    run.add_argument("--yes", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--plan-only", action="store_true")
    run.add_argument("--out", default=None)
    run.add_argument("--merge", action="store_true")

    status = sub.add_parser("status", parents=[common])
    status.add_argument("--run-id", default=None)
    status.add_argument("--last", action="store_true")

    handoff = sub.add_parser("handoff", parents=[common])
    handoff.add_argument("--run-id", required=True)
    handoff.add_argument("--report", default=None)

    merge = sub.add_parser("merge", parents=[common])
    merge.add_argument("--run-id", required=True)
    merge.add_argument("--message", default=None)
    merge.add_argument("--verify-timeout", type=int, default=300)
    merge.add_argument("--dry-run", action="store_true")

    resume = sub.add_parser("resume", parents=[common],
                            help="recover a killed phase or resume a paused run")
    resume.add_argument("--run-id", required=True)

    rollback = sub.add_parser("rollback", parents=[common],
                              help="revert a previous explicit merge")
    rollback.add_argument("--run-id", required=True)
    rollback.add_argument("--message", default=None)

    inbox = sub.add_parser("inbox", parents=[common],
                           help="list pending decisions for a run")
    inbox.add_argument("run_id")
    inbox.add_argument("--all", action="store_true",
                       help="also include answered/cancelled messages")

    ask = sub.add_parser("ask", parents=[common],
                         help="worker asks a question or requests approval")
    ask.add_argument("run_id")
    ask.add_argument("question")
    ask.add_argument("--gate", choices=(
        "usage_approval", "review_approval", "merge_approval",
        "destructive_action", "scope_change",
    ), default=None)
    ask.add_argument("--task", dest="task_id", default=None)
    ask.add_argument("--author", default="worker")
    ask.add_argument("--no-block", dest="blocking", action="store_false")
    ask.set_defaults(blocking=True)

    reply = sub.add_parser("reply", parents=[common],
                           help="answer a decision and unblock the worker")
    reply.add_argument("message_id")
    reply.add_argument("answer")
    reply.add_argument("--run-id", default=None)
    reply.add_argument("--answered-by", default="operator")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    seat_factory: Callable[..., Any] | None = None,
    planner_goal: Callable[..., Any] | None = None,
    planner_file: Callable[..., Any] | None = None,
    planner_order: Callable[..., Any] | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(_normalize_argv(sys.argv[1:] if argv is None else argv))
    root = _resolve_root(args)
    as_json = bool(getattr(args, "json", False))

    if args.command == "doctor":
        return doctor_main(root, json_output=as_json, color=not args.no_color)

    if args.command == "planner":
        council_kwargs = {
            "council": args.council,
            "planner_models": args.planner_model,
            "council_rounds": args.council_rounds,
        }
        try:
            if args.goal:
                result = from_goal(
                    args.goal,
                    run_id=args.run_id,
                    base_sha=args.base_sha,
                    root=root,
                    targets=args.target,
                    verify_command=args.verify,
                    tier=args.tier,
                    low_risk_opt_in=args.e1_opt_in,
                    allow_new_files=args.allow_new_files,
                    **council_kwargs,
                )
            elif args.order:
                order_path = Path(args.order)
                if not order_path.is_absolute():
                    order_path = (root / order_path).resolve()
                result = from_order(
                    load_source_plan(order_path),
                    run_id=args.run_id,
                    base_sha=args.base_sha,
                    root=root,
                    tier=args.tier,
                    low_risk_opt_in=args.e1_opt_in,
                    allow_new_files=True,
                    verify_command=args.verify,
                    **council_kwargs,
                )
            else:
                result = adapt_file(
                    args.plan,
                    run_id=args.run_id,
                    base_sha=args.base_sha,
                    root=root,
                    tier=args.tier,
                    low_risk_opt_in=args.e1_opt_in,
                    allow_new_files=args.allow_new_files,
                    verify_command=args.verify,
                    **council_kwargs,
                )
        except (PlanningError, PlanValidationError, ExecutionError, V2Error) as exc:
            _emit({"ok": False, "status": "PLAN_INVALID", "error": str(exc)},
                  as_json=as_json)
            return 2
        out = Path(args.out or (root / ".agents" / "tmp" / "swarm_plan.json"))
        if not out.is_absolute():
            out = (root / out).resolve()
        write_plan(out, result)
        payload = {
            "ok": True,
            "status": "PLANNED",
            "source": result.source,
            "tier": result.tier,
            "planner": result.planner,
            "plan_hash": result.plan_hash,
            "plan": str(out),
            "summary": result.summary,
            "council": result.council,
        }
        _emit(payload, as_json=as_json)
        return 0

    if args.command in {"status", "handoff", "merge", "resume", "rollback"}:
        state_root, worktree_root = _resolve_state_paths(root, args)
        runner = V2Runner(root, state_root=state_root, worktree_root=worktree_root)
        try:
            if args.command == "status":
                run_id = args.run_id or _latest_run(runner)
                payload = runner.status(run_id)
                payload.update(_enrich_status(runner, run_id))
                _emit(payload, as_json=as_json)
                return 0
            if args.command == "handoff":
                payload = runner.handoff(args.run_id, report=args.report)
                _emit(payload, as_json=as_json)
                return 0
            if args.command == "resume":
                manifest = runner.load(args.run_id).load()
                status = str(manifest.get("status") or "")
                if status in {"PHASE_EXECUTING", "PHASE_VERIFYING"}:
                    recovered = runner.recover(args.run_id)
                    payload = {"ok": True, "action": "recover",
                               "previous_status": status, **recovered}
                elif status in {"PAUSED", "BLOCKED", "RATE_LIMITED",
                                "AWAITING_USER_CONFIRMATION", "USER_CONFIRMED"}:
                    resumed = runner.resume(args.run_id)
                    payload = {"ok": True, "action": "resume",
                               "status": resumed.get("status"),
                               "run_id": args.run_id}
                else:
                    raise ExecutionError(
                        f"resume requires a killed/disrupted phase or paused run, got {status!r}")
                _emit(payload, as_json=as_json)
                return 0
            if args.command == "rollback":
                from v2.delivery import rollback as rollback_merge

                payload = rollback_merge(
                    root, args.run_id, state_root=state_root,
                    message=args.message,
                )
                _emit(payload, as_json=as_json)
                return 0
            from v2.delivery import merge as merge_handoff

            payload = merge_handoff(
                root, args.run_id, state_root=state_root,
                message=args.message, verify_timeout=args.verify_timeout,
                dry_run=args.dry_run,
            )
            _emit(payload, as_json=as_json)
            return 0
        except (V2Error, ExecutionError) as exc:
            _emit({"ok": False, "status": "FAILED", "error": str(exc)},
                  as_json=as_json)
            return 2

    if args.command in {"inbox", "ask", "reply"}:
        from unified import api as unified_api

        state_root, _ = _resolve_state_paths(root, args)
        try:
            if args.command == "inbox":
                payload = unified_api.run_inbox(
                    root, args.run_id, state_root=state_root,
                )
                if not args.all:
                    payload = {
                        **payload,
                        "items": payload.get("pending_items") or [],
                    }
                _emit_inbox(payload, as_json=as_json)
                return 0
            if args.command == "ask":
                payload = unified_api.ask_decision(
                    root, args.run_id, args.question,
                    state_root=state_root,
                    gate=args.gate,
                    blocking=bool(args.blocking),
                    task_id=args.task_id,
                    author=args.author,
                )
                _emit(payload, as_json=as_json)
                return 0
            payload = unified_api.reply_decision(
                root, args.message_id, args.answer,
                run_id=args.run_id, state_root=state_root,
                answered_by=args.answered_by,
            )
            _emit(payload, as_json=as_json)
            return 0
        except (V2Error, ExecutionError, FileNotFoundError) as exc:
            _emit({"ok": False, "status": "FAILED", "error": str(exc)},
                  as_json=as_json)
            return 2

    try:
        code, payload = _run_task(
            args,
            seat_factory=seat_factory or build_worker_seats,
            planner_goal=planner_goal or from_goal,
            planner_file=planner_file or adapt_file,
            planner_order=planner_order or from_order,
        )
    except (ContractError, PlanningError, PlanValidationError,
            ExecutionError, V2Error) as exc:
        _emit({"ok": False, "status": "FAILED", "error": str(exc)},
              as_json=as_json)
        return 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
