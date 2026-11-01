#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-run and per-phase budget enforcement for Adaptive Swarm Pipeline v2.1.

The important property is reserve-before-call: a provider invocation cannot start
unless the controller has already reserved requests/cost/wall time. Settlement
records the actual usage and stops the run on an overrun.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import BudgetBlocked, BudgetError, BudgetExceeded, BudgetOverrun
from .state import atomic_write_json, read_json

SCHEMA_VERSION = "2.1"


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class Envelope:
    max_requests: int
    max_cost_usd: float
    max_wall_seconds: int
    emergency_reserve_pct: float = 10.0
    max_repair_attempts_per_phase: int = 3
    max_consecutive_no_progress: int = 2

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Envelope":
        return cls(
            max_requests=int(data.get("max_requests", 120)),
            max_cost_usd=float(data.get("max_cost_usd", 3.5)),
            max_wall_seconds=int(data.get("max_wall_seconds", 36000)),
            emergency_reserve_pct=float(data.get("emergency_reserve_pct", 10.0)),
            max_repair_attempts_per_phase=int(
                data.get("max_repair_attempts_per_phase", 3)
            ),
            max_consecutive_no_progress=int(
                data.get("max_consecutive_no_progress", 2)
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PhaseBudget:
    max_requests: int
    max_cost_usd: float
    max_wall_seconds: int
    repair_reserve_pct: float = 20.0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PhaseBudget":
        return cls(
            max_requests=int(data.get("max_requests", 24)),
            max_cost_usd=float(data.get("max_cost_usd", 0.8)),
            max_wall_seconds=int(data.get("max_wall_seconds", 7200)),
            repair_reserve_pct=float(data.get("repair_reserve_pct", 20.0)),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BudgetConfig:
    envelope: Envelope
    phases: dict[str, PhaseBudget] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BudgetConfig":
        global_data = dict(data.get("global") or data.get("envelope") or {})
        limits = data.get("limits") or {}
        if limits:
            global_data.setdefault(
                "max_repair_attempts_per_phase",
                limits.get("max_repair_attempts_per_phase", 3),
            )
            global_data.setdefault(
                "max_consecutive_no_progress",
                limits.get("max_consecutive_no_progress", 2),
            )
        envelope = Envelope.from_dict(global_data)
        phases = {
            str(phase_id): PhaseBudget.from_dict(value)
            for phase_id, value in (data.get("phases") or {}).items()
        }
        return cls(envelope=envelope, phases=phases)

    def validate(self) -> "BudgetConfig":
        """Reject allocations that cannot fit inside the signed envelope."""
        if self.envelope.max_requests < 1:
            raise BudgetError("max_requests must be >= 1")
        if self.envelope.max_cost_usd < 0:
            raise BudgetError("max_cost_usd must be >= 0")
        if self.envelope.max_wall_seconds < 1:
            raise BudgetError("max_wall_seconds must be >= 1")
        if not self.phases:
            raise BudgetError("at least one phase budget is required")
        reserve_pct = max(0.0, min(100.0, self.envelope.emergency_reserve_pct))
        normal_requests = self.envelope.max_requests - int(
            self.envelope.max_requests * reserve_pct / 100.0
        )
        normal_cost = self.envelope.max_cost_usd * (1.0 - reserve_pct / 100.0)
        total_requests = sum(item.max_requests for item in self.phases.values())
        total_cost = sum(item.max_cost_usd for item in self.phases.values())
        if total_requests > normal_requests:
            raise BudgetError(
                f"phase requests exceed global envelope: {total_requests} > {normal_requests}"
            )
        if total_cost > normal_cost + 1e-9:
            raise BudgetError(
                f"phase cost exceeds global envelope: {total_cost:.4f} > {normal_cost:.4f}"
            )
        for phase_id, item in self.phases.items():
            if item.max_requests < 1 or item.max_cost_usd < 0 or item.max_wall_seconds < 1:
                raise BudgetError(f"invalid phase budget: {phase_id}")
            if item.max_requests > self.envelope.max_requests:
                raise BudgetError(f"phase request cap exceeds global cap: {phase_id}")
            if item.max_wall_seconds > self.envelope.max_wall_seconds:
                raise BudgetError(f"phase wall cap exceeds global cap: {phase_id}")
        return self

    @classmethod
    def default(cls, phase_ids: list[str] | None = None) -> "BudgetConfig":
        phases = {
            str(phase_id): PhaseBudget(24, 0.70, 7200, 20.0)
            for phase_id in (phase_ids or ["P1"])
        }
        return cls(
            envelope=Envelope(
                max_requests=120,
                max_cost_usd=3.5,
                max_wall_seconds=36000,
                emergency_reserve_pct=10.0,
                max_repair_attempts_per_phase=3,
                max_consecutive_no_progress=2,
            ),
            phases=phases,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "global": self.envelope.as_dict(),
            "phases": {key: value.as_dict() for key, value in self.phases.items()},
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.as_dict()).encode("utf-8")).hexdigest()


class BudgetController:
    """Persisted budget ledger for one run.

    ``clock`` is injectable so wall-clock behavior can be tested without sleeping.
    """

    def __init__(
        self,
        state_dir: str | Path,
        config: BudgetConfig,
        *,
        clock: Callable[[], float] | None = None,
    ):
        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "budget.json"
        self.events_path = self.state_dir / "budget_events.ndjson"
        self.config = config.validate()
        self._clock = clock or time.monotonic
        self._clock_base = self._clock()
        self._elapsed_base = 0.0
        self._lock = threading.RLock()

        if self.path.exists():
            payload = read_json(self.path)
            if payload.get("config_hash") != config.digest:
                raise BudgetError(
                    "budget config changed after run start; refusing to reuse state"
                )
            self._state = payload
            self._elapsed_base = float(payload.get("elapsed_seconds") or 0.0)
        else:
            self._state = self._new_state()
            self._save()

    def _new_state(self) -> dict[str, Any]:
        phases = {
            phase_id: {
                "allocated": phase_budget.as_dict(),
                "used_requests": 0,
                "used_cost_usd": 0.0,
                "used_tokens": 0,
                "used_wall_seconds": 0.0,
                "reserved_requests": 0,
                "reserved_cost_usd": 0.0,
                "reserved_wall_seconds": 0.0,
                "repair_attempts": 0,
            }
            for phase_id, phase_budget in self.config.phases.items()
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "config": self.config.as_dict(),
            "config_hash": self.config.digest,
            "started_at": utc_now_iso(),
            "elapsed_seconds": 0.0,
            "stopped": False,
            "stop_reason": None,
            "global": {
                "used_requests": 0,
                "used_cost_usd": 0.0,
                "used_tokens": 0,
                "used_wall_seconds": 0.0,
                "reserved_requests": 0,
                "reserved_cost_usd": 0.0,
                "reserved_wall_seconds": 0.0,
            },
            "phases": phases,
            "reservations": {},
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _elapsed(self) -> float:
        return max(0.0, self._elapsed_base + (self._clock() - self._clock_base))

    def _save(self) -> None:
        self._state["elapsed_seconds"] = round(self._elapsed(), 3)
        atomic_write_json(self.path, self._state)

    def _append_event(self, event_type: str, data: dict[str, Any]) -> None:
        event = {
            "schema_version": SCHEMA_VERSION,
            "timestamp": utc_now_iso(),
            "event_type": str(event_type),
            "data": data,
        }
        with self._lock:
            with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _global(self) -> dict[str, Any]:
        return self._state["global"]

    def _phase(self, phase_id: str) -> dict[str, Any]:
        phase = self._state.get("phases", {}).get(phase_id)
        if phase is None:
            raise BudgetBlocked(f"phase {phase_id!r} has no budget allocation")
        return phase

    def _normal_request_limit(self) -> int:
        reserve = int(
            self.config.envelope.max_requests
            * max(0.0, min(100.0, self.config.envelope.emergency_reserve_pct))
            / 100.0
        )
        return max(0, self.config.envelope.max_requests - reserve)

    def _assert_not_stopped(self) -> None:
        if self._state.get("stopped"):
            raise BudgetBlocked(
                f"run budget is stopped: {self._state.get('stop_reason') or 'unknown'}"
            )

    def _wall_remaining(self, phase_id: str | None = None) -> float:
        remaining = self.config.envelope.max_wall_seconds - self._elapsed()
        if phase_id is not None:
            phase = self._phase(phase_id)
            phase_cap = float(phase["allocated"]["max_wall_seconds"])
            phase_used = float(phase["used_wall_seconds"]) + float(
                phase["reserved_wall_seconds"]
            )
            remaining = min(remaining, phase_cap - phase_used)
        return max(0.0, remaining)

    def _check_requests(self, phase: dict[str, Any], requests: int, *, allow_emergency: bool) -> None:
        global_used = int(self._global()["used_requests"])
        global_reserved = int(self._global()["reserved_requests"])
        phase_used = int(phase["used_requests"])
        phase_reserved = int(phase["reserved_requests"])
        phase_max = int(phase["allocated"]["max_requests"])

        if phase_used + phase_reserved + requests > phase_max:
            raise BudgetExceeded(
                f"phase request cap would be exceeded: "
                f"{phase_used + phase_reserved + requests} > {phase_max}"
            )
        if global_used + global_reserved + requests > self.config.envelope.max_requests:
            raise BudgetExceeded(
                f"global request cap would be exceeded: "
                f"{global_used + global_reserved + requests} > "
                f"{self.config.envelope.max_requests}"
            )
        if not allow_emergency:
            normal = self._normal_request_limit()
            if global_used + global_reserved + requests > normal:
                raise BudgetExceeded(
                    f"emergency reserve would be touched: "
                    f"{global_used + global_reserved + requests} > {normal}"
                )

    def _check_cost(self, phase: dict[str, Any], cost_usd: float) -> None:
        global_used = float(self._global()["used_cost_usd"])
        global_reserved = float(self._global()["reserved_cost_usd"])
        phase_used = float(phase["used_cost_usd"])
        phase_reserved = float(phase["reserved_cost_usd"])
        phase_max = float(phase["allocated"]["max_cost_usd"])
        if phase_used + phase_reserved + cost_usd > phase_max + 1e-9:
            raise BudgetExceeded(
                f"phase cost cap would be exceeded: "
                f"{phase_used + phase_reserved + cost_usd:.4f} > {phase_max:.4f}"
            )
        if (
            global_used + global_reserved + cost_usd
            > self.config.envelope.max_cost_usd + 1e-9
        ):
            raise BudgetExceeded(
                f"global cost cap would be exceeded: "
                f"{global_used + global_reserved + cost_usd:.4f} > "
                f"{self.config.envelope.max_cost_usd:.4f}"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reserve(
        self,
        phase_id: str,
        *,
        role: str,
        model: str,
        requests: int = 1,
        cost_usd: float | None = None,
        wall_seconds: float = 0.0,
        repair: bool = False,
        allow_emergency: bool = False,
        reservation_id: str | None = None,
    ) -> str:
        """Reserve budget before a provider call; raises without side effects."""
        with self._lock:
            self._assert_not_stopped()
            if requests < 1:
                raise BudgetError("requests must be >= 1")
            if cost_usd is None:
                raise BudgetBlocked(
                    "cost is unknown; paid model calls are blocked by default"
                )
            cost = float(cost_usd)
            if cost < 0:
                raise BudgetError("cost_usd must be >= 0")
            phase = self._phase(phase_id)
            self._check_requests(phase, int(requests), allow_emergency=allow_emergency)
            self._check_cost(phase, cost)
            if self._wall_remaining(phase_id) < max(0.0, float(wall_seconds)):
                raise BudgetExceeded(
                    f"not enough wall time for phase {phase_id}: "
                    f"need {wall_seconds}s, have {self._wall_remaining(phase_id):.1f}s"
                )
            if repair:
                attempts = int(phase.get("repair_attempts") or 0)
                if attempts >= self.config.envelope.max_repair_attempts_per_phase:
                    raise BudgetExceeded(
                        f"repair cap reached for phase {phase_id}: {attempts}"
                    )
                phase["repair_attempts"] = attempts + 1

            rid = reservation_id or f"res_{uuid.uuid4().hex[:12]}"
            if rid in self._state["reservations"]:
                raise BudgetError(f"reservation already exists: {rid}")
            reservation = {
                "id": rid,
                "phase_id": phase_id,
                "role": role,
                "model": model,
                "requests": int(requests),
                "cost_usd": cost,
                "wall_seconds": max(0.0, float(wall_seconds)),
                "repair": bool(repair),
                "status": "reserved",
                "created_at": utc_now_iso(),
            }
            self._state["reservations"][rid] = reservation
            global_data = self._global()
            global_data["reserved_requests"] += int(requests)
            global_data["reserved_cost_usd"] += cost
            global_data["reserved_wall_seconds"] += reservation["wall_seconds"]
            phase["reserved_requests"] += int(requests)
            phase["reserved_cost_usd"] += cost
            phase["reserved_wall_seconds"] += reservation["wall_seconds"]
            self._append_event("budget.reserve", reservation)
            self._save()
            return rid

    def settle(
        self,
        reservation_id: str,
        *,
        requests: int | None = None,
        tokens: int = 0,
        cost_usd: float | None = None,
        wall_seconds: float | None = None,
        cost_confidence: str = "high",
    ) -> dict[str, Any]:
        """Settle actual usage. An overrun stops the run before more calls."""
        with self._lock:
            reservation = self._state["reservations"].get(reservation_id)
            if reservation is None:
                raise BudgetError(f"unknown reservation: {reservation_id}")
            if reservation.get("status") != "reserved":
                raise BudgetError(
                    f"reservation {reservation_id} is already {reservation.get('status')}"
                )
            actual_requests = int(
                reservation["requests"] if requests is None else requests
            )
            actual_cost = float(reservation["cost_usd"] if cost_usd is None else cost_usd)
            actual_wall = float(
                reservation["wall_seconds"] if wall_seconds is None else wall_seconds
            )
            if actual_requests < 0 or actual_cost < 0 or actual_wall < 0:
                raise BudgetError("actual usage cannot be negative")

            over_requests = actual_requests > int(reservation["requests"])
            over_cost = actual_cost > float(reservation["cost_usd"]) + 1e-9
            phase = self._phase(str(reservation["phase_id"]))
            global_data = self._global()

            global_data["reserved_requests"] -= int(reservation["requests"])
            global_data["reserved_cost_usd"] -= float(reservation["cost_usd"])
            global_data["reserved_wall_seconds"] -= float(reservation["wall_seconds"])
            phase["reserved_requests"] -= int(reservation["requests"])
            phase["reserved_cost_usd"] -= float(reservation["cost_usd"])
            phase["reserved_wall_seconds"] -= float(reservation["wall_seconds"])

            global_data["used_requests"] += actual_requests
            global_data["used_cost_usd"] += actual_cost
            global_data["used_tokens"] += max(0, int(tokens))
            global_data["used_wall_seconds"] += actual_wall
            phase["used_requests"] += actual_requests
            phase["used_cost_usd"] += actual_cost
            phase["used_tokens"] += max(0, int(tokens))
            phase["used_wall_seconds"] += actual_wall
            reservation["status"] = "settled"
            reservation["settled_at"] = utc_now_iso()
            reservation["actual"] = {
                "requests": actual_requests,
                "cost_usd": actual_cost,
                "tokens": max(0, int(tokens)),
                "wall_seconds": actual_wall,
                "cost_confidence": cost_confidence,
            }
            self._append_event("budget.settle", reservation)

            if over_requests or over_cost:
                self._state["stopped"] = True
                self._state["stop_reason"] = "budget_overrun"
                self._append_event(
                    "budget.overrun",
                    {
                        "reservation_id": reservation_id,
                        "reserved": {
                            "requests": reservation["requests"],
                            "cost_usd": reservation["cost_usd"],
                        },
                        "actual": reservation["actual"],
                    },
                )
                self._save()
                raise BudgetOverrun(
                    f"provider usage exceeded reservation for {reservation_id}"
                )

            self._save()
            return self.summary(phase_id=str(reservation["phase_id"]))

    def release(self, reservation_id: str, *, reason: str = "cancelled") -> None:
        with self._lock:
            reservation = self._state["reservations"].get(reservation_id)
            if reservation is None:
                raise BudgetError(f"unknown reservation: {reservation_id}")
            if reservation.get("status") != "reserved":
                return
            phase = self._phase(str(reservation["phase_id"]))
            global_data = self._global()
            global_data["reserved_requests"] -= int(reservation["requests"])
            global_data["reserved_cost_usd"] -= float(reservation["cost_usd"])
            global_data["reserved_wall_seconds"] -= float(reservation["wall_seconds"])
            phase["reserved_requests"] -= int(reservation["requests"])
            phase["reserved_cost_usd"] -= float(reservation["cost_usd"])
            phase["reserved_wall_seconds"] -= float(reservation["wall_seconds"])
            reservation["status"] = "released"
            reservation["released_reason"] = reason
            self._append_event(
                "budget.release",
                {"reservation_id": reservation_id, "reason": reason},
            )
            self._save()

    def stop(self, reason: str) -> None:
        with self._lock:
            self._state["stopped"] = True
            self._state["stop_reason"] = str(reason)
            self._append_event("budget.stop", {"reason": str(reason)})
            self._save()

    def remaining(self, phase_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            normal = self._normal_request_limit()
            global_data = self._global()
            out: dict[str, Any] = {
                "global": {
                    "requests": max(
                        0,
                        self.config.envelope.max_requests
                        - int(global_data["used_requests"])
                        - int(global_data["reserved_requests"]),
                    ),
                    "normal_requests": max(
                        0,
                        normal
                        - int(global_data["used_requests"])
                        - int(global_data["reserved_requests"]),
                    ),
                    "cost_usd": max(
                        0.0,
                        self.config.envelope.max_cost_usd
                        - float(global_data["used_cost_usd"])
                        - float(global_data["reserved_cost_usd"]),
                    ),
                    "wall_seconds": self._wall_remaining(),
                },
                "stopped": bool(self._state.get("stopped")),
                "stop_reason": self._state.get("stop_reason"),
            }
            if phase_id is not None:
                phase = self._phase(phase_id)
                out["phase"] = {
                    "requests": max(
                        0,
                        int(phase["allocated"]["max_requests"])
                        - int(phase["used_requests"])
                        - int(phase["reserved_requests"]),
                    ),
                    "cost_usd": max(
                        0.0,
                        float(phase["allocated"]["max_cost_usd"])
                        - float(phase["used_cost_usd"])
                        - float(phase["reserved_cost_usd"]),
                    ),
                    "wall_seconds": self._wall_remaining(phase_id),
                    "repair_attempts": int(phase.get("repair_attempts") or 0),
                }
            return out

    def summary(self, phase_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            result = {
                "config_hash": self.config.digest,
                "started_at": self._state.get("started_at"),
                "elapsed_seconds": round(self._elapsed(), 3),
                "stopped": bool(self._state.get("stopped")),
                "stop_reason": self._state.get("stop_reason"),
                "global": dict(self._global()),
                "reservations": len(self._state.get("reservations") or {}),
                "remaining": self.remaining(phase_id),
            }
            if phase_id is not None:
                result["phase"] = dict(self._phase(phase_id))
            return result

    def phase_status(self, phase_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._phase(phase_id))

    @property
    def config_hash(self) -> str:
        return self.config.digest

    @property
    def stopped(self) -> bool:
        return bool(self._state.get("stopped"))
