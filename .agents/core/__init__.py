"""Core meeting data contracts.

This package is intentionally independent from OpenCode and from the
filesystem writer.  It only describes messages, decisions and the shared
blackboard used by the meeting mode.
"""

from .protocol import (
    AgentMessage,
    Claim,
    Decision,
    Dissent,
    EvidenceRef,
    Objection,
    ProtocolError,
    Vote,
)
from .blackboard import Blackboard
from .budget import BudgetExceeded, CircuitBreaker, MeetingBudget
from .policy import VetoPolicy

__all__ = [
    "AgentMessage",
    "Blackboard",
    "BudgetExceeded",
    "CircuitBreaker",
    "Claim",
    "Decision",
    "Dissent",
    "EvidenceRef",
    "Objection",
    "MeetingBudget",
    "ProtocolError",
    "VetoPolicy",
    "Vote",
]
