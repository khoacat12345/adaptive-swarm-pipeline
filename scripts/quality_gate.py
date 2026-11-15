#!/usr/bin/env python3
"""Anti-Slop quality gate for the Adaptive Swarm code base.

The gate parses Python with :mod:`ast` and blocks *new* instances of the slop
patterns we care about:

=====  ==================================================================
AS001  duplicate top-level function / class name inside one module
AS002  naked ``except:`` that swallows every error
AS003  swallowed exception (``except Exception: pass``)
AS004  function body longer than the configured limit (default 100 lines)
AS005  file that cannot be parsed
=====  ==================================================================

``--changed`` inspects the Python files touched by the working tree (tracked
modifications plus untracked files) and compares them with a git baseline
(``HEAD`` by default) so pre-existing debt never blocks unrelated work::

    python scripts/quality_gate.py --changed

Exit status is ``0`` when nothing new is found and ``1`` otherwise.
"""

from __future__ import annotations

import argparse
import ast
import collections
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

RULE_DUPLICATE = "AS001"
RULE_NAKED_EXCEPT = "AS002"
RULE_SWALLOWED_EXCEPT = "AS003"
RULE_LONG_FUNCTION = "AS004"
RULE_SYNTAX = "AS005"

DEFAULT_MAX_FUNCTION_LINES = 100

#: Directories that hold first-party runtime code.
CORE_DIRS = (".agents", "agents", "scripts")

#: Directory names that never contain code worth gating.
EXCLUDED_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "venv",
        "env",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "tests",
        "test",
        "draft",
        "tmp",
        "backups",
    }
)

EXCLUDED_FILES = frozenset({"conftest.py", "setup.py"})

SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
SWALLOW_TYPES = frozenset({"Exception", "BaseException"})


class GateError(RuntimeError):
    """Raised when the gate cannot even determine what to inspect."""


@dataclass(frozen=True)
class Violation:
    """One slop finding, keyed so it can be matched against a baseline."""

    path: str
    line: int
    column: int
    rule: str
    message: str
    key: tuple[object, ...]

    def render(self) -> str:
        return f"{self.path}:{self.line}:{self.column}: {self.rule} {self.message}"

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "rule": self.rule,
            "message": self.message,
        }


