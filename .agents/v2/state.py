#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run manifest and state machine for Adaptive Swarm Pipeline v2.1.

M1 deliberately keeps this module free of model calls. The state machine is the
single source of truth for whether a run may write, verify, checkpoint, or stop.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ManifestError, StateTransitionError

SCHEMA_VERSION = "2.1"
STATE_ROOT_REL = Path(".agents") / "tmp" / "v2"

# States that may still make progress or be resumed.
ACTIVE_STATES = {
    "CREATED",
    "PRECHECKED",
    "PLANNING",
    "AWAITING_SIGNOFF",
    "PROVISIONING",
    "PHASE_EXECUTING",
    "PHASE_VERIFYING",
    "PHASE_COMMITTED",
    "HANDOFF_READY",
    "PAUSED",
    "BLOCKED",
    "RATE_LIMITED",
    "AWAITING_USER_CONFIRMATION",
    "USER_CONFIRMED",
    "USER_REJECTED",
}

# Normal transitions. The event name is stored in the manifest/event log.
TRANSITIONS: dict[tuple[str, str], str] = {
    ("CREATED", "preflight_ok"): "PRECHECKED",
    ("PRECHECKED", "start_planning"): "PLANNING",
    ("PLANNING", "plan_valid"): "AWAITING_SIGNOFF",
    ("PLANNING", "plan_changed"): "AWAITING_SIGNOFF",
    ("PROVISIONING", "plan_changed"): "AWAITING_SIGNOFF",
    ("AWAITING_SIGNOFF", "sign_off"): "PROVISIONING",
    ("PROVISIONING", "worktree_ready"): "PHASE_EXECUTING",
    ("PHASE_EXECUTING", "verify_start"): "PHASE_VERIFYING",
    ("PHASE_VERIFYING", "verify_pass"): "PHASE_COMMITTED",
    ("PHASE_VERIFYING", "repair"): "PHASE_EXECUTING",
    ("PHASE_VERIFYING", "verify_fail_exhausted"): "QUARANTINED",
    ("PHASE_COMMITTED", "next_phase"): "PHASE_EXECUTING",
    ("PHASE_COMMITTED", "all_phases_done"): "HANDOFF_READY",
    ("HANDOFF_READY", "user_accepts"): "DONE",
    ("AWAITING_USER_CONFIRMATION", "user_confirms"): "USER_CONFIRMED",
    ("AWAITING_USER_CONFIRMATION", "user_rejects"): "USER_REJECTED",
}

# Any active state may enter these safety states.
ANY_ACTIVE_TRANSITIONS: dict[str, str] = {
    "pause": "PAUSED",
    "stop": "STOPPED",
    "block": "BLOCKED",
    "quarantine": "QUARANTINED",
    "fail": "FAILED",
    "rate_limit": "RATE_LIMITED",
    "request_confirmation": "AWAITING_USER_CONFIRMATION",
}

TERMINAL_STATES = {"DONE", "STOPPED", "FAILED", "QUARANTINED"}


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"v2_{stamp}_{uuid.uuid4().hex[:6]}"


