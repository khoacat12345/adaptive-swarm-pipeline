#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 5 / F04 - CLI provider adapters.

This is the only provider file allowed to know about a CLI binary.  The default
runners are imported lazily so importing the provider module does not require an
installed executable.

Three adapters share the ``complete()`` boundary:

* ``OpenCodeAdapter``   - ``opencode run --model ...`` (kept for compatibility);
* ``ClaudeCodeAdapter`` - ``claude -p ... --model ...``;
* ``GenericCliAdapter`` - any argv template using ``{prompt}``/``{model}``.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from .base_adapter import AdapterCapabilities, AgentReply, LLMAdapter


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


def _reply(text: str, *, model: str, usage: dict | None = None,
           raw: str | None = None) -> AgentReply:
    text = str(text or "")
    data = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            data = parsed
    except (TypeError, json.JSONDecodeError):
        pass
    return AgentReply(text=text, data=data, model=model,
                      usage=dict(usage or {}), raw=raw if raw is not None else text)


def _which(executable: str) -> str | None:
    return shutil.which(executable) if executable else None


class OpenCodeAdapter(LLMAdapter):
    """Wrap the existing `run_opencode_cli` call shape."""

    name = "opencode"
    default_capabilities = AdapterCapabilities(
        headless=True, tools=True, reasoning=True, json_output=True,
        messaging=True, write_mode="workspace", max_parallel=2,
    )

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

    def resolved_binary(self) -> str:
        if not self.binary:
            try:
                import workers
                self.binary = workers.resolve_binary(None)
            except Exception:
                self.binary = "opencode"
        return str(self.binary)

    def complete(self, messages: list[dict], schema: dict | None = None) -> AgentReply:
        self.calls += 1
        runner = self.runner or self._default_runner()
        binary = self.resolved_binary()
        prompt = _prompt(messages, schema)
        result = runner(binary, self.model, prompt, agent=self.agent,
                        cwd=self.cwd, timeout=self.timeout)
        if isinstance(result, tuple):
            text, usage = result[0], (result[1] if len(result) > 1 else {})
        else:
            text, usage = result, {}
        return _reply(str(text or ""), model=self.model, usage=usage)

    def health(self) -> dict:
        binary = self.resolved_binary()
        found = _which(binary)
        return {
            "ok": bool(found) or self.runner is not None,
            "name": self.name,
            "executable": found or binary,
            "degraded": not bool(found),
            "model": self.model,
        }


class GenericCliAdapter(LLMAdapter):
    """Adapter for any CLI that accepts a prompt through an argv template.

    The runner contract is ``runner(argv, cwd=..., timeout=...)`` returning
    either ``{"returncode", "stdout", "stderr"}`` or a plain string.  Tests can
    inject a callable and never spawn a process.
    """

    name = "generic-cli"
    default_capabilities = AdapterCapabilities(
        headless=True, tools=True, reasoning=True, json_output=False,
        messaging=True, write_mode="workspace",
    )

    def __init__(self, command=None, *, model: str = "", cwd=None,
                 timeout: int = 300, runner=None) -> None:
        self.command = [str(item) for item in (command or [])]
        self.model = model
        self.cwd = cwd
        self.timeout = int(timeout)
        self.runner = runner
        self.calls = 0

    def build_command(self, prompt: str, *, model: str = "",
                      cwd=None) -> list[str]:
        values = {
            "prompt": str(prompt or ""),
            "model": str(model or ""),
            "cwd": str(cwd or self.cwd or ""),
        }
        argv: list[str] = []
        has_prompt = False
        for part in self.command:
            text = str(part)
            if "{prompt}" in text:
                has_prompt = True
            for key, value in values.items():
                text = text.replace("{" + key + "}", value)
            argv.append(text)
        if argv and not has_prompt:
            argv.append(values["prompt"])
        return argv

    def _default_runner(self, argv: list[str], *, cwd, timeout: int) -> dict:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
        }

    def complete(self, messages: list[dict], schema: dict | None = None) -> AgentReply:
        self.calls += 1
        prompt = _prompt(messages, schema)
        argv = self.build_command(prompt, model=self.model, cwd=self.cwd)
        if not argv:
            return AgentReply(text="", data=None, model=self.model, usage={},
                              raw="")
        runner = self.runner or self._default_runner
        result = runner(argv, cwd=self.cwd, timeout=self.timeout)
        if isinstance(result, dict):
            returncode = int(result.get("returncode", 0) or 0)
            stdout = str(result.get("stdout") or "")
            stderr = str(result.get("stderr") or "")
            text = stdout if returncode == 0 else (stderr or stdout)
            return _reply(text, model=self.model,
                          usage={"returncode": returncode}, raw=text)
        return _reply(str(result or ""), model=self.model)

    def health(self) -> dict:
        executable = self.command[0] if self.command else ""
        found = _which(executable) if executable else None
        ok = bool(self.runner is not None) or bool(found)
        return {
            "ok": ok,
            "name": self.name,
            "executable": found or executable or None,
            "degraded": bool(executable) and not bool(found)
                        and self.runner is None,
            "model": self.model,
        }


class ClaudeCodeAdapter(GenericCliAdapter):
    """Claude Code print-mode adapter (``claude -p ... --model ...``)."""

    name = "claude-code"
    default_capabilities = AdapterCapabilities(
        headless=True, tools=True, reasoning=True, json_output=False,
        messaging=False, write_mode="workspace",
    )

    def __init__(self, executable: str = "claude", *, model: str = "",
                 cwd=None, timeout: int = 300, runner=None) -> None:
        super().__init__(
            [executable, "-p", "{prompt}", "--model", "{model}"],
            model=model, cwd=cwd, timeout=timeout, runner=runner,
        )
