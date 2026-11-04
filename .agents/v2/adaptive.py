#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adaptive toolkit for v2.1.

Lightweight, dependency-free mechanisms inspired by:
  - aider repo map
  - SWE-agent ACI
  - mem0 failure lessons
  - Letta tiered memory
  - OpenHands event stream
  - DSPy metric-driven proposals (offline only)
  - LiteLLM gateway metadata
  - Roo-Code role modes
  - AutoGen speaker selection
  - SWE-bench/agbenchmark evaluation

No framework is imported at runtime. These are local primitives with explicit
safety boundaries.
"""

from __future__ import annotations

import ast
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SKIP_DIRS = {
    ".git", ".agents/tmp", ".specify", "node_modules", "__pycache__",
    ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache",
}
SECRET_NAME_RE = re.compile(r"(secret|token|password|credential|private[_-]?key|\.env)", re.I)
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _tokens(text: str) -> set[str]:
    return {item.lower() for item in TOKEN_RE.findall(text or "") if len(item) > 2}


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _safe_relative(value: str | Path) -> bool:
    text = str(value or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return False
    return ".." not in Path(text).parts


# ---------------------------------------------------------------------------
# 1. Repo Map (aider-inspired)
# ---------------------------------------------------------------------------

@dataclass
class Symbol:
    path: str
    kind: str
    name: str
    line: int
    parent: str = ""
    imports: list[str] = field(default_factory=list)

    @property
    def qualified(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name


@dataclass
class RepoMap:
    text: str
    symbols: list[Symbol]
    files: int
    approx_tokens: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "files": self.files,
            "approx_tokens": self.approx_tokens,
            "symbols": [
                {"path": item.path, "kind": item.kind, "name": item.qualified,
                 "line": item.line}
                for item in self.symbols
            ],
        }


class RepoMapper:
    """Python-first AST symbol map; tree-sitter can replace the extractor later."""

    def __init__(self, root: str | Path, *, max_tokens: int = 1200):
        self.root = Path(root).resolve()
        self.max_tokens = max(200, int(max_tokens))

    def _skip(self, path: Path) -> bool:
        relative = path.relative_to(self.root).as_posix()
        return (
            any(relative == item or relative.startswith(item + "/") for item in SKIP_DIRS)
            or bool(SECRET_NAME_RE.search(path.name))
        )

    def _parse(self, path: Path) -> list[Symbol]:
        relative = path.relative_to(self.root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            return []
        symbols: list[Symbol] = []
        imports: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append(Symbol(
                    relative, "class" if isinstance(node, ast.ClassDef) else "function",
                    node.name, node.lineno, imports=list(imports),
                ))
                if isinstance(node, ast.ClassDef):
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            symbols.append(Symbol(
                                relative, "method", child.name, child.lineno,
                                parent=node.name, imports=list(imports),
                            ))
        return symbols

    def build(self, task: str = "") -> RepoMap:
        symbols: list[Symbol] = []
        files = 0
        for path in sorted(self.root.rglob("*.py")):
            if not path.is_file() or self._skip(path):
                continue
            files += 1
            symbols.extend(self._parse(path))
        terms = _tokens(task)
        def score(item: Symbol) -> tuple:
            haystack = f"{item.path} {item.parent} {item.name} {' '.join(item.imports)}".lower()
            hits = sum(1 for term in terms if term in haystack)
            exact = sum(2 for term in terms if term in item.name.lower())
            kind_bonus = 2 if item.kind == "class" else 1
            return (-(hits + exact + kind_bonus), item.path, item.line)
        ranked = sorted(symbols, key=score)
        lines = [f"# repo map ({files} Python files, task-aware)"]
        used = _approx_tokens(lines[0])
        kept: list[Symbol] = []
        current_path = ""
        for item in ranked:
            line = f"{item.path}:{item.line} {item.kind} {item.qualified}"
            cost = _approx_tokens(line)
            if used + cost > self.max_tokens:
                continue
            if item.path != current_path:
                lines.append(f"{item.path}:")
                current_path = item.path
                used += _approx_tokens(lines[-1])
            lines.append(f"  {item.line}: {item.kind} {item.qualified}")
            used += cost
            kept.append(item)
        return RepoMap("\n".join(lines), kept, files, used)


# ---------------------------------------------------------------------------
# 2. ACI (SWE-agent-inspired)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class Observation:
    action: str
    status: str
    output: str = ""
    exit_code: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False

    def compact(self, limit: int = 4000) -> str:
        text = self.output or ""
        if len(text) > limit:
            self.truncated = True
            text = text[:limit] + "\n...[truncated]"
        return json.dumps({
            "action": self.action, "status": self.status,
            "exit_code": self.exit_code, "truncated": self.truncated,
            "output": text, "metadata": self.metadata,
        }, ensure_ascii=False)


class ActionPolicy:
    """Allowlisted, root-bound actions; no arbitrary shell."""

    ALLOWED = {"list", "read", "search", "verify"}

    def __init__(self, root: str | Path, *, max_read_chars: int = 12000):
        self.root = Path(root).resolve()
        self.max_read_chars = max(1000, int(max_read_chars))

    def _path(self, relative: str) -> Path:
        if not _safe_relative(relative):
            raise ValueError(f"unsafe path: {relative!r}")
        if SECRET_NAME_RE.search(str(relative)):
            raise ValueError(f"sensitive path blocked: {relative!r}")
        path = (self.root / relative).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path escapes root: {relative!r}") from exc
        return path

    def execute(self, action: Action) -> Observation:
        if action.name not in self.ALLOWED:
            return Observation(action.name, "blocked", f"action not allowed: {action.name}")
        try:
            if action.name == "list":
                relative = str(action.args.get("path") or ".")
                path = self._path(relative)
                entries = sorted(
                    f"{item.name}/" if item.is_dir() else item.name
                    for item in path.iterdir()
                    if not SECRET_NAME_RE.search(item.name)
                )[:200]
                return Observation("list", "ok", "\n".join(entries))
            if action.name == "read":
                relative = str(action.args.get("path") or "")
                path = self._path(relative)
                if not path.is_file():
                    return Observation("read", "failed", f"not a file: {relative}")
                text = path.read_text(encoding="utf-8", errors="replace")
                truncated = len(text) > self.max_read_chars
                return Observation("read", "ok", text[:self.max_read_chars],
                                   truncated=truncated, metadata={"path": relative})
            if action.name == "search":
                query = str(action.args.get("query") or "").strip()
                if not query:
                    return Observation("search", "failed", "query is empty")
                pattern = re.compile(re.escape(query), re.I)
                matches: list[str] = []
                for path in self.root.rglob("*"):
                    if (not path.is_file() or self._skip_path(path)
                            or SECRET_NAME_RE.search(path.name)):
                        continue
                    try:
                        for number, line in enumerate(
                            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                        ):
                            if pattern.search(line):
                                matches.append(f"{path.relative_to(self.root).as_posix()}:{number}:{line[:200]}")
                                if len(matches) >= 100:
                                    break
                    except OSError:
                        continue
                    if len(matches) >= 100:
                        break
                return Observation("search", "ok", "\n".join(matches),
                                   metadata={"query": query, "matches": len(matches)})
            if action.name == "verify":
                command = str(action.args.get("command") or "").strip()
                if not command:
                    return Observation("verify", "failed", "command is empty")
                import system_bot  # type: ignore

                guard = system_bot.check_command(command)
                if not guard.get("allowed"):
                    return Observation("verify", "blocked", str(guard.get("reason") or "blocked"))
                timeout = int(action.args.get("timeout") or 120)
                result = system_bot.run_command(command, cwd=self.root, timeout=timeout)
                return Observation("verify", "ok" if result.get("exit_code") == 0 else "failed",
                                   str(result.get("output_tail") or ""),
                                   exit_code=result.get("exit_code"))
        except Exception as exc:  # noqa: BLE001
            return Observation(action.name, "failed", str(exc)[:500])
        return Observation(action.name, "blocked", "unhandled action")

    def _skip_path(self, path: Path) -> bool:
        relative = path.relative_to(self.root).as_posix()
        return any(relative == item or relative.startswith(item + "/") for item in SKIP_DIRS)


# ---------------------------------------------------------------------------
# 3. Failure lessons (mem0-inspired)
# ---------------------------------------------------------------------------

@dataclass
class Lesson:
    signature: str
    cause: str
    fix: str
    files: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_run: str = ""
    created_at: str = field(default_factory=_now)


class LessonStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lessons: list[Lesson] = []
        self.load()

    def load(self) -> "LessonStore":
        if not self.path.exists():
            return self
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                self.lessons.append(Lesson(
                    signature=str(item.get("signature") or ""),
                    cause=str(item.get("cause") or ""),
                    fix=str(item.get("fix") or ""),
                    files=[str(x) for x in item.get("files") or []],
                    tags=[str(x) for x in item.get("tags") or []],
                    source_run=str(item.get("source_run") or ""),
                    created_at=str(item.get("created_at") or _now()),
                ))
            except json.JSONDecodeError:
                continue
        return self

    def record(self, lesson: Lesson) -> Lesson:
        if not lesson.signature or not lesson.fix:
            raise ValueError("lesson needs signature and fix")
        self.lessons.append(lesson)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(lesson.__dict__, ensure_ascii=False) + "\n")
        return lesson

    def query(self, *, files: Iterable[str] = (), text: str = "", limit: int = 3) -> list[Lesson]:
        file_set = {str(item).replace("\\", "/") for item in files}
        terms = _tokens(text)
        scored: list[tuple[int, Lesson]] = []
        for lesson in self.lessons:
            score = len(file_set.intersection(lesson.files)) * 3
            haystack = f"{lesson.signature} {lesson.cause} {lesson.fix} {' '.join(lesson.tags)}".lower()
            score += sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, lesson))
        scored.sort(key=lambda item: (-item[0], item[1].created_at))
        return [item for _, item in scored[:limit]]

    def hints(self, *, files: Iterable[str] = (), text: str = "", limit: int = 3) -> str:
        lessons = self.query(files=files, text=text, limit=limit)
        if not lessons:
            return ""
        return "\n".join(f"- {item.signature}: {item.fix}" for item in lessons)


# ---------------------------------------------------------------------------
# 4. Tiered context (Letta-inspired)
# ---------------------------------------------------------------------------

@dataclass
class ContextPacker:
    token_budget: int = 3000
    working: list[str] = field(default_factory=list)
    recall: list[str] = field(default_factory=list)
    archival: list[str] = field(default_factory=list)

    def add(self, text: str, *, tier: str = "working", label: str = "") -> "ContextPacker":
        item = f"[{label}] {text}" if label else text
        if tier not in {"working", "recall", "archival"}:
            raise ValueError(f"unknown tier: {tier}")
        getattr(self, tier).append(item)
        return self

    def pack(self) -> dict[str, Any]:
        sections: list[str] = []
        used = 0
        included: list[dict[str, Any]] = []
        for tier in ("working", "recall", "archival"):
            for item in getattr(self, tier):
                cost = _approx_tokens(item)
                if used + cost > self.token_budget:
                    continue
                sections.append(f"## {tier}\n{item}")
                used += cost
                included.append({"tier": tier, "approx_tokens": cost})
        return {"text": "\n\n".join(sections), "approx_tokens": used, "items": included}


# ---------------------------------------------------------------------------
# 5. Event schema helper (OpenHands-inspired)
# ---------------------------------------------------------------------------

def adaptive_event(
    *,
    run_id: str,
    event_type: str,
    phase_id: str = "",
    action: str = "",
    observation: str = "",
    status: str = "info",
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    allowed = {"action", "observation", "status", "checkpoint", "handoff"}
    if event_type not in allowed:
        raise ValueError(f"unknown adaptive event type: {event_type}")
    return {
        "schema_version": "2.1-adaptive",
        "timestamp": _now(),
        "run_id": str(run_id),
        "phase_id": str(phase_id),
        "event_type": event_type,
        "action": str(action),
        "observation": str(observation)[:4000],
        "status": str(status),
        "data": dict(data or {}),
    }


# ---------------------------------------------------------------------------
# 6. Offline optimizer (DSPy-inspired, no DSPy dependency)
# ---------------------------------------------------------------------------

@dataclass
class OptimizationProposal:
    role: str
    hint: str
    source_signatures: list[str]
    baseline_note: str = "offline candidate; requires benchmark comparison"


class PromptOptimizer:
    def __init__(self, lessons: LessonStore):
        self.lessons = lessons

    def propose(self, *, role: str, files: Iterable[str] = (), text: str = "") -> OptimizationProposal:
        relevant = self.lessons.query(files=files, text=text, limit=5)
        if not relevant:
            return OptimizationProposal(role, "", [], "no failure lessons available")
        hints = []
        seen = set()
        for item in relevant:
            if item.signature in seen:
                continue
            seen.add(item.signature)
            hints.append(f"Avoid {item.signature}: {item.fix}")
        return OptimizationProposal(
            role=role,
            hint="\n".join(f"- {line}" for line in hints[:4]),
            source_signatures=[item.signature for item in relevant],
        )


# ---------------------------------------------------------------------------
# 7. Gateway metadata (LiteLLM-inspired)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GatewayModel:
    model: str
    family: str
    free: bool = False
    healthy: bool = True
    estimated_cost: float = 0.0


def select_gateway_model(
    candidates: Iterable[GatewayModel],
    *,
    exclude_families: Iterable[str] = (),
    allow_paid: bool = False,
) -> GatewayModel:
    excluded = {str(item) for item in exclude_families}
    usable = [
        item for item in candidates
        if item.healthy and item.family not in excluded and (allow_paid or item.free)
    ]
    if not usable:
        raise ValueError("no healthy gateway model matches the policy")
    usable.sort(key=lambda item: (not item.free, item.estimated_cost, item.model))
    return usable[0]


# ---------------------------------------------------------------------------
# 8. Role modes / presets (Roo-Code-inspired)
# ---------------------------------------------------------------------------

MODE_PRESETS: dict[str, dict[str, Any]] = {
    "free-only": {"allow_paid": False, "max_cost_usd": 0.0, "security_veto": True},
    "strict-security": {"allow_paid": False, "max_cost_usd": 0.0, "security_veto": True,
                         "network_for_verify": False, "test_integrity": True},
    "nightly": {"allow_paid": False, "security_veto": True, "test_integrity": True,
                "auto_merge": False, "max_phases": 6},
}


def apply_mode(name: str, base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    preset = MODE_PRESETS.get(str(name))
    if preset is None:
        raise ValueError(f"unknown mode: {name}")
    result = dict(base or {})
    result.update(preset)
    result["mode"] = str(name)
    return result


# ---------------------------------------------------------------------------
# 9. Speaker selection (AutoGen-inspired)
# ---------------------------------------------------------------------------

def select_speaker(
    *,
    findings: Iterable[Mapping[str, Any]],
    available: Iterable[str],
    fallback: str = "qa",
) -> str:
    roles = [str(item) for item in available]
    for finding in findings:
        if str(finding.get("severity") or "").lower() == "blocker":
            role = str(finding.get("role") or "")
            if role in roles:
                return role
    for finding in findings:
        if str(finding.get("verdict") or "").lower() == "reject":
            role = str(finding.get("role") or "")
            if role in roles:
                return role
    return fallback if fallback in roles else (roles[0] if roles else fallback)


# ---------------------------------------------------------------------------
# 10. Benchmark harness (SWE-bench/agbenchmark-inspired)
# ---------------------------------------------------------------------------

@dataclass
class BenchResult:
    name: str
    command: str
    exit_code: int
    seconds: float
    output_tail: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def run_benchmark(
    cases: Iterable[Mapping[str, Any]],
    *,
    cwd: str | Path,
    timeout: int = 120,
) -> list[BenchResult]:
    """Run test commands through the same guarded SystemBot command gate."""
    import system_bot  # type: ignore

    results: list[BenchResult] = []
    for case in cases:
        name = str(case.get("name") or "case")
        command = str(case.get("command") or "")
        if not command:
            continue
        started = time.perf_counter()
        guard = system_bot.check_command(command)
        if not guard.get("allowed"):
            results.append(BenchResult(name, command, 126, 0.0,
                                       str(guard.get("reason") or "blocked")))
            continue
        result = system_bot.run_command(command, cwd=str(cwd), timeout=timeout)
        output = "\n".join(str(item) for item in (result.get("output_tail") or []))
        results.append(BenchResult(
            name, command, int(result.get("exit_code") or 0),
            round(time.perf_counter() - started, 3),
            output[-1200:],
        ))
    return results
