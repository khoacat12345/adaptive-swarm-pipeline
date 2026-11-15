#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F05: quota-aware dispatch gate for v2.

The gate answers one question before a task leaves the queue: *may this task
start right now on this model?*  It is a thin, deterministic v2 facade over
:mod:`quota` so the scheduler, the runner and the execution engine all share a
single implementation:

* :func:`evaluate_headroom` / :func:`choose_with_headroom` are the pure rules
  and are safe to unit-test without touching the network;
* :class:`QuotaGate` / :class:`QuotaDecision` / :func:`build_gate` are
  re-exported (the canonical implementation lives in ``quota.py``);
* :func:`gate_from_snapshot` builds an offline gate from a cached
  ``QuotaTracker.snapshot()`` payload -- dispatch must never block on a live
  quota fetch, and air-gapped machines must still run;
* :func:`normalize_decision` / :func:`snapshot_evidence` /
  :func:`record_decision` produce the redaction-safe payloads recorded as
  ``quota.decision`` and ``quota.snapshot`` events, which
  ``unified.state._quota_projection`` surfaces on the dashboard.

Reserve policy
--------------
``reserve_pct`` (default 15 %) is kept free in *every* quota window so
verification commands and reviewer seats can still run after coder work has
been dispatched.  ``verifier_reserve_pct`` is the share of that buffer that
belongs to verification; it is recorded in the decision evidence so cost
reports can attribute the protected budget.

Decisions
---------
``dispatch``   the requested model has headroom (or has no cap at all);
``reassign``   the requested model is exhausted and another candidate has room;
``pause``      no candidate has headroom, or quota is unknown in strict mode.

Unknown quota is advisory by default: the decision is ``dispatch`` with
``evidence["ungated"] = True``.  Set ``SWARM_QUOTA_STRICT=1`` (or pass
``strict=True``) to fail closed instead.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

try:  # ``.agents`` is normally on sys.path when v2 modules are imported
    import quota as _quota
except ImportError:  # pragma: no cover - package-relative fallback
    _AGENTS_DIR = Path(__file__).resolve().parents[1]
    if str(_AGENTS_DIR) not in sys.path:
        sys.path.insert(0, str(_AGENTS_DIR))
    import quota as _quota

DEFAULT_RESERVE_PCT = 15.0
DEFAULT_VERIFIER_RESERVE_PCT = 10.0

DISPATCH = "dispatch"
REASSIGN = "reassign"
PAUSE = "pause"
SKIP = "skip"

#: Event types recognised by ``unified.state._quota_projection``.
QUOTA_EVENT_TYPES = ("quota.decision", "quota.snapshot", "quota.gate")

#: Canonical implementation, re-exported so callers only import ``v2``.
QuotaDecision = _quota.QuotaDecision
QuotaGate = _quota.QuotaGate
QuotaTracker = _quota.QuotaTracker
evaluate_headroom = _quota.evaluate_headroom
choose_with_headroom = _quota.choose_with_headroom


def build_gate(
    root: str | Path | None = None,
    *,
    tracker: Any | None = None,
    candidates: Iterable[str] = (),
    reserve_pct: float = DEFAULT_RESERVE_PCT,
    verifier_reserve_pct: float = DEFAULT_VERIFIER_RESERVE_PCT,
    strict: bool | None = None,
    refresh: bool = False,
) -> QuotaGate:
    """Build the configured dispatch gate.

    ``refresh`` is off by default: a dispatch decision must stay cheap and
    offline-safe unless the caller explicitly wants a live quota refresh.
    """
    return QuotaGate(
        tracker,
        root=root,
        candidates=candidates,
        reserve_pct=reserve_pct,
        verifier_reserve_pct=verifier_reserve_pct,
        strict=strict,
        refresh=refresh,
    )


def gate_from_snapshot(
    payload: Mapping[str, Any] | None,
    *,
    root: str | Path | None = None,
    candidates: Iterable[str] = (),
    reserve_pct: float = DEFAULT_RESERVE_PCT,
    verifier_reserve_pct: float = DEFAULT_VERIFIER_RESERVE_PCT,
    strict: bool | None = None,
    plan_cap: float | None = None,
) -> QuotaGate:
    """Offline gate over a cached ``QuotaTracker.snapshot()`` payload.

    Used by tests, replay and air-gapped dispatch: no sqlite, no subprocess and
    no HTTP request is performed.
    """
    tracker = QuotaTracker.from_snapshot(
        dict(payload or {}), root=root, plan_cap=plan_cap, cache_ttl=0,
    )
    return build_gate(
        root,
        tracker=tracker,
        candidates=candidates,
        reserve_pct=reserve_pct,
        verifier_reserve_pct=verifier_reserve_pct,
        strict=strict,
    )


