#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2 environment preflight: Git, worktrees, OpenCode models and Spec Kit."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import model_registry


@dataclass
class Check:
    name: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _run(command: list[str], cwd: str | Path, timeout: int = 20) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return result.returncode, ((result.stdout or "") + (result.stderr or "")).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def _check_git(root: Path) -> list[Check]:
    exe = shutil.which("git")
    if not exe:
        return [Check("git", "bad", "git executable not found")]

    code, version = _run(["git", "--version"], root)
    rows = [Check("git", "ok" if code == 0 else "bad", version or "unknown")]
    code, top = _run(["git", "rev-parse", "--show-toplevel"], root)
    if code != 0:
        rows.append(Check("worktree", "bad", f"not a Git repository: {root}"))
        return rows
    rows.append(Check("worktree", "ok", top))
    code, status = _run(["git", "status", "--porcelain"], root)
    dirty = [line for line in status.splitlines() if line.strip()]
    rows.append(Check(
        "working tree",
        "ok" if code == 0 and not dirty else "warn",
        "clean" if not dirty else f"{len(dirty)} changed path(s); prepare requires a clean tree",
    ))
    return rows


def _check_models(root: Path) -> list[Check]:
    models = model_registry.load(root)
    if not models:
        return [Check("models", "bad", "no model metadata (config or fallback)")]
    free = [item for item in models.values() if item.is_free]
    families = sorted({item.family for item in free})
    required = [name for name in ("deepseek", "glm", "nemotron") if name not in families]
    status = "ok" if not required else "warn"
    detail = f"{len(free)} free model(s): {', '.join(families) or 'none'}"
    if required:
        detail += f"; missing free family: {', '.join(required)}"
    rows = [Check("free models", status, detail)]
    for model_id in (
        "opencode/deepseek-v4.1-flash-free",
        "opencode/glm-5.3-flash-free",
        "opencode/nemotron-3.5-lightning-free",
    ):
        item = models.get(model_id)
        if item is None:
            rows.append(Check(model_id, "warn", "not present in registry; fallback may apply"))
        else:
            rows.append(Check(
                model_id,
                "ok",
                f"{item.family}, free={item.is_free}, auth={item.auth_status}, "
                f"tools={item.supports_tools}",
            ))
    return rows


def _check_opencode(root: Path) -> list[Check]:
    # On Windows npm installs an extensionless shell shim plus .cmd/.ps1;
    # CreateProcess cannot execute the extensionless shim or the .ps1 file.
    exe = (
        shutil.which("opencode.exe")
        or shutil.which("opencode.cmd")
        or shutil.which("opencode")
    )
    if not exe:
        return [Check("opencode CLI", "warn", "opencode not on PATH; pipeline can still run via API")]
    code, version = _run([exe, "--version"], root)
    return [Check("opencode CLI", "ok" if code == 0 else "warn", version or exe)]


def _check_speckit(root: Path) -> list[Check]:
    spec_dir = root / ".specify"
    if spec_dir.is_dir():
        file_count = sum(1 for _ in spec_dir.rglob("*") if _.is_file())
        return [Check("spec kit config", "ok", f"{spec_dir} ({file_count} files)")]
    exe = shutil.which("specify") or shutil.which("spec-kit")
    if exe:
        return [Check("spec kit CLI", "ok", exe)]
    return [Check("spec kit config", "warn", "no .specify/ or specify CLI detected; bridge still supports tasks.yaml")]


def collect(root: str | Path = ".") -> list[Check]:
    path = Path(root).expanduser().resolve()
    rows: list[Check] = []
    rows.extend(_check_git(path))
    rows.extend(_check_opencode(path))
    rows.extend(_check_models(path))
    rows.extend(_check_speckit(path))
    return rows


def as_dict(rows: list[Check]) -> dict[str, Any]:
    return {
        "ok": all(row.ok or row.status == "warn" for row in rows),
        "checks": [asdict(row) for row in rows],
        "bad": sum(1 for row in rows if row.status == "bad"),
        "warn": sum(1 for row in rows if row.status == "warn"),
    }


def format_table(rows: list[Check], *, color: bool = True) -> str:
    use_color = bool(color and sys.stdout.isatty())
    green, red, yellow, dim, reset = (
        "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
    ) if use_color else ("", "", "", "", "")
    lines = ["v2 doctor", "", f"{'STATUS':<7} {'CHECK':<44} DETAIL"]
    lines.append("-" * 100)
    for row in rows:
        mark = {"ok": f"{green}OK{reset}", "bad": f"{red}FAIL{reset}",
                "warn": f"{yellow}WARN{reset}"}.get(row.status, row.status.upper())
        lines.append(f"{mark:<7} {row.name[:44]:<44} {row.detail}")
    bad = sum(1 for row in rows if row.status == "bad")
    warn = sum(1 for row in rows if row.status == "warn")
    lines.append("-" * 100)
    lines.append(f"{dim}{len(rows)} check(s), {bad} failure(s), {warn} warning(s){reset}")
    return "\n".join(lines)


def main(root: str | Path = ".", *, json_output: bool = False, color: bool = True) -> int:
    rows = collect(root)
    if json_output:
        print(json.dumps(as_dict(rows), ensure_ascii=False, indent=2))
    else:
        print(format_table(rows, color=color))
    return 0 if not any(row.status == "bad" for row in rows) else 2
