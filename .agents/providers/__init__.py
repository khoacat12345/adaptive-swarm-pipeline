"""Provider adapters for the LLM boundary.

`core/` must never import OpenCode, subprocess or a CLI binary.  Providers are
the only place allowed to know how a concrete model is reached.
"""

from .base_adapter import AgentReply, LLMAdapter
from .mock_adapter import MockAdapter
from .opencode_adapter import OpenCodeAdapter
from .registry import ProviderRegistry

__all__ = ["AgentReply", "LLMAdapter", "MockAdapter", "OpenCodeAdapter",
           "ProviderRegistry"]
