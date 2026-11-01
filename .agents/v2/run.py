#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M1 CLI: prepare/status/handoff for the guarded v2.1 foundation.

Examples:
    python .agents/v2/run.py prepare --goal "fix tests" --phase P1
    python .agents/v2/run.py status --run-id v2_...
    python .agents/v2/run.py handoff --run-id v2_...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v2.budget import BudgetConfig  # noqa: E402
from v2.runner import V2Runner  # noqa: E402


def _budget_config(path: str | None, phases: list[str]) -> BudgetConfig | None:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not payload.get("phases"):
        payload["phases"] = {
            phase_id: BudgetConfig.default([phase_id]).phases[phase_id].as_dict()
            for phase_id in phases
        }
    return BudgetConfig.from_dict(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Adaptive Swarm v2.1 runner")
    parser.add_argument("--root", default=".")
    parser.add_argument("--state-root", default=None)
    parser.add_argument("--worktree-root", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare")
    prep.add_argument("--goal", required=True)
    prep.add_argument("--base-sha", default="HEAD")
    prep.add_argument("--run-id", default=None)
    prep.add_argument("--phase", action="append", default=None)
    prep.add_argument("--budget", default=None, help="JSON budget config")
    prep.add_argument("--pretty", action="store_true")

    status = sub.add_parser("status")
    status.add_argument("--run-id", required=True)

    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--run-id", required=True)
    plan_parser.add_argument("--plan", required=True, help="path to master plan JSON")

    signoff_parser = sub.add_parser("signoff")
    signoff_parser.add_argument("--run-id", required=True)
    signoff_parser.add_argument("--approved-by", default="local-user")
    signoff_parser.add_argument("--auto-merge", action="store_true")
    signoff_parser.add_argument("--allow-dirty-snapshot", action="store_true")

    assert_parser = sub.add_parser("assert-signoff")
    assert_parser.add_argument("--run-id", required=True)

    execute = sub.add_parser("execute")
    execute.add_argument("--run-id", required=True)
    execute.add_argument("--coder-model", default=None)
    execute.add_argument("--coder-kind", choices=("agentic", "opencode"), default="agentic")
    execute.add_argument("--reviewer", action="append", default=[],
                         help="role=model; repeat for security, qa, perf, docs")
    execute.add_argument("--free", action="store_true",
                         help="select free models from .agents/config.json")
    execute.add_argument("--paid-cost", type=float, default=0.0,
                         help="max USD estimate per paid call; paid models stay blocked at 0")
    execute.add_argument("--timeout", type=int, default=300)

    handoff = sub.add_parser("handoff")
    handoff.add_argument("--run-id", required=True)
    handoff.add_argument("--report", default=None)

    args = parser.parse_args(argv)
    runner = V2Runner(
        args.root,
        state_root=args.state_root,
        worktree_root=args.worktree_root,
        require_clean=True,
    )

    if args.command == "prepare":
        phases = args.phase or ["P1"]
        result = runner.prepare(
            args.goal,
            base_sha=args.base_sha,
            run_id=args.run_id,
            phase_ids=phases,
            budget_config=_budget_config(args.budget, phases),
        )
        indent = 2 if args.pretty else None
        print(json.dumps(result, ensure_ascii=False, indent=indent))
        return 0

    if args.command == "status":
        print(json.dumps(runner.status(args.run_id), ensure_ascii=False, indent=2))
        return 0

    if args.command == "plan":
        plan_path = Path(args.plan).expanduser()
        if not plan_path.is_absolute():
            plan_path = Path(args.root) / plan_path
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        result = runner.submit_plan(args.run_id, plan)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "signoff":
        result = runner.signoff(
            args.run_id,
            approved_by=args.approved_by,
            auto_merge=args.auto_merge,
            allow_dirty_snapshot=args.allow_dirty_snapshot,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "assert-signoff":
        record = runner.assert_signed(args.run_id)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0

    if args.command == "execute":
        from v2.adapters import FREE_MODELS, build_worker_seats, free_seats_from_config
        from v2.execution import ExecutionEngine
        from v2.models import load_config

        config = None
        try:
            config = load_config(args.root)
        except Exception:
            config = None
        reviewer_models: dict[str, str] = {}
        coder_model = args.coder_model
        if args.free:
            if config is not None:
                coder_model, reviewer_models = free_seats_from_config(config)
            else:
                coder_model = FREE_MODELS["coder"]
                reviewer_models = {
                    role: FREE_MODELS[role]
                    for role in ("security", "qa", "perf", "docs")
                }
        for item in args.reviewer:
            if "=" not in item:
                raise SystemExit(f"bad --reviewer value: {item!r}; use role=model")
            role, model = item.split("=", 1)
            reviewer_models[role.strip()] = model.strip()
        if not coder_model:
            raise SystemExit("execute needs --coder-model or --free")
        missing = [role for role in ("security", "qa", "perf", "docs")
                   if not reviewer_models.get(role)]
        if missing:
            raise SystemExit(f"missing reviewer models: {', '.join(missing)}")
        store = runner.load(args.run_id)
        coder, reviewers = build_worker_seats(
            coder_model=coder_model,
            reviewer_models=reviewer_models,
            sandbox_root=store.run_dir / "coder_sandbox",
            coder_kind=args.coder_kind,
            timeout=args.timeout,
            config=config,
            paid_cost_usd=args.paid_cost,
            free_only=args.free,
        )
        result = ExecutionEngine(runner).execute(
            args.run_id, coder=coder, reviewers=reviewers
        )
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "handoff":
        result = runner.handoff(args.run_id, report=args.report)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
