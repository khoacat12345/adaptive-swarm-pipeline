# -*- coding: utf-8 -*-
"""Unified engine contracts and orchestrator.

Phase 2 of the unified engine keeps one public data shape for the CLI, the War
Room and the execution engine. The contracts in this package are deliberately
dependency-light so a dashboard or an external client can import them without
pulling in the Git/worktree machinery.
"""

from .contracts import (  # noqa: F401
    PlanDecision,
    PreflightStatus,
    RunProgress,
    UnifiedRequest,
    UnifiedResult,
)

__all__ = [
    "PlanDecision",
    "PreflightStatus",
    "RunProgress",
    "UnifiedRequest",
    "UnifiedResult",
]
