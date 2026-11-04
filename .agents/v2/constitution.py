#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Project constitution discovery and hashing for v2.1 sign-off."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .errors import ExecutionError

CANDIDATES = (
    Path(".specify") / "memory" / "constitution.md",
    Path("docs") / "CONSTITUTION.md",
    Path("CONSTITUTION.md"),
    Path(".agents") / "CONSTITUTION.md",
)
TEMPLATE_MARKERS = ("[PROJECT_NAME]", "[PRINCIPLE_1_NAME]", "[GOVERNANCE_RULES]")


def find_constitution(root: str | Path) -> Path | None:
    base = Path(root).resolve()
    for relative in CANDIDATES:
        path = (base / relative).resolve()
        if path.is_file():
            return path
    return None


def load_constitution(root: str | Path) -> dict[str, Any]:
    """Return the current constitution text/hash.

    A missing constitution is allowed for compatibility with non-Spec-Kit
    projects, but the sign-off records ``hash: null`` and a later file addition
    invalidates the sign-off.
    """
    path = find_constitution(root)
    if path is None:
        return {
            "present": False,
            "path": None,
            "hash": None,
            "complete": False,
            "text": "",
        }
    try:
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except OSError as exc:
        raise ExecutionError(f"cannot read constitution: {exc}") from exc
    normalized = text.strip() + "\n"
    complete = not any(marker in text for marker in TEMPLATE_MARKERS)
    return {
        "present": True,
        "path": str(path),
        "hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "complete": complete,
        "text": text,
    }


def constitution_hash(root: str | Path) -> str | None:
    return load_constitution(root)["hash"]


def assert_constitution(root: str | Path, expected_hash: str | None) -> dict[str, Any]:
    current = load_constitution(root)
    if current["hash"] != expected_hash:
        raise ExecutionError(
            "constitution changed after sign-off: "
            f"{expected_hash} != {current['hash']}"
        )
    return current
