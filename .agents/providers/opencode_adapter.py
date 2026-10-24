#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 5 - OpenCode CLI adapter.

This is the only provider file allowed to know about the CLI.  The default
runner is imported lazily so importing the provider module does not require an
installed binary.
"""

from __future__ import annotations

import json

from .base_adapter import AgentReply, LLMAdapter


def _prompt(messages: list[dict], schema: dict | None = None) -> str:
    items = list(messages or [])
    if len(items) == 1:
        text = str(items[0].get("content") or "")
    else:
        text = "\n".join(
            f"{item.get('role', 'user')}: {item.get('content', '')}"
            for item in items
        )
    if schema:
        text += ("\n\nChi tra ve mot object JSON hop le, khong markdown, "
                 "khong giai thich, dung schema da cho.")
    return text


class OpenCodeAdapter(LLMAdapter):
    """Wrap the existing `run_opencode_cli` call shape."""

    name = "opencode"

    def __init__(self, binary: str | None = None, *, model: str = "",
                 agent: str | None = None, cwd=None, timeout: int = 300,
                 runner=None) -> None:
        self.binary = binary
        self.model = model
        self.agent = agent
        self.cwd = cwd
        self.timeout = int(timeout)
        self.runner = runner
        self.calls = 0

    def _default_runner(self):
        import workers
        return workers.run_opencode_cli

    def complete(self, messages: list[dict], schema: dict | None = None) -> AgentReply:
        self.calls += 1
        runner = self.runner or self._default_runner()
        if self.binary is None:
            try:
                import workers
                self.binary = workers.resolve_binary(None)
            except Exception:
                self.binary = "opencode"
        prompt = _prompt(messages, schema)
        result = runner(self.binary, self.model, prompt, agent=self.agent,
                        cwd=self.cwd, timeout=self.timeout)
        if isinstance(result, tuple):
            text, usage = result[0], (result[1] if len(result) > 1 else {})
        else:
            text, usage = result, {}
        text = str(text or "")
        data = None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                data = parsed
        except (TypeError, json.JSONDecodeError):
            pass
        return AgentReply(text=text, data=data, model=self.model,
                          usage=dict(usage or {}), raw=text)
