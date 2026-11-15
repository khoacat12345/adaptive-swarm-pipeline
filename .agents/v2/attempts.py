#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TaskAttempt lifecycle and history for v2 (F02).

Every dispatch of a work order is an attempt.  The state machine is deliberately
small::

    IDLE -> DISPATCHED -> WORKING -> VERIFYING -> SETTLED
                  |           |          |
                  +-----------+----------+--> FAILED
                  +-----------+----------+--> LOST

``IDLE`` is not active: an attempt that was recorded but never dispatched can
only be dispatched.  ``FAILED``/``LOST`` are reachable from the three active
states (``DISPATCHED``, ``WORKING``, ``VERIFYING``) and, together with
``SETTLED``, are terminal.

``LOST`` is reserved for a worker that stopped heart-beating (process kill,
provider disconnect).  The history is an append-only JSONL file inside the run
directory, so a crashed process leaves a readable audit trail and the next run
can tell a retry from a first attempt.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import ExecutionError

IDLE = "IDLE"
DISPATCHED = "DISPATCHED"
WORKING = "WORKING"
VERIFYING = "VERIFYING"
SETTLED = "SETTLED"
FAILED = "FAILED"
LOST = "LOST"

ORDER = (IDLE, DISPATCHED, WORKING, VERIFYING, SETTLED, FAILED, LOST)
TERMINAL = {SETTLED, FAILED, LOST}
ACTIVE = {DISPATCHED, WORKING, VERIFYING}

TRANSITIONS: dict[str, set[str]] = {
    IDLE: {DISPATCHED},
    DISPATCHED: {WORKING, FAILED, LOST},
    WORKING: {VERIFYING, FAILED, LOST},
    VERIFYING: {SETTLED, FAILED, LOST},
    SETTLED: set(),
    FAILED: set(),
    LOST: set(),
}


class AttemptError(ExecutionError):
    code = "ATTEMPT_INVALID"


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def legal_transitions(state: str) -> set[str]:
    return set(TRANSITIONS.get(str(state), set()))


def assert_transition(current: str, target: str) -> None:
    current = str(current).upper()
    target = str(target).upper()
    if target not in TRANSITIONS:
        raise AttemptError(f"unknown attempt state: {target}")
    if target not in TRANSITIONS.get(current, set()):
        raise AttemptError(f"illegal attempt transition: {current} -> {target}")


