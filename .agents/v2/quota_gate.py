#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quota-aware dispatch gate (F05).

``quota.QuotaTracker`` answers "how much is used".  This module answers the
dispatch question: *may this task start on this model right now?*  Rules:

* a model without a monthly cap is always dispatchable;
* a paid model must keep a reserve for verification and reviewers;
* if the requested model is exhausted, try the candidate chain and reassign;
* if nothing has headroom, pause the task until the quota window resets or an
  operator changes the plan.

The gate never performs a model call and never touches the repository.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from v2.errors import ExecutionError

try:  # The module is importable both as ``quota`` and ``agents.quota``.
    import quota as quota_module  # type: ignore
except ImportError:  # pragma: no cover - fallback for package imports
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import quota as quota_module  # type: ignore


class QuotaGateError(ExecutionError):
    code = "QUOTA_GATE_INVALID"


@dataclass
class QuotaDecision:
    """One dispatch decision with redaction-safe evidence."""

    model: str | None = None
    ok: bool = True
    action: str = "dispatch"  # dispatch | reassign | pause
    reason: str = ""
    wait_seconds: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "ok": self.ok,
            "action": self.action,
            "reason": self.reason,
            "wait_seconds": self.wait_seconds,
            "evidence": dict(self.evidence),
        }


def _dedupe(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


class QuotaGate:
    """Stateful dispatch gate over a :class:`quota.QuotaTracker`."""

    def __init__(
        self,
        tracker: Any | None = None,
        *,
        root: str | Path | None = None,
        reserve_pct: float = 15.0,
        verifier_reserve_pct: float = 10.0,
        candidates: Iterable[str] = (),
        strict: bool | None = None,
        refresh: bool = False,
    ):
        self.tracker = tracker
        self.root = Path(root).resolve() if root else None
        self.reserve_pct = quota_module._clamp_pct(reserve_pct)
        self.verifier_reserve_pct = quota_module._clamp_pct(verifier_reserve_pct)
        self.candidates = _dedupe(candidates)
        self.strict = strict
        self.refresh = bool(refresh)
        self._refreshed = tracker is not None
        self.decisions: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    def _ensure_tracker(self) -> Any:
        if self.tracker is None:
            self.tracker = quota_module.QuotaTracker(root=self.root)
        if self.refresh and not self._refreshed:
            try:
                self.tracker.refresh()
            except Exception:  # noqa: BLE001 - a gate must never crash a run
                pass
            self._refreshed = True
        return self.tracker

    def _strict(self) -> bool:
        if self.strict is not None:
            return bool(self.strict)
        import os

        return str(os.environ.get("SWARM_QUOTA_STRICT", "0")).strip().lower() in {
            "1", "true", "yes", "on",
        }

    # ------------------------------------------------------------------
    def decide(
        self,
        model: str | None,
        *,
        candidates: Iterable[str] = (),
        phase_id: str | None = None,
        task_id: str | None = None,
        reserve_pct: float | None = None,
    ) -> QuotaDecision:
        """Return whether ``model`` (or a candidate) may be dispatched."""
        reserve = quota_module._clamp_pct(
            self.reserve_pct if reserve_pct is None else reserve_pct
        )
        base = str(model or "").strip()
        chain = _dedupe([base, *candidates, *self.candidates])
        if not chain:
            return QuotaDecision(
                model=None, ok=True, action="dispatch",
                reason="no model binding; quota gate not applicable",
                evidence={"phase_id": phase_id, "task_id": task_id},
            )

        tracker = self._ensure_tracker()
        decisions: dict[str, dict[str, Any]] = {}
        for candidate in chain:
            try:
                info = tracker.remaining(candidate)
            except Exception as exc:  # noqa: BLE001
                info = {"model": candidate, "ok": False, "unknown": True,
                        "error": str(exc)[:200]}
            decisions[candidate] = quota_module.evaluate_headroom(
                candidate, info, reserve_pct=reserve,
            )

        chosen: str | None = None
        for candidate in chain:
            if decisions[candidate].get("ok"):
                chosen = candidate
                break

        shared = {}
        for candidate in decisions.values():
            if candidate.get("shared"):
                shared = candidate["shared"]
                break

        evidence = {
            "phase_id": phase_id,
            "task_id": task_id,
            "requested": base or None,
            "chain": chain,
            "reserve_pct": reserve,
            "verifier_reserve_pct": self.verifier_reserve_pct,
            "windows": {name: decisions[name].get("windows", {}) for name in chain},
            "blocked": {
                name: decisions[name].get("blocked_windows", []) for name in chain
            },
            "shared": shared,
            "source": (tracker.snapshot().get("shared") or {}).get("source")
            if hasattr(tracker, "snapshot") else None,
        }

        if chosen is not None:
            action = "dispatch" if chosen == base else "reassign"
            decision = QuotaDecision(
                model=chosen,
                ok=True,
                action=action,
                reason=(
                    decisions[chosen].get("reason") if action == "dispatch"
                    else f"reassigned from {base or 'none'} to {chosen}"
                ),
                evidence=evidence,
            )
            self.decisions.append(decision.as_dict())
            return decision

        unknown_paid = [
            name for name in chain if decisions[name].get("unknown")
        ]
        if unknown_paid and not self._strict():
            decision = QuotaDecision(
                model=base or (chain[0] if chain else None),
                ok=True,
                action="dispatch",
                reason=(
                    "quota usage unavailable; dispatching in non-strict mode "
                    "(set SWARM_QUOTA_STRICT=1 to fail closed)"
                ),
                evidence={**evidence, "ungated": True, "unknown": unknown_paid},
            )
            self.decisions.append(decision.as_dict())
            return decision

        blocked = sorted({
            window for name in chain
            for window in decisions[name].get("blocked_windows", [])
        })
        wait = self.wait_seconds_for(chain)
        decision = QuotaDecision(
            model=base or (chain[0] if chain else None),
            ok=False,
            action="pause",
            reason=(
                "quota reserve exhausted"
                + (f" for: {', '.join(blocked)}" if blocked else "")
            ),
            wait_seconds=wait,
            evidence=evidence,
        )
        self.decisions.append(decision.as_dict())
        return decision

    # ------------------------------------------------------------------
    def wait_seconds_for(self, models: Iterable[str]) -> int:
        """Seconds until the earliest quota window reset, if known."""
        tracker = self._ensure_tracker()
        resets: list[int] = []
        try:
            snapshot = tracker.snapshot()
        except Exception:  # noqa: BLE001
            snapshot = {}
        for window in ("today", "week", "month"):
            stamp = (snapshot.get("reset") or {}).get(window)
            try:
                value = int(stamp or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                resets.append(value)
        if not resets:
            return 0
        now_ms = int(time.time() * 1000)
        return max(0, int((min(resets) - now_ms) / 1000))

    def snapshot(self) -> dict[str, Any]:
        tracker = self._ensure_tracker()
        try:
            remaining = {
                model: tracker.remaining(model) for model in (
                    list(self.candidates) or list(getattr(tracker, "caps", {}))
                )
            }
        except Exception:  # noqa: BLE001
            remaining = {}
        return {
            "strict": self._strict(),
            "reserve_pct": self.reserve_pct,
            "verifier_reserve_pct": self.verifier_reserve_pct,
            "candidates": list(self.candidates),
            "tracker": tracker.snapshot() if hasattr(tracker, "snapshot") else {},
            "remaining": remaining,
            "decisions": list(self.decisions[-50:]),
        }

    def headroom_table(self, models: Iterable[str] | None = None) -> dict[str, dict]:
        tracker = self._ensure_tracker()
        names = _dedupe(models or self.candidates or getattr(tracker, "caps", {}))
        return {
            model: tracker.headroom(model, reserve_pct=self.reserve_pct)
            for model in names
        }


def build_gate(
    root: str | Path | None = None,
    *,
    tracker: Any | None = None,
    candidates: Iterable[str] = (),
    reserve_pct: float = 15.0,
    strict: bool | None = None,
) -> QuotaGate:
    return QuotaGate(
        tracker, root=root, candidates=candidates,
        reserve_pct=reserve_pct, strict=strict,
    )
