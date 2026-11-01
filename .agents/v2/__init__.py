# -*- coding: utf-8 -*-
"""Adaptive Swarm Pipeline v2.1 — M1 isolation, state and budget core.

This package is intentionally separate from the v1 modules. v1 remains the
default execution path until the v2.1 acceptance gates pass.
"""

from .errors import (  # noqa: F401
    BudgetBlocked,
    BudgetError,
    BudgetExceeded,
    BudgetOverrun,
    CleanTreeRequired,
    IsolationError,
    IsolationGuardFailed,
    StateTransitionError,
    V2Error,
)

__all__ = [
    "BudgetBlocked",
    "BudgetError",
    "BudgetExceeded",
    "BudgetOverrun",
    "CleanTreeRequired",
    "IsolationError",
    "IsolationGuardFailed",
    "StateTransitionError",
    "V2Error",
]
