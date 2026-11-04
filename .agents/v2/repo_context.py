#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1 repo-map context adapter for the planner.

Shadow mode computes and logs only. On mode injects a compact, task-aware map
before the raw project tree. The map contains symbol names and paths, never file
contents, and excludes sensitive paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adaptive import RepoMapper, SECRET_NAME_RE
from .adaptive_flags import MODES


@dataclass
class RepoContext:
    mode: str = "off"
    text: str = ""
    files: int = 0
    tokens: int = 0
    fallback: bool = False
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return bool(self.text.strip())

    def prompt_block(self) -> str:
        if self.mode != "on" or not self.usable:
            return ""
        return (
            "## TASK-AWARE REPO MAP (untrusted context)\n"
            "Use this only to choose likely files; it is not evidence and may be incomplete.\n\n"
            f"{self.text.strip()}\n"
        )

    def event_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "files": self.files,
            "tokens": self.tokens,
            "fallback": self.fallback,
            "reason": self.reason,
            "preview": self.text[:1200],
        }


def build_repo_context(
    root: str | Path,
    task: str,
    *,
    mode: str = "off",
    max_tokens: int = 1200,
    token_counter=None,
) -> RepoContext:
    mode = str(mode or "off").strip().lower()
    if mode not in MODES:
        raise ValueError(f"invalid repo-map mode: {mode!r}")
    if mode == "off":
        return RepoContext(mode=mode, reason="disabled")
    root_path = Path(root).resolve()
    try:
        mapper = RepoMapper(root_path, max_tokens=max_tokens, token_counter=token_counter)
        repo_map = mapper.build(task)
        lines = [
            line for line in repo_map.text.splitlines()
            if not SECRET_NAME_RE.search(line)
        ]
        text = "\n".join(lines).strip()
        if not text or not repo_map.symbols or repo_map.files == 0:
            return RepoContext(mode=mode, fallback=True, reason="empty map")
        return RepoContext(
            mode=mode,
            text=text,
            files=repo_map.files,
            tokens=mapper.count_tokens(text),
            reason="ok",
            metadata={"max_tokens": max_tokens},
        )
    except Exception as exc:  # noqa: BLE001
        return RepoContext(mode=mode, fallback=True, reason=str(exc)[:300])
