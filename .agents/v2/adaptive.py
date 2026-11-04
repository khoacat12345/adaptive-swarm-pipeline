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
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SKIP_DIRS = {
    ".git", ".agents/tmp", ".specify", "node_modules", "__pycache__",
    ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache",
    "AppData", "Downloads", "Documents", "Desktop", "OneDrive",
    "Music", "Pictures", "Videos", ".cache", ".local", ".config",
    ".gemini", ".opencode", ".claude", ".copilot", ".vscode", ".github",
    ".kaggle", ".antigravity-ide", ".lim", ".limpc",
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
    """Python AST repo map with a PageRank-inspired file relevance graph.

    The graph is deliberately lightweight (no networkx). It models local
    imports and symbol references, then personalizes PageRank with task terms.
    This is the mechanism extracted from aider/repomap.py, without its
    tree-sitter dependency.
    """

    def __init__(self, root: str | Path, *, max_tokens: int = 1200,
                 token_counter=None, max_files: int = 400):
        self.root = Path(root).resolve()
        self.max_tokens = max(200, int(max_tokens))
        self.token_counter = token_counter
        self.max_files = max(1, int(max_files))

    def count_tokens(self, text: str) -> int:
        if self.token_counter is not None:
            return max(1, int(self.token_counter(text)))
        return max(1, len(re.findall(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]", text or "")))

    def _skip(self, path: Path) -> bool:
        relative = path.relative_to(self.root).as_posix()
        return (
            any(relative == item or relative.startswith(item + "/") for item in SKIP_DIRS)
            or bool(SECRET_NAME_RE.search(path.name))
        )

    def _py_files(self) -> list[Path]:
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            current = Path(dirpath)
            dirnames[:] = [
                name for name in dirnames
                if not self._skip(current / name)
            ]
            for name in filenames:
                candidate = current / name
                if candidate.suffix == ".py" and not self._skip(candidate):
                    found.append(candidate)
                    if len(found) >= self.max_files:
                        return found
        return found

    def _scan(self, path: Path) -> tuple[list[Symbol], list[str], set[str]]:
        relative = path.relative_to(self.root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            return [], [], set()
        symbols: list[Symbol] = []
        imports: list[str] = []
        used: set[str] = set()
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
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
        return symbols, imports, used

    @staticmethod
    def _module_keys(relative: str) -> set[str]:
        without = relative[:-3] if relative.endswith(".py") else relative
        parts = [part for part in without.split("/") if part and part != "."]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        keys = {".".join(parts), parts[-1] if parts else ""}
        keys.update(part for part in parts if part)
        return {key for key in keys if key}

    @staticmethod
    def _pagerank(graph: dict[str, dict[str, float]], personal: dict[str, float],
                   *, alpha: float = 0.15, iterations: int = 25) -> dict[str, float]:
        nodes = sorted(graph)
        if not nodes:
            return {}
        total = sum(personal.get(node, 0.0) for node in nodes)
        if total <= 0:
            personal = {node: 1.0 / len(nodes) for node in nodes}
        else:
            personal = {node: personal.get(node, 0.0) / total for node in nodes}
        rank = {node: 1.0 / len(nodes) for node in nodes}
        outgoing = {
            node: sum(weight for weight in graph.get(node, {}).values())
            for node in nodes
        }
        for _ in range(iterations):
            nxt = {node: (1.0 - alpha) * personal[node] for node in nodes}
            dangling = sum(rank[node] for node in nodes if outgoing.get(node, 0.0) <= 0)
            if dangling:
                share = alpha * dangling / len(nodes)
                for node in nodes:
                    nxt[node] += share
            for source in nodes:
                degree = outgoing.get(source, 0.0)
                if degree <= 0:
                    continue
                for target, weight in graph.get(source, {}).items():
                    if target in nxt:
                        nxt[target] += alpha * rank[source] * weight / degree
            rank = nxt
        return rank

    def build(self, task: str = "") -> RepoMap:
        terms = _tokens(task)
        files: list[str] = []
        symbols_by_file: dict[str, list[Symbol]] = {}
        imports_by_file: dict[str, list[str]] = {}
        used_by_file: dict[str, set[str]] = {}
        definitions: dict[str, set[str]] = {}
        for path in sorted(self._py_files()):
            if len(files) >= self.max_files:
                break
            if not path.is_file() or self._skip(path):
                continue
            relative = path.relative_to(self.root).as_posix()
            files.append(relative)
            symbols, imports, used = self._scan(path)
            symbols_by_file[relative] = symbols
            imports_by_file[relative] = imports
            used_by_file[relative] = used
            for symbol in symbols:
                definitions.setdefault(symbol.name, set()).add(relative)
        graph: dict[str, dict[str, float]] = {file: {} for file in files}
        for file in files:
            for imported in imports_by_file.get(file, []):
                last = imported.split(".")[-1]
                for target in files:
                    if target == file:
                        continue
                    keys = self._module_keys(target)
                    if imported in keys or last in keys or any(key.endswith("." + last) for key in keys):
                        graph[file][target] = graph[file].get(target, 0.0) + 1.0
            for used in used_by_file.get(file, set()):
                for target in definitions.get(used, set()):
                    if target != file:
                        graph[file][target] = graph[file].get(target, 0.0) + 0.5
        personal: dict[str, float] = {}
        for file in files:
            haystack = (file + " " + " ".join(
                symbol.qualified for symbol in symbols_by_file.get(file, [])
            )).lower()
            hits = sum(1 for term in terms if term in haystack)
            personal[file] = float(hits + (1 if not terms else 0))
        rank = self._pagerank(graph, personal)
        all_symbols = [item for file in files for item in symbols_by_file.get(file, [])]

        def score(item: Symbol) -> tuple:
            haystack = f"{item.path} {item.parent} {item.name} {' '.join(item.imports)}".lower()
            hits = sum(1 for term in terms if term in haystack)
            exact = sum(2 for term in terms if term in item.name.lower())
            kind_bonus = 2 if item.kind == "class" else 1
            return (-(rank.get(item.path, 0.0) * 100 + hits + exact + kind_bonus),
                    item.path, item.line)

        ranked = sorted(all_symbols, key=score)
        lines = [f"# repo map ({len(files)} Python files, task-aware PageRank)"]
        used_tokens = self.count_tokens(lines[0])
        kept: list[Symbol] = []
        current_path = ""
        for item in ranked:
            line = f"{item.path}:{item.line} {item.kind} {item.qualified}"
            cost = self.count_tokens(line)
            if used_tokens + cost > self.max_tokens:
                continue
            if item.path != current_path:
                lines.append(f"{item.path}:")
                current_path = item.path
                used_tokens += self.count_tokens(lines[-1])
            lines.append(f"  {item.line}: {item.kind} {item.qualified}")
            used_tokens += cost
            kept.append(item)
        return RepoMap("\n".join(lines), kept, len(files), used_tokens)


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

    ALLOWED = {"list", "read", "open", "scroll", "search", "verify", "edit_check"}

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
            if action.name in {"open", "scroll"}:
                relative = str(action.args.get("path") or "")
                path = self._path(relative)
                if not path.is_file():
                    return Observation(action.name, "failed", f"not a file: {relative}")
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                window = max(1, min(200, int(action.args.get("window") or 40)))
                start = int(action.args.get("start_line") or action.args.get("line") or 1)
                if action.name == "scroll":
                    start += int(action.args.get("offset") or window)
                start = max(1, min(start, max(1, len(lines))))
                end = min(len(lines), start + window - 1)
                body = "\n".join(f"{number:>5}: {lines[number - 1]}" for number in range(start, end + 1))
                return Observation(
                    action.name, "ok", body,
                    metadata={"path": relative, "start_line": start, "end_line": end,
                              "total_lines": len(lines)},
                    truncated=end < len(lines),
                )
            if action.name == "edit_check":
                relative = str(action.args.get("path") or "")
                path = self._path(relative)
                if not path.is_file():
                    return Observation("edit_check", "failed", f"not a file: {relative}")
                old = str(action.args.get("old") or "")
                new = str(action.args.get("new") or "")
                if not old:
                    return Observation("edit_check", "failed", "old text is empty")
                if old == new:
                    return Observation("edit_check", "failed", "edit is a no-op")
                text = path.read_text(encoding="utf-8", errors="replace")
                count = text.count(old)
                if count != 1:
                    return Observation("edit_check", "failed",
                                       f"old text must be unique; found {count} matches",
                                       metadata={"path": relative, "matches": count})
                line = text[:text.index(old)].count("\n") + 1
                start_line = action.args.get("start_line")
                end_line = action.args.get("end_line")
                if start_line is not None or end_line is not None:
                    low = int(start_line or 1)
                    high = int(end_line or len(text.splitlines()))
                    if not low <= line <= high:
                        return Observation("edit_check", "failed",
                                           f"edit line {line} is outside window {low}-{high}",
                                           metadata={"path": relative, "line": line})
                lint = "ok"
                if path.suffix == ".py":
                    candidate = text.replace(old, new, 1)
                    try:
                        compile(candidate, str(path), "exec")
                    except SyntaxError as exc:
                        lint = f"syntax: {exc.msg} at {exc.lineno}"
                if lint != "ok":
                    return Observation("edit_check", "failed", lint,
                                       metadata={"path": relative, "line": line})
                diff_lines = sum(1 for a, b in zip(old.splitlines(), new.splitlines()) if a != b)
                diff_lines += abs(len(old.splitlines()) - len(new.splitlines()))
                return Observation(
                    "edit_check", "ok", "edit is unique and syntactically valid",
                    metadata={"path": relative, "line": line, "matches": 1,
                              "lint": lint, "diff_lines": diff_lines},
                )
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
    updated_at: str = field(default_factory=_now)
    revision: int = 0


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
                    updated_at=str(item.get("updated_at") or _now()),
                    revision=int(item.get("revision") or 0),
                ))
                self._upsert_memory(self.lessons[-1])
            except (json.JSONDecodeError, ValueError):
                continue
        return self

    def _upsert_memory(self, lesson: "Lesson") -> None:
        for index, item in enumerate(self.lessons[:-1]):
            if item.signature == lesson.signature:
                # The newest JSONL line wins; preserve the earliest creation time.
                lesson.created_at = item.created_at
                self.lessons.pop(index)
                break

    def record(self, lesson: Lesson) -> Lesson:
        """Upsert by signature: one current lesson, append-only audit line."""
        if not lesson.signature or not lesson.fix:
            raise ValueError("lesson needs signature and fix")
        existing = next((item for item in self.lessons if item.signature == lesson.signature), None)
        if existing is not None:
            lesson.created_at = existing.created_at
            lesson.revision = existing.revision + 1
            self.lessons = [item for item in self.lessons if item.signature != lesson.signature]
        self.lessons.append(lesson)
        lesson.updated_at = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(lesson.__dict__)
        payload["op"] = "upsert"
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
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
