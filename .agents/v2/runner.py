#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M1 runner shell: create a guarded run, record checkpoints, hand off patches.

Planning, sign-off and model execution are M2/M3. This module deliberately does
not call a model; it provides the isolation/state/budget foundation those
milestones will use.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import isolation
from .budget import BudgetConfig, BudgetController
from .errors import ManifestError, StateTransitionError
from .state import RunStore, resolve_state_root


class V2Runner:
    def __init__(
        self,
        root: str | Path,
        *,
        state_root: str | Path | None = None,
        worktree_root: str | Path | None = None,
        require_clean: bool = True,
    ):
        self.root = Path(root).expanduser().resolve()
        self.state_root = resolve_state_root(self.root, state_root)
        self.worktree_root = (
            Path(worktree_root).expanduser().resolve() if worktree_root else None
        )
        self.require_clean = bool(require_clean)

    def _store(self, run_id: str) -> RunStore:
        return RunStore(self.root, run_id, state_root=self.state_root)

    def prepare(
        self,
        goal: str,
        *,
        base_sha: str = "HEAD",
        run_id: str | None = None,
        phase_ids: list[str] | None = None,
        budget_config: BudgetConfig | dict[str, Any] | None = None,
        min_free_bytes: int = 512 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Create a manifest, a guarded worktree, and a persisted budget."""
        main = isolation.git_root(self.root)
        # Check the main tree before writing any run-state files into it.
        if self.require_clean:
            isolation.assert_clean(main)
        base = isolation.resolve_base_sha(main, base_sha)
        store = RunStore.create(
            main,
            goal=goal,
            base_sha=base,
            run_id=run_id,
            state_root=self.state_root,
        )
        try:
            workspace = isolation.create_worktree(
                main,
                store.run_id,
                worktree_root=self.worktree_root,
                base_sha=base,
                # The clean check already ran before the state root was created.
                require_clean=False,
                min_free_bytes=min_free_bytes,
            )
            store.update(workspace=workspace.as_dict())
            config = (
                budget_config
                if isinstance(budget_config, BudgetConfig)
                else BudgetConfig.from_dict(budget_config)
                if budget_config
                else BudgetConfig.default(phase_ids)
            )
            budget = BudgetController(store.run_dir, config)
            store.update(
                budget_hash=budget.config_hash,
                plan_hash=None,
                status="CREATED",
            )
            store.transition("preflight_ok")
            store.append_event(
                "run.prepared",
                {
                    "workspace": workspace.as_dict(),
                    "budget_hash": budget.config_hash,
                },
            )
            first_phase = next(iter(config.phases), None)
            return {
                "run_id": store.run_id,
                "state_root": str(self.state_root),
                "workspace": workspace.as_dict(),
                "budget": budget.summary(first_phase),
                "manifest": store.summary(),
            }
        except Exception as exc:
            try:
                store.update(last_error=f"{type(exc).__name__}: {exc}")
                store.append_event("run.prepare_failed", {"error": str(exc)})
            except Exception:
                pass
            raise

    def load(self, run_id: str) -> RunStore:
        store = self._store(run_id)
        if not store.manifest_path.exists():
            raise ManifestError(f"run not found: {run_id}")
        return store

    def workspace(self, run_id: str) -> isolation.Workspace:
        store = self.load(run_id)
        data = (store.load().get("workspace") or {})
        if not data:
            raise ManifestError(f"run has no workspace: {run_id}")
        return isolation.Workspace.from_dict(data)

    def checkpoint_phase(
        self,
        run_id: str,
        phase_id: str,
        *,
        verify_exit: int = 0,
    ) -> dict[str, Any]:
        store = self.load(run_id)
        manifest = store.load()
        if manifest.get("status") == "PHASE_EXECUTING":
            store.transition("verify_start")
        store.assert_state("PHASE_VERIFYING")
        workspace = self.workspace(run_id)
        checkpoint = isolation.checkpoint(
            workspace,
            phase_id,
            verify_exit=verify_exit,
            state_root=store.run_dir,
        )
        store.record_checkpoint(checkpoint)
        store.transition("verify_pass")
        store.update(current_phase=phase_id)
        store.append_event(
            "phase.checkpoint",
            {"phase_id": phase_id, "checkpoint": checkpoint},
        )
        return checkpoint

    def quarantine_phase(
        self,
        run_id: str,
        phase_id: str,
        *,
        reason: str,
        verify: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        store = self.load(run_id)
        manifest = store.load()
        if manifest.get("status") not in {"PHASE_VERIFYING", "PHASE_EXECUTING"}:
            raise StateTransitionError(
                f"quarantine requires an executing/verifying phase, got {manifest.get('status')}"
            )
        workspace = self.workspace(run_id)
        checkpoint = store.checkpoint_for(phase_id)
        last = checkpoint.get("sha") if checkpoint else workspace.base_sha
        evidence = isolation.quarantine_and_rollback(
            workspace,
            store.run_dir,
            phase_id,
            reason=reason,
            last_checkpoint=last,
            verify=verify or {},
        )
        store.append_event("phase.quarantined", evidence)
        store.transition("quarantine", reason=reason)
        store.update(last_error=reason)
        return evidence

    def handoff(self, run_id: str, *, report: str | None = None) -> dict[str, Any]:
        store = self.load(run_id)
        manifest = store.load()
        if manifest.get("status") == "PHASE_COMMITTED":
            store.transition("all_phases_done")
        store.assert_state("HANDOFF_READY")
        workspace = self.workspace(run_id)
        result = isolation.handoff(workspace, store.run_dir, report=report)
        store.append_event("run.handoff", result)
        return result

    def status(self, run_id: str) -> dict[str, Any]:
        store = self.load(run_id)
        summary = store.summary()
        budget_path = store.budget_path
        if budget_path.exists():
            try:
                summary["budget"] = json.loads(budget_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                summary["budget"] = None
        return summary
