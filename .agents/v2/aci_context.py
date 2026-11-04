#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 2 read-only ACI and pre-apply validation.

ACI actions are read-only. Editing and writing remain outside this module and
must still pass the existing Lead/safe_writer/verify path.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .adaptive import Action, ActionPolicy, Observation, SECRET_NAME_RE


@dataclass
class ValidationResult:
    ok: bool
    issues: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def raise_if_invalid(self) -> "ValidationResult":
        if not self.ok:
            raise ACIValidationError(self.issues, self.stats)
        return self


class ACIValidationError(ValueError):
    code = "ACI_EDIT_INVALID"

    def __init__(self, issues: list[str], stats: dict[str, Any] | None = None):
        self.issues = list(issues)
        self.stats = dict(stats or {})
        super().__init__("; ".join(self.issues[:8]))


class ACIContext:
    """Small read-only facade over ActionPolicy."""

    def __init__(self, root: str | Path, *, default_window: int = 40):
        self.root = Path(root).resolve()
        self.policy = ActionPolicy(self.root)
        self.default_window = max(1, int(default_window))

    def open(self, path: str, *, start_line: int = 1, window: int | None = None) -> Observation:
        return self.policy.execute(Action("open", {
            "path": path, "start_line": start_line,
            "window": window or self.default_window,
        }))

    def scroll(self, path: str, *, start_line: int, offset: int | None = None,
               window: int | None = None) -> Observation:
        return self.policy.execute(Action("scroll", {
            "path": path, "start_line": start_line,
            "offset": offset if offset is not None else (window or self.default_window),
            "window": window or self.default_window,
        }))

    def search_symbol(self, query: str) -> Observation:
        return self.policy.execute(Action("search", {"query": query}))

    def edit_check(self, path: str, old: str, new: str, *,
                   start_line: int | None = None, end_line: int | None = None) -> Observation:
        return self.policy.execute(Action("edit_check", {
            "path": path, "old": old, "new": new,
            "start_line": start_line, "end_line": end_line,
        }))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _test_like(path: str) -> bool:
    name = Path(path).name.lower()
    return "/tests/" in f"/{path.lower()}" or name.startswith("test_") or name.endswith("_test.py")


def validate_proposed_files(
    root: str | Path,
    old_files: Mapping[str, str],
    new_files: Mapping[str, str],
    work_order: Mapping[str, Any],
    *,
    test_hashes: Mapping[str, str] | None = None,
    max_diff_lines: int = 4000,
    max_files: int = 12,
) -> ValidationResult:
    """Validate a proposed file set without writing it.

    Checks target allowlist, path safety, Python syntax, ambiguous replacement
    fragments, diff size and frozen test hashes.
    """
    root_path = Path(root).resolve()
    targets = [str(item) for item in (work_order.get("target_files") or [])]
    issues: list[str] = []
    stats: dict[str, Any] = {"files": len(new_files), "diff_lines": 0}
    if len(new_files) > max_files:
        issues.append(f"too many files changed: {len(new_files)} > {max_files}")
    for relative in new_files:
        if relative not in targets:
            issues.append(f"file outside work order: {relative}")
        if SECRET_NAME_RE.search(relative):
            issues.append(f"sensitive path blocked: {relative}")
        if not _safe(relative):
            issues.append(f"unsafe path: {relative}")
    for relative, expected in (test_hashes or {}).items():
        target = (root_path / relative).resolve()
        if target.is_file() and _sha256(target) != expected:
            issues.append(f"test file changed: {relative}")
    diff_total = 0
    for relative, new_text in new_files.items():
        if not _safe(relative):
            continue
        old_text = str(old_files.get(relative, ""))
        if relative.lower().endswith(".py"):
            try:
                compile(str(new_text), relative, "exec")
            except SyntaxError as exc:
                issues.append(f"{relative}: syntax error at line {exc.lineno}: {exc.msg}")
        diff = list(difflib.unified_diff(
            old_text.splitlines(), str(new_text).splitlines(),
            fromfile=f"a/{relative}", tofile=f"b/{relative}", lineterm="",
        ))
        diff_total += len(diff)
        if old_text and old_text != new_text:
            matcher = difflib.SequenceMatcher(a=old_text, b=str(new_text))
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag in {"replace", "delete"} and i2 > i1:
                    fragment = old_text[i1:i2]
                    if fragment and old_text.count(fragment) > 1:
                        issues.append(f"{relative}: replacement fragment is ambiguous (occurs {old_text.count(fragment)} times)")
                        break
    stats["diff_lines"] = diff_total
    if diff_total > max_diff_lines:
        issues.append(f"diff too large: {diff_total} > {max_diff_lines} lines")
    return ValidationResult(not issues, issues, stats)


def _safe(relative: str) -> bool:
    text = str(relative or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or re_drive(text):
        return False
    return ".." not in Path(text).parts and text not in {".", ""}


def re_drive(text: str) -> bool:
    import re

    return bool(re.match(r"^[A-Za-z]:", text))