@dataclass
class Attempt:
    """One in-memory attempt record; persisted as a full snapshot per event."""

    attempt_id: str
    task_id: str
    run_id: str = ""
    phase_id: str = ""
    worker: str = "coder"
    worker_id: str = ""
    model: str = ""
    state: str = IDLE
    number: int = 1
    started_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    ended_at: str | None = None
    heartbeat_at: str = ""
    reason: str = ""
    error: str = ""
    escalate_to: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.attempt_id = str(self.attempt_id or f"att_{uuid.uuid4().hex[:12]}")
        self.task_id = str(self.task_id or "").strip()
        if not self.task_id:
            raise AttemptError("attempt requires a task_id")
        self.worker = str(self.worker or "coder")
        self.worker_id = str(self.worker_id or self.worker)
        self.state = str(self.state or IDLE).upper()
        if self.state not in TRANSITIONS:
            raise AttemptError(f"unknown attempt state: {self.state}")
        self.number = max(1, int(self.number or 1))
        self.heartbeat_at = self.heartbeat_at or self.started_at
        if not self.history:
            self.history = [{
                "state": self.state,
                "at": self.started_at,
                "reason": "",
            }]

    def transition(self, state: str, *, reason: str = "", at: str | None = None) -> "Attempt":
        target = str(state or "").upper()
        assert_transition(self.state, target)
        stamp = at or utc_now_iso()
        self.state = target
        self.updated_at = stamp
        self.heartbeat_at = stamp
        if reason:
            self.reason = str(reason)
        if target in TERMINAL:
            self.ended_at = stamp
        self.history.append({"state": target, "at": stamp, "reason": str(reason or "")})
        return self

    def heartbeat(self, *, at: str | None = None) -> "Attempt":
        if self.state not in ACTIVE:
            raise AttemptError(f"cannot heartbeat a {self.state} attempt")
        self.heartbeat_at = at or utc_now_iso()
        self.updated_at = self.heartbeat_at
        return self

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "phase_id": self.phase_id,
            "worker": self.worker,
            "worker_id": self.worker_id or self.worker,
            "model": self.model,
            "state": self.state,
            "number": self.number,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "ended_at": self.ended_at,
            "heartbeat_at": self.heartbeat_at,
            "reason": self.reason,
            "error": self.error,
            "escalate_to": self.escalate_to,
            "metadata": dict(self.metadata),
            "history": [dict(item) for item in self.history],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Attempt":
        worker = str(data.get("worker") or data.get("worker_id") or "coder")
        return cls(
            attempt_id=str(data.get("attempt_id") or ""),
            task_id=str(data.get("task_id") or ""),
            run_id=str(data.get("run_id") or ""),
            phase_id=str(data.get("phase_id") or ""),
            worker=worker,
            worker_id=str(data.get("worker_id") or worker),
            model=str(data.get("model") or ""),
            state=str(data.get("state") or IDLE).upper(),
            number=int(data.get("number") or 1),
            started_at=str(data.get("started_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
            ended_at=data.get("ended_at"),
            heartbeat_at=str(data.get("heartbeat_at") or ""),
            reason=str(data.get("reason") or ""),
            error=str(data.get("error") or ""),
            escalate_to=str(data.get("escalate_to") or ""),
            metadata=dict(data.get("metadata") or {}),
            history=[dict(item) for item in (data.get("history") or [])],
        )


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry plus optional model escalation.

    Escalation is a list of models tried in order after the first failure.  The
    policy is pure: it never imports a provider, it only says which model a
    caller *should* use and how long to wait before the retry.
    """

    max_attempts: int = 3
    escalation: tuple[str, ...] = ()
    base_delay_seconds: float = 0.0
    max_delay_seconds: float = 60.0

    def should_retry(self, attempt_number: int) -> bool:
        return int(attempt_number) < max(1, int(self.max_attempts))

    def next_model(self, model: str, attempt_number: int) -> str:
        """Model for retry ``attempt_number`` (1-based, first retry is 1)."""
        if not self.escalation or attempt_number <= 1:
            return str(model)
        index = min(int(attempt_number) - 2, len(self.escalation) - 1)
        return str(self.escalation[max(0, index)])

    def escalated_model(self, model: str, attempt_number: int) -> str:
        candidate = self.next_model(model, attempt_number)
        return candidate if candidate and candidate != str(model) else ""

    def delay(self, attempt_number: int) -> float:
        if self.base_delay_seconds <= 0 or attempt_number <= 1:
            return 0.0
        delay = float(self.base_delay_seconds) * (2 ** max(0, int(attempt_number) - 2))
        return float(min(max(0.0, delay), float(self.max_delay_seconds)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": int(self.max_attempts),
            "escalation": list(self.escalation),
            "base_delay_seconds": float(self.base_delay_seconds),
            "max_delay_seconds": float(self.max_delay_seconds),
        }


class AttemptHistory:
    """Append-only attempt journal stored under a run directory."""

    def __init__(self, path: str | Path):
        path = Path(path)
        if path.suffix != ".ndjson":
            path = path / "attempts.ndjson"
        self.path = path
        self._attempts: dict[str, Attempt] = {}
        self._load()

    # -- persistence -------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise AttemptError(f"cannot read attempt history: {exc}") from exc
        for line in lines:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            try:
                attempt = Attempt.from_dict(payload)
            except (AttemptError, TypeError, ValueError):
                continue
            self._attempts[attempt.attempt_id] = attempt

    def _append(self, attempt: Attempt) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(attempt.as_dict(), ensure_ascii=False, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    # -- lifecycle ---------------------------------------------------
    def start(
        self,
        task_id: str,
        *,
        run_id: str = "",
        phase_id: str = "",
        worker: str = "coder",
        model: str = "",
        number: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        dispatch: bool = True,
    ) -> Attempt:
        previous = self.latest(str(task_id))
        attempt = Attempt(
            attempt_id=f"att_{uuid.uuid4().hex[:12]}",
            task_id=str(task_id),
            run_id=str(run_id),
            phase_id=str(phase_id),
            worker=str(worker),
            model=str(model),
            number=int(number or ((previous.number + 1) if previous else 1)),
            metadata=dict(metadata or {}),
        )
        self._attempts[attempt.attempt_id] = attempt
        if dispatch:
            attempt.transition(DISPATCHED, reason="dispatch")
        else:
            attempt.history[0]["state"] = IDLE
        self._append(attempt)
        return attempt

    def transition(self, attempt_id: str, state: str, *,
                   reason: str = "", error: str = "") -> Attempt:
        attempt = self._attempts.get(str(attempt_id))
        if attempt is None:
            raise AttemptError(f"unknown attempt: {attempt_id}")
        if error:
            attempt.error = str(error)
        attempt.transition(state, reason=reason)
        self._append(attempt)
        return attempt

    def heartbeat(self, attempt_id: str) -> Attempt:
        attempt = self._attempts.get(str(attempt_id))
        if attempt is None:
            raise AttemptError(f"unknown attempt: {attempt_id}")
        attempt.heartbeat()
        self._append(attempt)
        return attempt

    def settle(self, attempt_id: str, *, reason: str = "") -> Attempt:
        return self.transition(attempt_id, SETTLED, reason=reason or "verified")

    def fail(self, attempt_id: str, *, reason: str = "") -> Attempt:
        return self.transition(attempt_id, FAILED, reason=reason or "failed")

    def lose(self, attempt_id: str, *, reason: str = "") -> Attempt:
        return self.transition(attempt_id, LOST, reason=reason or "worker lost")

    def mark_lost(self, *, timeout_seconds: float, now: float | None = None) -> list[Attempt]:
        """Mark active attempts whose heartbeat is older than the timeout."""
        limit = float(max(0.0, timeout_seconds))
        if limit <= 0:
            return []
        current = float(now if now is not None else datetime.now(timezone.utc).timestamp())
        lost: list[Attempt] = []
        for attempt in list(self._attempts.values()):
            if attempt.state not in ACTIVE:
                continue
            stamp = _epoch(attempt.heartbeat_at or attempt.updated_at)
            if stamp is None:
                continue
            if current - stamp >= limit:
                attempt.transition(LOST, reason=f"heartbeat older than {limit}s")
                self._append(attempt)
                lost.append(attempt)
        return lost

    # -- queries -----------------------------------------------------
    def get(self, attempt_id: str) -> Attempt | None:
        return self._attempts.get(str(attempt_id))

    def all(self) -> list[Attempt]:
        return sorted(
            self._attempts.values(),
            key=lambda attempt: (attempt.started_at, attempt.attempt_id),
        )

    def for_task(self, task_id: str) -> list[Attempt]:
        return [attempt for attempt in self.all() if attempt.task_id == str(task_id)]

    def latest(self, task_id: str) -> Attempt | None:
        attempts = self.for_task(task_id)
        return attempts[-1] if attempts else None

    def active(self) -> list[Attempt]:
        return [attempt for attempt in self.all() if attempt.state in ACTIVE]

    def summary(self) -> dict[str, Any]:
        attempts = self.all()
        by_state = {state: 0 for state in ORDER}
        for attempt in attempts:
            by_state[attempt.state] = by_state.get(attempt.state, 0) + 1
        tasks: dict[str, dict[str, Any]] = {}
        for attempt in attempts:
            row = tasks.setdefault(attempt.task_id, {
                "task_id": attempt.task_id,
                "attempts": 0,
                "state": attempt.state,
                "models": [],
                "last_error": "",
                "last_at": attempt.updated_at,
            })
            row["attempts"] += 1
            row["state"] = attempt.state
            row["last_at"] = attempt.updated_at
            if attempt.model and attempt.model not in row["models"]:
                row["models"].append(attempt.model)
            if attempt.error:
                row["last_error"] = attempt.error
        return {
            "total": len(attempts),
            "by_state": by_state,
            "retries": max(0, len(attempts) - len(tasks)),
            "tasks": [
                tasks[task_id] for task_id in sorted(tasks)
            ],
            "active": len(self.active()),
            "lost": by_state.get(LOST, 0),
            "history_path": str(self.path),
        }

    def as_list(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return [attempt.as_dict() for attempt in self.all()[-max(1, int(limit)):]]


def _epoch(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def summarize_attempts(attempts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(item) for item in attempts if isinstance(item, Mapping)]
    by_state: dict[str, int] = {}
    tasks: set[str] = set()
    for row in rows:
        state = str(row.get("state") or IDLE)
        by_state[state] = by_state.get(state, 0) + 1
        tasks.add(str(row.get("task_id") or ""))
    return {
        "total": len(rows),
        "by_state": by_state,
        "retries": max(0, len(rows) - len({item for item in tasks if item})),
        "tasks": sorted(item for item in tasks if item),
    }
