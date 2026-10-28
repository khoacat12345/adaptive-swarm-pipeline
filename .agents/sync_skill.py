#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sync the repository skill entrypoint into the local OpenCode skill folder.

The project source of truth is ``.agents/SKILL.md``.  OpenCode also needs a
frontmatter header in the installed copy, otherwise the skill disappears from
discovery.  This script makes the installed copy deterministic.

    python .agents/sync_skill.py --check
    python .agents/sync_skill.py --write
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "SKILL.md"
SKILL_NAME = "adaptive-swarm-pipeline"
FRONTMATTER = """---
name: adaptive-swarm-pipeline
description: Run, observe and harden the Adaptive Swarm Pipeline in the project's `.agents/` folder. Use when the user asks to run a coding task through the swarm, replay a run, open the local dashboard, read cost/token/fallback statistics, get memory suggestions from past runs, or verify pipeline health (Phase 0 / 0.5 / 1 / 2 / 3 / 4 acceptance suites).
---

"""


def target_paths() -> list[Path]:
    """Known OpenCode skill roots on Windows, Linux and macOS."""
    paths: list[Path] = []
    home = Path.home()
    roots = [
        HERE.parent / ".opencode" / "skills",
        home / ".opencode" / "skills",
        Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "opencode" / "skills",
        home / ".config" / "opencode" / "skills",
    ]
    for root in roots:
        path = root / SKILL_NAME / "SKILL.md"
        if path not in paths:
            paths.append(path)
    return paths


def render() -> str:
    body = SOURCE.read_text(encoding="utf-8").lstrip("\ufeff")
    if body.startswith("---"):
        # The source already carries frontmatter; do not duplicate it.
        return body
    return FRONTMATTER + body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync the adaptive-swarm-pipeline skill.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true",
                       help="fail if an installed copy differs from .agents/SKILL.md")
    group.add_argument("--write", action="store_true",
                       help="write/update installed copies")
    args = parser.parse_args(argv)

    if not SOURCE.exists():
        print(f"missing source: {SOURCE}", file=sys.stderr)
        return 2

    expected = render()
    targets = target_paths()
    existed = [path for path in targets if path.exists()]
    if args.write:
        written = 0
        for path in targets:
            if path.exists() or path == targets[0]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(expected, encoding="utf-8")
                print(f"wrote {path}")
                written += 1
        if not written:
            print("no writable skill target", file=sys.stderr)
            return 1
        return 0

    if not existed:
        print("skill is not installed in a known OpenCode skill root; skipped")
        return 0
    mismatched = [path for path in existed
                  if path.read_text(encoding="utf-8") != expected]
    if mismatched:
        for path in mismatched:
            print(f"STALE {path}")
        print("Run: python .agents/sync_skill.py --write", file=sys.stderr)
        return 1
    print(f"skill copies match ({len(existed)} installed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
