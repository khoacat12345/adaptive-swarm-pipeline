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

__all__ = [
    "AgentMessage",
    "Blackboard",
    "Claim",
    "Decision",
    "Dissent",
    "EvidenceRef",
    "Objection",
    "ProtocolError",
    "Vote",
]
