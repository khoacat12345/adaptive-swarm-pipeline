#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 3 failure-lesson runtime.

Capture normalized failures, upsert them through LessonStore, and expose a
small untrusted-advice block for planner/coder prompts. Lessons never replace
verify, plan hashes or sign-off evidence.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable, Mapping

from .adaptive import Lesson, LessonStore

_SECRET_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}",
    re.IGNORECASE,
)
_VOLATILE_RE = re.compile(
    r"[A-Za-z]:\\[^\s:]+|/tmp/[^\s:]+|/home/[^\s:]+|:\d+"
)
MAX_FIX_CHARS = 400


def _sanitize(text: str) -> str:
    value = _SECRET_RE.sub("[redacted-secret]", str(text or ""))
    value = _VOLATILE_RE.sub("[path]", value)
    return " ".join(value.split())[:MAX_FIX_CHARS]


def normalized_signature(kind: str, error: str) -> str:
    clean = _sanitize(error)
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:12]
    return f"{kind}:{digest}"


class FailureLessonRuntime:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.store = LessonStore(self.path)

    @classmethod
    def for_root(cls, root: str | Path) -> "FailureLessonRuntime":
        return cls(Path(root).resolve() / ".agents" / "tmp" / "v2" / "lessons.jsonl")

    def record_failure(
        self,
        *,
        kind: str,
        error: str,
        files: Iterable[str] = (),
        cause: str = "",
        fix: str = "",
        source_run: str = "",
        tags: Iterable[str] = (),
    ) -> Lesson:
        clean_error = _sanitize(error)
        signature = normalized_signature(kind, clean_error)
        lesson = Lesson(
            signature=signature,
            cause=_sanitize(cause or clean_error),
            fix=_sanitize(fix or "inspect the failure and retry with a smaller diff"),
            files=[str(item) for item in files if str(item)],
            tags=[str(item) for item in tags if str(item)],
            source_run=str(source_run or ""),
        )
        from .context_runtime import MemoryLock

        with MemoryLock(self.path):
            return self.store.record(lesson)

    def record_fix(self, *, signature: str, fix: str, source_run: str = "") -> Lesson | None:
        matches = [item for item in self.store.lessons if item.signature == signature]
        if not matches:
            return None
        current = matches[0]
        return self.store.record(Lesson(
            signature=current.signature,
            cause=current.cause,
            fix=_sanitize(fix),
            files=list(current.files),
            tags=list(current.tags),
            source_run=source_run or current.source_run,
        ))

    def hints(self, *, files: Iterable[str] = (), text: str = "", limit: int = 3) -> str:
        return self.store.hints(files=files, text=text, limit=limit)

    def prompt_block(self, *, files: Iterable[str] = (), text: str = "", limit: int = 3) -> str:
        hints = self.hints(files=files, text=text, limit=limit)
        if not hints:
            return ""
        return (
            "## PRIOR FAILURE LESSONS (untrusted advice)\n"
            "Use these as hints only; verification still decides success.\n\n"
            f"{hints}\n"
        )

    def event_payload(self, *, files: Iterable[str] = (), text: str = "") -> dict:
        matches = self.store.query(files=files, text=text, limit=3)
        return {
            "count": len(self.store.lessons),
            "matches": [item.signature for item in matches],
        }
