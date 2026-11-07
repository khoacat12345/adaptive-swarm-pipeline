#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-14: one adaptive capability bundle for every execution tier.

E1–E4 all receive the same read-only context primitives:

* aider-style repo map
* SWE-agent ACI window
* mem0 failure lessons
* letta-style bounded memory
* OpenHands-style event page
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .aci_context import ACIContext
from .context_runtime import ConstrainedContext
from .event_runtime import EventStore
from .lesson_runtime import FailureLessonRuntime
from .repo_context import build_repo_context
from tier_policy import TIER_ORDER


@dataclass
class CapabilityBundle:
    tier: str
    text: str
    components: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "text": self.text,
            "components": dict(self.components),
            "errors": list(self.errors),
        }


def build_context(
    root: str | Path,
    worktree: str | Path,
    task: str,
    tier: str,
    *,
    target_files: Iterable[str] = (),
    max_chars: int = 8000,
) -> CapabilityBundle:
    tier = str(tier or "E2").upper()
    if tier not in TIER_ORDER:
        tier = "E2"
    components: dict[str, str] = {}
    errors: list[str] = []
    targets = [str(item) for item in target_files if str(item)]

    try:
        repo = build_repo_context(worktree, task, mode="on", max_tokens=900)
        if repo.text:
            components["repo_map"] = (
                "## TASK-AWARE REPO MAP (untrusted)\n" + repo.text
            )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"repo_map: {exc}")

    try:
        if targets:
            observation = ACIContext(worktree).open(targets[0], window=24)
            text = getattr(observation, "output", "") or observation.compact()
            if text:
                components["aci"] = (
                    "## ACI WINDOW (read-only; untrusted)\n" + text[:3000]
                )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"aci: {exc}")

    try:
        lessons = FailureLessonRuntime.for_root(root)
        hints = lessons.prompt_block(files=targets, text=task, limit=3)
        if hints:
            components["lessons"] = hints
    except Exception as exc:  # noqa: BLE001
        errors.append(f"lessons: {exc}")

    try:
        events_path = Path(root).resolve() / ".agents" / "tmp" / "v2" / "events.ndjson"
        page = EventStore(events_path).read(limit=5)
        if page.items:
            lines = [
                f"- {item.event_type} {item.status}" for item in page.items
            ]
            components["events"] = "## RECENT EVENTS (paged)\n" + "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"events: {exc}")

    # Bound the total context with the letta-style memory constraint.
    try:
        memory = ConstrainedContext(
            max_file_characters=max(512, max_chars),
            max_core_characters=max(1024, max_chars),
        )
        for name, text in components.items():
            memory.add(name, text, tier="working")
        packed = memory.pack()
        text = packed.text
    except Exception as exc:  # noqa: BLE001
        errors.append(f"memory: {exc}")
        text = "\n\n".join(components.values())

    try:
        import secret_scan  # type: ignore

        text, _findings = secret_scan.scan_text(text, filename="capability-context")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"secret_scan: {exc}")
    return CapabilityBundle(
        tier=tier,
        text=text[:max_chars],
        components=components,
        errors=errors,
    )
