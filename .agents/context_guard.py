#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-08 prompt/token length guard.

Estimates prompt size before dispatch and trims the middle when the model's
registered context window is exceeded. Unknown context windows are left alone
(fail-open for context, fail-closed for paid spend).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import model_registry


@dataclass(frozen=True)
class PromptFit:
    text: str
    original_tokens: int
    final_tokens: int
    context_window: int | None
    trimmed: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "original_tokens": self.original_tokens,
            "final_tokens": self.final_tokens,
            "context_window": self.context_window,
            "trimmed": self.trimmed,
            "reason": self.reason,
        }


def estimate_tokens(text: str) -> int:
    return max(1, (len(str(text or "")) + 3) // 4)


def fit_prompt(text: str, model_id: str, *, root: str | Path | None = None,
               reserve_tokens: int = 1024) -> PromptFit:
    text = str(text or "")
    original = estimate_tokens(text)
    model = model_registry.get(root or Path.cwd(), model_id) if root or Path.cwd() else None
    # When root is not supplied, model_registry still checks cwd/.agents.
    if model is None:
        model = model_registry.get(Path.cwd(), model_id)
    window = int(model.context_window) if model and model.context_window else None
    if not window:
        return PromptFit(text, original, original, None, False, "unknown context window")
    budget = max(256, window - max(0, int(reserve_tokens)))
    if original <= budget:
        return PromptFit(text, original, original, window, False, "within context budget")
    max_chars = max(512, budget * 4)
    if len(text) <= max_chars:
        return PromptFit(text, original, original, window, False, "within context budget")
    head = max_chars * 3 // 5
    tail = max_chars - head
    marker = "\n\n[... context trimmed to fit model window ...]\n\n"
    shortened = text[:head] + marker + text[-tail:]
    return PromptFit(
        shortened, original, estimate_tokens(shortened), window, True,
        f"trimmed to context window {window}",
    )
