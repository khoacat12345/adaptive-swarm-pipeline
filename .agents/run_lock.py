#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repository-wide run lock (P1-11).

One process may own a project's plan/run state at a time. The lock is a small
JSON file under `.agents/tmp/run.lock`; stale locks can be reclaimed by age.
"""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path


class LockBusy(RuntimeError):
    pass


def lock_path(root: str | Path) -> Path:
    return Path(root).resolve() / ".agents" / "tmp" / "run.lock"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_stale(path: str | Path, *, ttl_seconds: int = 6 * 60 * 60) -> bool:
    path = Path(path)
    data = _read(path)
    pid = int(data.get("pid") or 0)
    created = float(data.get("created_at") or 0)
    if pid and not _alive(pid):
        return True
    return bool(created) and (time.time() - created) > max(60, ttl_seconds)


class RepoLock:
    def __init__(self, root: str | Path, *, owner: str = "swarm",
                 stale_seconds: int = 6 * 60 * 60):
        self.root = Path(root).resolve()
        self.path = lock_path(self.root)
        self.owner = str(owner)
        self.stale_seconds = max(60, int(stale_seconds))
        self.acquired = False

    def acquire(self) -> "RepoLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "owner": self.owner,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "created_at": time.time(),
        }
        for attempt in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                self.acquired = True
                return self
            except FileExistsError:
                if attempt == 0 and is_stale(self.path, ttl_seconds=self.stale_seconds):
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                current = _read(self.path)
                raise LockBusy(
                    f"repo already locked by {current.get('owner', '?')} "
                    f"pid={current.get('pid', '?')}"
                )
        raise LockBusy("could not acquire repo lock")

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            current = _read(self.path)
            if int(current.get("pid") or 0) == os.getpid():
                self.path.unlink()
        except OSError:
            pass
        finally:
            self.acquired = False

    def __enter__(self) -> "RepoLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
