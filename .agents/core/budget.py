#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 3 - hard budget and circuit breaker for meeting mode.

The budget is intentionally small and explicit:

    coder initial        <= 8
    coder rebuttal       <= 2   (coder total <= 10)
    round 1 reviewers    == 4
    round 2 reviewers    <= 2
    total                <= 16
    wall clock           <= 300s

No `safe_writer` and no OpenCode import belongs here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    """Raised before a model call would exceed the meeting budget."""


@dataclass
class MeetingBudget:
    soft: int = 8
    hard: int = 16
    seconds: int = 300
    coder_initial_cap: int = 8
    coder_rebuttal_cap: int = 2
    round1_cap: int = 4
    round2_cap: int = 2
    coder_initial: int = 0
    coder_rebuttal: int = 0
    round1: int = 0
    round2: int = 0
    exceeded: str | None = None
    reason: str | None = None
    started: float = field(default_factory=time.perf_counter)

    @property
    def spent(self) -> int:
        return self.coder_initial + self.coder_rebuttal + self.round1 + self.round2

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    @property
    def remaining(self) -> int:
        return max(0, self.hard - self.spent)

    def check_time(self) -> bool:
        if self.exceeded:
            return False
        if self.elapsed >= self.seconds:
            self.exceeded = "time"
            self.reason = f"time cap {self.seconds}s reached"
            return False
        return True

    def can_spend(self, role: str, kind: str, count: int = 1) -> bool:
        if self.exceeded or not self.check_time():
            return False
        role = str(role or "").lower()
        kind = str(kind or "").lower()
        cap = {
            ("coder", "initial"): self.coder_initial_cap,
            ("coder", "rebuttal"): self.coder_rebuttal_cap,
            ("reviewer", "round1"): self.round1_cap,
            ("reviewer", "round2"): self.round2_cap,
        }.get((role, kind))
        if cap is None:
            self.exceeded = "kind"
            self.reason = f"unknown budget kind: {role}/{kind}"
            return False
        current = {
            ("coder", "initial"): self.coder_initial,
            ("coder", "rebuttal"): self.coder_rebuttal,
            ("reviewer", "round1"): self.round1,
            ("reviewer", "round2"): self.round2,
        }[(role, kind)]
        if current + count > cap:
            self.exceeded = kind
            self.reason = f"{role} {kind} cap {cap} reached"
            return False
        if self.spent + count > self.hard:
            self.exceeded = "requests"
            self.reason = f"hard cap {self.hard} reached"
            return False
        return True

    def spend(self, role: str, kind: str, count: int = 1) -> None:
        if not self.can_spend(role, kind, count):
            raise BudgetExceeded(self.reason or "meeting budget exceeded")
        role = str(role).lower()
        kind = str(kind).lower()
        if role == "coder" and kind == "initial":
            self.coder_initial += count
        elif role == "coder" and kind == "rebuttal":
            self.coder_rebuttal += count
        elif role == "reviewer" and kind == "round1":
            self.round1 += count
        elif role == "reviewer" and kind == "round2":
            self.round2 += count

    def seed(self, *, coder_initial: int = 0, round1: int = 0) -> None:
        """Record work already spent before the meeting object was created."""
        self.coder_initial = max(0, int(coder_initial))
        self.round1 = max(0, int(round1))
        if self.coder_initial > self.coder_initial_cap:
            self.exceeded = "coder_initial"
            self.reason = f"coder initial cap {self.coder_initial_cap} reached"
        if self.round1 > self.round1_cap:
            self.exceeded = "round1"
            self.reason = f"round1 cap {self.round1_cap} reached"
        if self.spent > self.hard:
            self.exceeded = "requests"
            self.reason = f"hard cap {self.hard} reached"

    def can_open_round2(self) -> bool:
        if self.exceeded or not self.check_time():
            return False
        if self.coder_initial > self.coder_initial_cap:
            self.exceeded = "coder_initial"
            self.reason = f"coder initial cap {self.coder_initial_cap} reached"
            return False
        if self.spent + 1 > self.hard:
            self.exceeded = "requests"
            self.reason = f"hard cap {self.hard} reached"
            return False
        return True

    def snapshot(self) -> dict:
        return {
            "soft": self.soft,
            "hard": self.hard,
            "seconds": self.seconds,
            "spent": self.spent,
            "remaining": self.remaining,
            "elapsed_s": round(self.elapsed, 2),
            "coder_initial": self.coder_initial,
            "coder_rebuttal": self.coder_rebuttal,
            "round1": self.round1,
            "round2": self.round2,
            "caps": {
                "coder_initial": self.coder_initial_cap,
                "coder_rebuttal": self.coder_rebuttal_cap,
                "round1": self.round1_cap,
                "round2": self.round2_cap,
            },
            "exceeded": self.exceeded,
            "reason": self.reason,
        }


class CircuitBreaker:
    """Stop after seeing the same failure signature twice."""

    def __init__(self, threshold: int = 2) -> None:
        self.threshold = max(1, int(threshold))
        self.counts: dict[str, int] = {}
        self.tripped = False
        self.signature: str | None = None

    def observe(self, signature: str) -> bool:
        if self.tripped:
            return True
        key = str(signature or "unknown")
        self.counts[key] = self.counts.get(key, 0) + 1
        if self.counts[key] >= self.threshold:
            self.tripped = True
            self.signature = key
        return self.tripped

    def snapshot(self) -> dict:
        return {"tripped": self.tripped, "signature": self.signature,
                "counts": dict(self.counts), "threshold": self.threshold}