def _decode_source(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        return None


def _git_text(root: Path, *args: str) -> str | None:
    proc = _git(root, *args)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def _repo_root(start: Path) -> Path:
    text = _git_text(start, "rev-parse", "--show-toplevel")
    if not text or not text.strip():
        raise GateError("not inside a git repository; --changed needs git")
    return Path(text.strip()).resolve()


def _split_z(text: str) -> list[str]:
    return [item for item in text.split("\0") if item]


def _changed_paths(root: Path, baseline: str | None) -> list[str]:
    """Return repo-relative paths touched by the working tree."""

    names: list[str] = []
    if baseline:
        diff = _git_text(
            root,
            "diff",
            "--name-only",
            "-z",
            "--diff-filter=ACMR",
            baseline,
            "--",
        )
        if diff is None:
            raise GateError(f"cannot diff against baseline {baseline!r}")
        names.extend(_split_z(diff))
    else:
        tracked = _git_text(root, "ls-files", "-z")
        if tracked is None:
            raise GateError("cannot list tracked files with git ls-files")
        names.extend(_split_z(tracked))

    untracked = _git_text(root, "ls-files", "--others", "--exclude-standard", "-z")
    if untracked is None:
        raise GateError("cannot list untracked files with git ls-files")
    names.extend(_split_z(untracked))
    return names


def _norm_rel(value: str) -> str:
    cleaned = value.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return "" if cleaned in (".", "") else cleaned


def _relative_to(path: Path, root: Path) -> str | None:
    try:
        return _norm_rel(path.resolve().relative_to(root).as_posix())
    except (OSError, ValueError):
        return None


def _is_excluded(rel: str) -> bool:
    parts = rel.split("/")
    if any(part in EXCLUDED_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    return name in EXCLUDED_FILES or name.startswith("test_")


def _is_core(rel: str) -> bool:
    return any(rel == base or rel.startswith(base + "/") for base in CORE_DIRS)


def _wanted(rel: str) -> bool:
    return rel.endswith(".py") and not _is_excluded(rel)


def _iter_tree(root: Path) -> list[str]:
    found: list[str] = []
    for base in CORE_DIRS:
        directory = root / base
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.rglob("*.py")):
            rel = _relative_to(candidate, root)
            if rel and _wanted(rel):
                found.append(rel)
    return found


def _collect_files(root: Path, args: argparse.Namespace) -> tuple[list[str], str | None]:
    if args.paths:
        rels: list[str] = []
        for raw in args.paths:
            path = Path(raw)
            if not path.exists():
                raise GateError(f"no such file or directory: {raw}")
            rel = _relative_to(path, root)
            if rel is None:
                raise GateError(f"path is outside the repository: {raw}")
            if path.is_dir():
                for candidate in sorted(path.rglob("*.py")):
                    child = _relative_to(candidate, root)
                    if child and _wanted(child):
                        rels.append(child)
            elif _wanted(rel):
                rels.append(rel)
        return _dedupe(rels), args.baseline

    if args.changed:
        baseline = args.baseline or _default_baseline(root)
        names = _changed_paths(root, baseline)
        rels = [
            rel
            for rel in (_norm_rel(name) for name in names)
            if rel and _wanted(rel) and _is_core(rel) and (root / rel).is_file()
        ]
        return _dedupe(rels), baseline

    return _dedupe(_iter_tree(root)), args.baseline


def _dedupe(rels: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for rel in rels:
        marker = os.path.normcase(rel)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(rel)
    return sorted(unique)


def _default_baseline(root: Path) -> str | None:
    if _git_text(root, "rev-parse", "--verify", "HEAD") is None:
        return None
    return "HEAD"


def _duplicate_findings(rel: str, tree: ast.Module) -> list[Violation]:
    seen: dict[str, list[int]] = {}
    for node in tree.body:
        if isinstance(node, SCOPE_NODES):
            seen.setdefault(node.name, []).append(node.lineno)

    findings = []
    for name, lines in seen.items():
        if len(lines) < 2:
            continue
        where = ", ".join(str(line) for line in lines)
        message = f"top-level '{name}' defined {len(lines)} times (lines {where})"
        findings.append(
            Violation(
                rel,
                lines[1],
                1,
                RULE_DUPLICATE,
                message,
                (RULE_DUPLICATE, rel, name),
            )
        )
    return findings


def _is_silent(body: list[ast.stmt]) -> bool:
    if len(body) != 1:
        return False
    statement = body[0]
    if isinstance(statement, ast.Pass):
        return True
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and statement.value.value is Ellipsis
    )


def _except_kind(handler: ast.ExceptHandler) -> tuple[str, str] | None:
    if handler.type is None:
        return RULE_NAKED_EXCEPT, "naked"
    if _is_silent(handler.body):
        type_name = handler.type.id if isinstance(handler.type, ast.Name) else None
        if type_name in SWALLOW_TYPES:
            return RULE_SWALLOWED_EXCEPT, type_name
    return None


def _function_qualnames(tree: ast.Module) -> dict[int, str]:
    names: dict[int, str] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, SCOPE_NODES):
                qualname = f"{prefix}{child.name}"
                names[id(child)] = qualname
                visit(child, qualname + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    return names


def _slurp_findings(rel: str, tree: ast.Module) -> list[Violation]:
    findings: list[Violation] = []

    def visit(node: ast.AST, scope: str) -> None:
        current = scope
        if isinstance(node, SCOPE_NODES):
            current = f"{scope}.{node.name}" if scope else node.name
        if isinstance(node, ast.ExceptHandler):
            kind = _except_kind(node)
            if kind is not None:
                rule, label = kind
                if rule == RULE_NAKED_EXCEPT:
                    message = f"naked 'except:' in '{current or '<module>'}'"
                else:
                    message = (
                        f"'except {label}: pass' swallows errors in "
                        f"'{current or '<module>'}'"
                    )
                findings.append(
                    Violation(
                        rel,
                        node.lineno,
                        node.col_offset + 1,
                        rule,
                        message,
                        (rule, rel, current, label),
                    )
                )
        for child in ast.iter_child_nodes(node):
            visit(child, current)

    visit(tree, "")
    return findings


def _long_findings(
    rel: str, tree: ast.Module, qualnames: dict[int, str], limit: int
) -> list[Violation]:
    findings: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, FUNCTION_NODES):
            continue
        end = getattr(node, "end_lineno", None) or node.lineno
        length = end - node.lineno + 1
        if length <= limit:
            continue
        qualname = qualnames.get(id(node), node.name)
        findings.append(
            Violation(
                rel,
                node.lineno,
                node.col_offset + 1,
                RULE_LONG_FUNCTION,
                f"function '{qualname}' spans {length} lines (limit {limit})",
                (RULE_LONG_FUNCTION, rel, qualname),
            )
        )
    return findings


