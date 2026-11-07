#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2-07: v2 doctor preflight checks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2 import doctor  # noqa: E402


def main() -> int:
    rows = doctor.collect(AGENTS_DIR.parent)
    names = {row.name for row in rows}
    assert "git" in names
    assert "worktree" in names
    assert "free models" in names
    assert any("spec kit" in name for name in names)
    assert any("deepseek" in name for name in names)
    assert any("glm" in name for name in names)

    report = doctor.as_dict(rows)
    assert report["checks"] and isinstance(report["ok"], bool)
    assert report["bad"] == sum(1 for row in rows if row.status == "bad")

    table = doctor.format_table(rows, color=False)
    assert "v2 doctor" in table and "STATUS" in table and "DETAIL" in table
    assert "check(s)" in table

    payload = json.dumps(report)
    assert "checks" in payload
    assert doctor.main(AGENTS_DIR.parent, json_output=True, color=False) in (0, 2)
    print("V2 DOCTOR: 17/17 PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