def resolve_state_root(root: str | Path, override: str | Path | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    return (Path(root).expanduser().resolve() / STATE_ROOT_REL).resolve()


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def read_json(path: str | Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"manifest not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc


class RunStore:
    """Persist one run manifest and its append-only event stream."""

    _lock = threading.RLock()

    def __init__(self, root: str | Path, run_id: str, *, state_root: str | Path | None = None):
        self.root = Path(root).expanduser().resolve()
        self.run_id = str(run_id)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.run_id):
            raise ManifestError(f"unsafe run_id: {self.run_id!r}")
        self.state_root = resolve_state_root(self.root, state_root)
        self.run_dir = (self.state_root / "runs" / self.run_id).resolve()
        self.manifest_path = self.run_dir / "run_manifest.json"
        self.events_path = self.run_dir / "events.ndjson"
        self.budget_path = self.run_dir / "budget.json"
        self.budget_events_path = self.run_dir / "budget_events.ndjson"
        self.checkpoints_path = self.run_dir / "checkpoints.json"
        self.quarantine_dir = self.run_dir / "quarantine"
        self.handoff_dir = self.run_dir / "handoff"

    # ------------------------------------------------------------------
    # Creation and persistence
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        root: str | Path,
        *,
        goal: str,
        base_sha: str,
        run_id: str | None = None,
        state_root: str | Path | None = None,
        workspace: dict[str, Any] | None = None,
    ) -> "RunStore":
        store = cls(root, run_id or new_run_id(), state_root=state_root)
        if store.manifest_path.exists():
            raise ManifestError(f"run already exists: {store.run_id}")
        now = utc_now_iso()
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": store.run_id,
            "goal": str(goal),
            "status": "CREATED",
            "created_at": now,
            "updated_at": now,
            "base_sha": str(base_sha),
            "plan_hash": None,
            "budget_hash": None,
            "model_snapshot_hash": None,
            "workspace": workspace or {},
            "current_phase": None,
            "checkpoints": [],
            "last_error": None,
            "stop_reason": None,
            "resume_state": None,
        }
        store.run_dir.mkdir(parents=True, exist_ok=True)
        store.save(manifest)
        store._append_event("run.created", {"goal": str(goal), "base_sha": str(base_sha)})
        return store

    def load(self) -> dict[str, Any]:
        with self._lock:
            manifest = read_json(self.manifest_path)
        if manifest.get("run_id") != self.run_id:
            raise ManifestError(
                f"manifest run_id mismatch: {manifest.get('run_id')} != {self.run_id}"
            )
        return manifest

    def save(self, manifest: dict[str, Any]) -> None:
        manifest["updated_at"] = utc_now_iso()
        atomic_write_json(self.manifest_path, manifest)

    def update(self, **fields: Any) -> dict[str, Any]:
        with self._lock:
            manifest = self.load()
            manifest.update(fields)
            self.save(manifest)
            return manifest

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def transition(self, event: str, **details: Any) -> dict[str, Any]:
        """Apply one legal transition and persist it before returning."""
        event = str(event)
        with self._lock:
            manifest = self.load()
            current = str(manifest.get("status") or "")
            if current in TERMINAL_STATES:
                raise StateTransitionError(
                    f"cannot {event!r} from terminal state {current!r}"
                )

            next_state = TRANSITIONS.get((current, event))
            if next_state is None and event in ANY_ACTIVE_TRANSITIONS:
                next_state = ANY_ACTIVE_TRANSITIONS[event]

            if event == "resume":
                resume_state = manifest.get("resume_state")
                if current in {
                    "PAUSED", "BLOCKED", "RATE_LIMITED",
                    "AWAITING_USER_CONFIRMATION", "USER_CONFIRMED",
                } and resume_state in ACTIVE_STATES:
                    next_state = str(resume_state)
                else:
                    raise StateTransitionError(
                        f"cannot resume from {current!r} without a valid resume_state"
                    )

            if next_state is None:
                raise StateTransitionError(
                    f"illegal transition: {current!r} --{event}--> ?"
                )

            if event in {"pause", "block", "rate_limit", "request_confirmation"} and current in ACTIVE_STATES:
                manifest["resume_state"] = current
            if event == "resume":
                manifest["resume_state"] = None
            if event in {"stop", "fail", "quarantine", "block"}:
                manifest["stop_reason"] = details.get("reason") or event

            manifest["status"] = next_state
            for key, value in details.items():
                if key != "reason":
                    manifest[key] = value
            self.save(manifest)
            self._append_event(
                "run.transition",
                {
                    "event": event,
                    "from": current,
                    "to": next_state,
                    **{k: v for k, v in details.items() if k != "reason"},
                    "reason": details.get("reason"),
                },
            )
            return manifest

    def assert_state(self, *allowed: str) -> dict[str, Any]:
        manifest = self.load()
        if manifest.get("status") not in set(allowed):
            raise StateTransitionError(
                f"expected state in {sorted(allowed)}, got {manifest.get('status')!r}"
            )
        return manifest

    # ------------------------------------------------------------------
    # Checkpoints and events
    # ------------------------------------------------------------------

    def record_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            manifest = self.load()
            checkpoints = [item for item in (manifest.get("checkpoints") or [])
                           if item.get("phase_id") != checkpoint.get("phase_id")]
            checkpoints.append(dict(checkpoint))
            manifest["checkpoints"] = checkpoints
            self.save(manifest)
            self._append_event("checkpoint.recorded", dict(checkpoint))
            return manifest

    def checkpoint_for(self, phase_id: str) -> dict[str, Any] | None:
        manifest = self.load()
        for item in manifest.get("checkpoints") or []:
            if item.get("phase_id") == phase_id:
                return item
        return None

    def append_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self._append_event(event_type, data or {})

    def _append_event(self, event_type: str, data: dict[str, Any]) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        event = {
            "schema_version": SCHEMA_VERSION,
            "timestamp": utc_now_iso(),
            "run_id": self.run_id,
            "event_type": str(event_type),
            "data": data,
        }
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    def read_events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def summary(self) -> dict[str, Any]:
        manifest = self.load()
        return {
            "run_id": manifest.get("run_id"),
            "goal": manifest.get("goal"),
            "status": manifest.get("status"),
            "base_sha": manifest.get("base_sha"),
            "current_phase": manifest.get("current_phase"),
            "plan_hash": manifest.get("plan_hash"),
            "budget_hash": manifest.get("budget_hash"),
            "workspace": manifest.get("workspace"),
            "checkpoints": manifest.get("checkpoints") or [],
            "last_error": manifest.get("last_error"),
            "stop_reason": manifest.get("stop_reason"),
            "run_dir": str(self.run_dir),
        }
