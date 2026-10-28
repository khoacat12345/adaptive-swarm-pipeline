#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill/documentation consistency checks.

Catches the failure mode we actually hit: the repository was upgraded, but the
installed OpenCode skill was a stale stub without frontmatter (so it disappeared
from discovery), or the Kaggle playbook still described the old 300s/180s limits.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

import core.budget  # noqa: E402
import recipe_c  # noqa: E402
import scout  # noqa: E402
import sync_skill  # noqa: E402


class Checks:
    def __init__(self) -> None:
        self.total = 0
        self.failures: list[str] = []

    def check(self, name: str, condition, detail="") -> bool:
        self.total += 1
        ok = bool(condition)
        if not ok:
            self.failures.append(f"{name}: {detail}" if detail else name)
        return ok

    @property
    def passed(self) -> int:
        return self.total - len(self.failures)


def main() -> int:
    checks = Checks()
    expected = sync_skill.render()

    checks.check("skill: frontmatter present",
                 expected.startswith("---\nname: adaptive-swarm-pipeline\n"), expected[:80])
    checks.check("skill: points to preflight test",
                 "test_preflight.py" in expected, "missing")
    checks.check("skill: paid opt-in documented",
                 "--allow-paid" in expected, "missing")
    checks.check("skill: preflight rule documented",
                 "Health preflight is mandatory" in expected, "missing")

    installed = [path for path in sync_skill.target_paths() if path.exists()]
    for path in installed:
        try:
            actual = path.read_text(encoding="utf-8")
        except OSError as exc:
            actual = f"<error {exc}>"
        checks.check(f"skill: installed copy matches {path}", actual == expected,
                     "run python .agents/sync_skill.py --write")

    checks.check("docs: recipe cap is 600s", recipe_c.BUDGET_MAX_SECONDS == 600,
                 recipe_c.BUDGET_MAX_SECONDS)
    checks.check("docs: meeting budget is 600s",
                 core.budget.MeetingBudget().seconds == 600,
                 core.budget.MeetingBudget().seconds)
    checks.check("auth: parses OpenCode Go",
                 scout.parse_auth_flags("OpenCode Go  OpenCode Go  stored")["has_go_auth"],
                 "not parsed")

    root_readme = (AGENTS_DIR.parent / "README.md")
    if root_readme.exists():
        text = root_readme.read_text(encoding="utf-8")
        checks.check("docs: root README uses 600s", "600 seconds" in text, "missing")
        checks.check("docs: root README free-only opt-in",
                     "--allow-paid" in text, "missing")
        checks.check("docs: root README has no stale 300s budget",
                     "300 seconds" not in text, "stale text")

    kaggle = Path(os.environ.get(
        "KAGGLE_SKILL_PATH",
        Path.home() / ".config" / "opencode" / "skills" / "kaggle-competition" / "SKILL.md",
    ))
    if kaggle.exists():
        text = kaggle.read_text(encoding="utf-8")
        checks.check("kaggle skill: uses 600s meeting cap", "600s" in text, "missing")
        checks.check("kaggle skill: uses 240s reviewer timeout", "240s" in text, "missing")
        checks.check("kaggle skill: no stale 300s/180s",
                     "300s" not in text and "180s" not in text, "stale text")
        checks.check("kaggle skill: no manual catalog patch instruction",
                     "Sửa `catalog.providers` thủ công" not in text, "stale text")
    else:
        print("[skip] kaggle-competition skill not installed")

    print("")
    print(f"SKILL CONSISTENCY: {checks.passed}/{checks.total} PASS")
    for failure in checks.failures:
        print(f"  FAIL {failure}")
    return 0 if not checks.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
