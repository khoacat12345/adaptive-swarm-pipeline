#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 5 - deterministic adapter for fast, isolated tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable

from .base_adapter import AgentReply, LLMAdapter


class MockAdapter(LLMAdapter):
    """Return scripted replies without spawning a process or touching a CLI."""

    name = "mock"

    def __init__(self, responses: Iterable | Callable | None = None,
                 *, model: str = "mock-model") -> None:
        self.responses = list(responses) if responses is not None and not callable(responses) else []
        self.responder = responses if callable(responses) else None
        self.model = model
        self.calls = 0
        self.history: list[dict] = []
        self._index = 0

    def reset(self) -> None:
        self.calls = 0
        self.history = []
        self._index = 0

    def complete(self, messages: list[dict], schema: dict | None = None) -> AgentReply:
        self.calls += 1
        self.history.append({"messages": list(messages or []), "schema": schema})
        if self.responder is not None:
            reply = self.responder(messages, schema)
        elif self.responses:
            reply = self.responses[min(self._index, len(self.responses) - 1)]
            self._index += 1
        else:
            reply = ""
        return self._coerce(reply)

    def _coerce(self, reply) -> AgentReply:
        if isinstance(reply, AgentReply):
            if not reply.model:
                reply.model = self.model
            return reply
        if isinstance(reply, dict):
            data = reply if isinstance(reply, dict) else None
            text = str(reply.get("text") or reply.get("content") or "")
            if not text:
                text = json.dumps(reply, ensure_ascii=False)
            return AgentReply(text=text, data=data, model=self.model,
                              usage=dict(reply.get("usage") or {}), raw=text)
        text = str(reply or "")
        data = None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                data = parsed
        except (TypeError, json.JSONDecodeError):
            pass
        return AgentReply(text=text, data=data, model=self.model, raw=text)
