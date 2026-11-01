#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared v2.1 errors.

Keep error codes stable: the runner and dashboard use them as machine-readable
stop reasons.
"""

from __future__ import annotations


class V2Error(Exception):
    """Base class for all v2.1 failures."""

    code = "V2_ERROR"

    def __init__(self, message: str, *, detail: str | None = None):
        self.message = str(message)
        self.detail = detail
        super().__init__(self.message)

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class IsolationError(V2Error):
    code = "ISOLATION_ERROR"


class IsolationGuardFailed(IsolationError):
    code = "ISOLATION_GUARD_FAILED"


class CleanTreeRequired(IsolationError):
    code = "CLEAN_TREE_REQUIRED"


class WorktreeCreateFailed(IsolationError):
    code = "WORKTREE_CREATE_FAILED"


class WorktreeRemoveFailed(IsolationError):
    code = "WORKTREE_REMOVE_FAILED"


class BudgetError(V2Error):
    code = "BUDGET_ERROR"


class BudgetExceeded(BudgetError):
    code = "BUDGET_EXHAUSTED"


class BudgetBlocked(BudgetError):
    code = "BUDGET_BLOCKED"


class BudgetOverrun(BudgetError):
    code = "BUDGET_OVERRUN"


class StateTransitionError(V2Error):
    code = "STATE_TRANSITION_FAILED"


class ManifestError(V2Error):
    code = "MANIFEST_ERROR"


class PlanChangedError(V2Error):
    code = "PLAN_MISMATCH"