def is_paused(decision: Any) -> bool:
    """True when a decision must keep the task out of the queue."""
    payload = normalize_decision(decision)
    return payload["action"] == PAUSE or not payload["ok"]


def normalize_decision(decision: Any) -> dict[str, Any]:
    """Normalise a gate decision into the recorded evidence schema.

    Accepts a :class:`QuotaDecision`, any object with ``as_dict()`` (the
    scheduler returns those) or a plain mapping.  The result always carries the
    six stable keys so event consumers never special-case a producer.
    """
    if hasattr(decision, "as_dict"):
        payload = dict(decision.as_dict())
    elif isinstance(decision, Mapping):
        payload = dict(decision)
    else:  # pragma: no cover - defensive
        raise TypeError(
            f"cannot normalize quota decision of type {type(decision).__name__}"
        )

    action = str(payload.get("action") or DISPATCH)
    payload["action"] = action
    payload["ok"] = bool(payload.get("ok", action != PAUSE))
    payload["model"] = payload.get("model")
    payload["reason"] = str(payload.get("reason") or "")
    payload["wait_seconds"] = int(payload.get("wait_seconds") or 0)
    payload["evidence"] = dict(payload.get("evidence") or {})
    return payload


def record_decision(
    store: Any,
    decision: Any,
    *,
    task_id: str | None = None,
    phase_id: str | None = None,
    event_type: str = "quota.decision",
) -> dict[str, Any]:
    """Append one normalised decision to the run event journal.

    Evidence is optional: a store that cannot write must never fail a dispatch.
    """
    payload = normalize_decision(decision)
    if task_id is not None:
        payload.setdefault("task_id", task_id)
    if phase_id is not None:
        payload.setdefault("phase_id", phase_id)
    append = getattr(store, "append_event", None)
    if not callable(append):
        payload["recorded"] = False
        payload["record_error"] = "store has no append_event"
        return payload
    try:
        append(event_type, dict(payload))
    except Exception as exc:  # noqa: BLE001 - evidence must not break the run
        payload["recorded"] = False
        payload["record_error"] = f"{type(exc).__name__}: {exc}"[:200]
    else:
        payload["recorded"] = True
    return payload


def snapshot_evidence(
    gate: Any,
    models: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Headroom summary for ``quota.snapshot`` events / the dashboard panel.

    ``headroom_pct`` is the tightest window across the reported models, i.e.
    the buffer left before a dispatch would eat into the reserve.  When the
    tracker has no data the snapshot is explicitly ``available: false`` rather
    than looking healthy.
    """
    tracker = getattr(gate, "tracker", None)
    reserve = float(getattr(gate, "reserve_pct", DEFAULT_RESERVE_PCT))
    names = list(models or getattr(gate, "candidates", ()) or ())
    if tracker is None:
        return {
            "available": False,
            "ok": False,
            "reason": "no quota tracker",
            "reserve_pct": reserve,
            "models": {},
        }
    if not names:
        names = list(getattr(tracker, "caps", {}) or {})
    rows: dict[str, dict[str, Any]] = {}
    for name in names:
        key = str(name)
        try:
            rows[key] = dict(tracker.headroom(key, reserve_pct=reserve))
        except Exception as exc:  # noqa: BLE001 - one bad model must not break
            rows[key] = {
                "model": key,
                "ok": False,
                "unknown": True,
                "reason": f"{type(exc).__name__}: {exc}"[:200],
            }
    blocked = sorted(name for name, row in rows.items() if not row.get("ok"))
    worst = max(
        (float(row.get("worst_pct") or 0.0) for row in rows.values()),
        default=0.0,
    )
    return {
        "available": bool(getattr(tracker, "available", bool(rows))),
        "ok": bool(rows) and not blocked,
        "blocked_models": blocked,
        "reserve_pct": reserve,
        "verifier_reserve_pct": float(
            getattr(gate, "verifier_reserve_pct", DEFAULT_VERIFIER_RESERVE_PCT)
        ),
        "headroom_pct": round(max(0.0, 100.0 - worst), 1),
        "models": rows,
    }
