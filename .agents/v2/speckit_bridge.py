#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility CLI for Spec Kit -> v2.1 MasterPlan bridging.

The reusable implementation lives in ``v2.tasks``. This file is the explicit
``speckit_bridge.py`` entry point requested by the RFC.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v2.tasks import bridge_spec_kit  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bridge canonical Spec Kit tasks.yaml to MasterPlan")
    parser.add_argument("--root", default=".")
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--worktree", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    result = bridge_spec_kit(
        args.root,
        args.tasks,
        run_id=args.run_id,
        base_sha=args.base_sha,
        worktree=args.worktree,
        out=args.out,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
