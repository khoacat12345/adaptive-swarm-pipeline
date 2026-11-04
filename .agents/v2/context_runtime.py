#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 4 memory constraints, locks and corruption recovery.

Inspired by letta-code's memory filesystem:
  - per-file character cap
  - core-memory cap
  - read-only constitution files
  - file lock for memory writes
  - quarantine + recovery for corrupt JSONL
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class MemoryConstraintError(ValueError):
    code = "MEMORY_CONSTRAINT"


READ_ONLY_LABELS = {"constitution.md", "constitution_hash"}
DEFAULT_MAX_FILE_CHARACTERS = 20_000
DEFAULT_MAX_CORE_CHARACTERS = 65_536


@dataclass
class ContextItem:
    label: str
    text: str
    tier: str = "working"
    read_only: bool = False


@dataclass
class PackedContext:
    text: str
    items: list[ContextItem]
    evicted: list[str]
    chars: int


@dataclass
class ConstrainedContext:
    max_file_characters: int = DEFAULT_MAX_FILE_CHARACTERS
    max_core_characters: int = DEFAULT_MAX_CORE_CHARACTERS
    items: list[ContextItem] = field(default_factory=list)

    def add(self, label: str, text: str, *, tier: str = "working",
            read_only: bool | None = None) -> ContextItem:
        if tier not in {"working", "recall", "archival"}:
            raise MemoryConstraintError(f"unknown context tier: {tier}")
        value = str(text or "")
        if len(value) > self.max_file_characters:
            raise MemoryConstraintError(
                f"{label}: {len(value)} chars exceeds max_file_characters "
                f"{self.max_file_characters}"
            )
        protected = label in READ_ONLY_LABELS if read_only is None else bool(read_only)
        if protected and any(item.label == label for item in self.items):
            raise MemoryConstraintError(f"{label}: read-only item already exists")
        item = ContextItem(label=label, text=value, tier=tier, read_only=protected)
        self.items.append(item)
        return item

    def pack(self) -> PackedContext:
        core = [item for item in self.items if item.tier == "working"]
        non_core = [item for item in self.items if item.tier != "working"]
        evicted: list[str] = []
        total = sum(len(item.text) for item in self.items)
        # Evict non-read-only archival/recall first, never protected items.
        for item in sorted(non_core, key=lambda item: (item.tier != "archival", item.label)):
            if total <= self.max_core_characters:
                break
            if item.read_only:
                continue
            self.items.remove(item)
            evicted.append(item.label)
            total -= len(item.text)
        if total > self.max_core_characters:
            for item in list(core):
                if total <= self.max_core_characters:
                    break
                if item.read_only:
                    continue
                self.items.remove(item)
                evicted.append(item.label)
                total -= len(item.text)
        if total > self.max_core_characters:
            raise MemoryConstraintError(
                f"read-only core memory exceeds max_core_characters: "
                f"{total} > {self.max_core_characters}"
            )
        text = "\n\n".join(
            f"## {item.tier} [{item.label}]\n{item.text}"
            for item in self.items
        )
        return PackedContext(text=text, items=list(self.items), evicted=evicted, chars=total)


class MemoryLock:
    """Exclusive process lock for a memory file, with explicit timeout."""

    def __init__(self, path: str | Path, *, timeout: float = 5.0):
        self.path = Path(str(path) + ".lock")
        self.timeout = max(0.0, float(timeout))
        self._fd: int | None = None

    def __enter__(self) -> "MemoryLock":
        deadline = time.monotonic() + self.timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise MemoryConstraintError(f"memory lock busy: {self.path}")
                time.sleep(0.02)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


@dataclass
class RecoveryResult:
    path: str
    valid_lines: int
    corrupt_lines: int
    quarantine: str | None
    recovered: bool


def recover_jsonl(path: str | Path, *, quarantine_dir: str | Path | None = None) -> RecoveryResult:
    path = Path(path)
    if not path.exists():
        return RecoveryResult(str(path), 0, 0, None, False)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    valid: list[str] = []
    corrupt = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            corrupt += 1
            continue
        valid.append(line)
    if not corrupt:
        return RecoveryResult(str(path), len(valid), 0, None, False)
    qdir = Path(quarantine_dir) if quarantine_dir else path.parent / "quarantine"
    qdir.mkdir(parents=True, exist_ok=True)
    qpath = qdir / f"{path.name}.{int(time.time() * 1000)}.corrupt"
    shutil.copy2(path, qpath)
    with MemoryLock(path):
        path.write_text("\n".join(valid) + ("\n" if valid else ""), encoding="utf-8", newline="\n")
    return RecoveryResult(str(path), len(valid), corrupt, str(qpath), True)
