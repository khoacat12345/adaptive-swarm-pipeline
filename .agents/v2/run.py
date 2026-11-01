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
    parser = argparse.ArgumentParser(description="Adaptive Swarm v2.1 M1 runner")
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

    if args.command == "handoff":
        result = runner.handoff(args.run_id, report=args.report)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