def collect_violations(rel: str, source: str, limit: int) -> list[Violation]:
    """Return every slop finding for one module."""

    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        return [
            Violation(
                rel,
                exc.lineno or 1,
                exc.offset or 1,
                RULE_SYNTAX,
                f"cannot parse: {exc.msg}",
                (RULE_SYNTAX, rel),
            )
        ]

    findings = _duplicate_findings(rel, tree)
    findings.extend(_slurp_findings(rel, tree))
    findings.extend(_long_findings(rel, tree, _function_qualnames(tree), limit))
    findings.sort(key=lambda item: (item.line, item.column, item.rule))
    return findings


def _baseline_violations(
    root: Path, baseline: str | None, rel: str, limit: int
) -> list[Violation]:
    if not baseline:
        return []
    proc = _git(root, "show", f"{baseline}:{rel}")
    if proc is None or proc.returncode != 0:
        return []
    return collect_violations(rel, _decode_source(proc.stdout), limit)


def _select_new(
    violations: list[Violation], baseline: collections.Counter[tuple[object, ...]]
) -> tuple[list[Violation], int]:
    remaining = collections.Counter(baseline)
    fresh: list[Violation] = []
    ignored = 0
    for violation in violations:
        if remaining[violation.key] > 0:
            remaining[violation.key] -= 1
            ignored += 1
            continue
        fresh.append(violation)
    return fresh, ignored


def _load(rel: str, root: Path) -> str:
    return _decode_source((root / rel).read_bytes())


def run(
    root: Path, rels: list[str], baseline: str | None, limit: int
) -> tuple[list[Violation], int]:
    fresh: list[Violation] = []
    ignored = 0
    for rel in rels:
        try:
            current = collect_violations(rel, _load(rel, root), limit)
        except OSError as exc:
            raise GateError(f"cannot read {rel}: {exc}") from exc
        old = _baseline_violations(root, baseline, rel, limit)
        new_here, ignored_here = _select_new(current, collections.Counter(v.key for v in old))
        fresh.extend(new_here)
        ignored += ignored_here
    fresh.sort(key=lambda item: (item.path, item.line, item.column, item.rule))
    return fresh, ignored


def _emit(fresh: list[Violation], ignored: int, scanned: int, as_json: bool) -> None:
    if as_json:
        payload = {
            "ok": not fresh,
            "scanned_files": scanned,
            "pre_existing_ignored": ignored,
            "violations": [item.as_dict() for item in fresh],
        }
        print(json.dumps(payload, indent=2))
        return

    for violation in fresh:
        print(violation.render())
    if fresh:
        print(
            f"quality_gate: FAILED: {len(fresh)} new violation(s) "
            f"in {scanned} scanned Python file(s); "
            f"{ignored} pre-existing finding(s) ignored via baseline."
        )
    else:
        print(
            f"quality_gate: OK: {scanned} Python file(s) scanned, "
            f"no new slop ({ignored} pre-existing finding(s) ignored)."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quality_gate.py",
        description="AST based anti-slop gate for first-party Python code.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="explicit files/directories (default: core tree, or changed files with --changed)",
    )
    parser.add_argument(
        "--changed",
        action="store_true",
        help="inspect modified and untracked Python files inside the core tree",
    )
    parser.add_argument(
        "--baseline",
        metavar="REF",
        default=None,
        help="git ref holding the accepted baseline (default: HEAD with --changed)",
    )
    parser.add_argument(
        "--max-function-lines",
        type=int,
        default=DEFAULT_MAX_FUNCTION_LINES,
        metavar="N",
        help=f"maximum function body length (default: {DEFAULT_MAX_FUNCTION_LINES})",
    )
    parser.add_argument("--json", action="store_true", help="machine readable report")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_function_lines < 1:
        print("quality_gate: error: --max-function-lines must be >= 1", file=sys.stderr)
        return 1

    try:
        root = _repo_root(Path.cwd()) if args.changed else Path.cwd().resolve()
        rels, baseline = _collect_files(root, args)
        fresh, ignored = run(root, rels, baseline, args.max_function_lines)
    except GateError as exc:
        print(f"quality_gate: error: {exc}", file=sys.stderr)
        return 1

    _emit(fresh, ignored, len(rels), args.json)
    return 1 if fresh else 0


if __name__ == "__main__":
    sys.exit(main())
