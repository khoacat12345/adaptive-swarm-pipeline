#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thang ghi file an toan - Format Ladder (Task 1.4).

Ba bac:

  Bac 0  whole   : ghi de ca file. CHI cho file <= 150 dong.
  Bac 1  replace : khoi SEARCH/REPLACE, co chuan hoa khoang trang
                   (khop chinh xac -> khop sau khi bo trailing space -> khop
                    chiu duoc lech thut dau dong, tu chinh lai indent).
  Bac 2  symbol  : thay theo TEN ham/class, dung `ast` de tim dung khoang dong.

Quy tac: patch ap dung that bai -> **tu dong ha bac va thu lai 1 lan**
(max_attempts = 2). Vi du: Coder tra ve `whole` cho file 200 dong -> bac 0 bi tu
choi theo dung luat -> engine tu sinh khoi SEARCH/REPLACE tu diff roi ap dung lai.

Bat bien quan trong: `apply_patch()` **khong bao gio tra ve code sai cu phap**.
Neu file goc parse duoc thi ket qua cung phai parse duoc; neu khong -> PatchError.
"""

from __future__ import annotations

import ast
import difflib

LEVEL_WHOLE = 0
LEVEL_REPLACE = 1
LEVEL_SYMBOL = 2
LEVEL_NAME = {LEVEL_WHOLE: "whole", LEVEL_REPLACE: "replace", LEVEL_SYMBOL: "symbol"}
MAX_WHOLE_FILE_LINES = 150
MAX_ATTEMPTS = 2
CONTEXT_LINES = 2


class PatchError(Exception):
    """Mot bac khong ap dung duoc."""


class PatchFailed(PatchError):
    """Da thu het cac bac cho phep ma van that bai."""

    def __init__(self, history: list[dict]) -> None:
        self.history = history
        detail = "; ".join(
            f"bac {item['level']}({LEVEL_NAME[item['level']]}): {item.get('error')}"
            for item in history
        )
        super().__init__(f"ap dung that bai sau {len(history)} lan thu -- {detail}")


# --------------------------------------------------------------------------
# Tien ich
# --------------------------------------------------------------------------

def count_lines(text: str) -> int:
    return len(_split(text)[0])


def _normalize(text: str) -> str:
    """Dua moi kieu xuong dong ve \\n de so khop.

    Co y KHONG dung `str.splitlines()`: no cat ca `\\x0c`, `\\x85`, `\\u2028` — nhung
    ky tu do co the nam trong string literal cua file nguon. Cat roi ghep lai se
    lam doi noi dung ben trong chuoi.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _detect_newline(text: str) -> str:
    """Kieu xuong dong chiem uu the cua file (de ghi tra lai dung kieu cu)."""
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _split(text: str) -> tuple[list[str], bool]:
    normalized = _normalize(text)
    trailing = normalized.endswith("\n")
    lines = normalized.split("\n")
    if trailing and lines and lines[-1] == "":
        lines.pop()
    return lines, trailing


def _join(lines: list[str], *, trailing_newline: bool, newline: str = "\n") -> str:
    body = "\n".join(lines)
    if trailing_newline and body:
        body += "\n"
    return body if newline == "\n" else body.replace("\n", newline)


def _lines(text: str) -> list[str]:
    return _split(text)[0]


def _leading(whitespace_line: str) -> str:
    return whitespace_line[: len(whitespace_line) - len(whitespace_line.lstrip())]


def _dedent_block(block: str) -> list[str]:
    """Bo phan thut dau chung cua mot khoi code do model tra ve."""
    lines = block.splitlines()
    indents = [_leading(line) for line in lines if line.strip()]
    if not indents:
        return lines
    common = indents[0]
    for indent in indents[1:]:
        while not indent.startswith(common):
            common = common[:-1]
            if not common:
                return lines
    if not common:
        return lines
    return [line[len(common):] if line.startswith(common) else line for line in lines]


def _assert_sane(before: str, after: str) -> None:
    """Khong bao gio de lot code sai cu phap ra khoi thang ghi file."""
    if not after.strip():
        raise PatchError("ket qua rong")
    if after == before:
        raise PatchError("patch khong tao ra thay doi nao")
    try:
        ast.parse(before)
    except SyntaxError:
        return          # file goc da hong cu phap -> khong the lam chuan
    try:
        ast.parse(after)
    except SyntaxError as exc:
        raise PatchError(f"ket qua sai cu phap: {exc.msg} (dong {exc.lineno})") from exc


# --------------------------------------------------------------------------
# Bac 0 - whole file
# --------------------------------------------------------------------------

def apply_whole(content: str, patch: dict, *, max_lines: int = MAX_WHOLE_FILE_LINES) -> str:
    new_content = patch.get("content")
    if not isinstance(new_content, str):
        raise PatchError("patch 'whole' thieu truong 'content'")
    total = count_lines(content)
    if total > max_lines:
        raise PatchError(
            f"file {total} dong > {max_lines} dong -> khong duoc ghi ca file (bac 0)"
        )
    # Giu dung kieu xuong dong cua file goc: file CRLF o lai CRLF.
    newline = _detect_newline(content)
    normalized = _normalize(new_content)
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized if newline == "\n" else normalized.replace("\n", newline)


# --------------------------------------------------------------------------
# Bac 1 - SEARCH / REPLACE
# --------------------------------------------------------------------------

def _find_block(haystack: list[str], needle: list[str]) -> tuple[int, str]:
    """Tim khoi `needle` trong `haystack`. Tra ve (vi tri, che do khop)."""
    if not needle:
        raise PatchError("khoi SEARCH rong")

    size = len(needle)
    if size > len(haystack):
        raise PatchError("khoi SEARCH dai hon file")

    def scan(transform) -> list[int]:
        target = [transform(line) for line in needle]
        return [
            index for index in range(len(haystack) - size + 1)
            if [transform(line) for line in haystack[index:index + size]] == target
        ]

    for mode, transform in (
        ("exact", lambda line: line.rstrip()),           # bo trailing space
        ("indent", lambda line: line.strip()),           # chiu duoc lech indent
    ):
        hits = scan(transform)
        if len(hits) == 1:
            return hits[0], mode
        if len(hits) > 1:
            raise PatchError(
                f"khoi SEARCH xuat hien {len(hits)} lan ({mode}) -> mo ho, khong ap dung"
            )
    raise PatchError("khong tim thay khoi SEARCH (da thu chuan hoa khoang trang)")


def _derive_edits(old: str, new: str) -> list[dict]:
    """Sinh khoi SEARCH/REPLACE tu diff giua 2 ban day du (dung khi ha bac 0 -> 1)."""
    old_lines, _ = _split(old)
    new_lines, _ = _split(new)
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    changed = [op for op in matcher.get_opcodes() if op[0] != "equal"]
    if not changed:
        raise PatchError("khong co thay doi nao giua 2 ban")

    first, last = changed[0], changed[-1]
    start = max(0, first[1] - CONTEXT_LINES)
    end = min(len(old_lines), last[2] + CONTEXT_LINES)
    new_start = max(0, first[3] - (first[1] - start))
    new_end = min(len(new_lines), last[4] + (end - last[2]))

    return [{
        "search": "\n".join(old_lines[start:end]),
        "replace": "\n".join(new_lines[new_start:new_end]),
    }]


def apply_replace(content: str, patch: dict) -> str:
    edits = patch.get("edits")
    if not edits:
        # Ha bac tu dong: bac 0 that bai -> sinh khoi replace tu chinh noi dung do
        if isinstance(patch.get("content"), str):
            edits = _derive_edits(content, patch["content"])
        else:
            raise PatchError("patch 'replace' thieu 'edits'")

    trailing_newline = content.endswith("\n") or content.endswith("\r")
    newline = _detect_newline(content)
    lines = _lines(content)
    for edit in edits:
        search = edit.get("search")
        replace = edit.get("replace")
        if not isinstance(search, str) or not isinstance(replace, str):
            raise PatchError("moi edit phai co 'search' va 'replace' dang chuoi")
        needle = _split(search)[0]
        replacement = _split(replace)[0]
        start, mode = _find_block(lines, needle)
        if mode == "indent":
            old_base = _leading(needle[0])
            new_base = _leading(lines[start])
            replacement = [
                (new_base + line[len(old_base):]) if line.startswith(old_base) and line.strip()
                else ("" if not line.strip() else new_base + line.lstrip())
                for line in replacement
            ]
        lines = lines[:start] + replacement + lines[start + len(needle):]
    return _join(lines, trailing_newline=trailing_newline, newline=newline)


# --------------------------------------------------------------------------
# Bac 2 - symbol replacement
# --------------------------------------------------------------------------

def _symbol_span(tree: ast.AST, name: str) -> tuple[int, int]:
    """Tra ve (dong bat dau tinh tu 0, dong ket thuc khong bao gom) cua symbol."""
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == name
    ]
    if not matches:
        raise PatchError(f"khong tim thay ham/class ten '{name}'")
    if len(matches) > 1:
        raise PatchError(f"ten '{name}' xuat hien {len(matches)} lan -> mo ho")
    node = matches[0]
    start = node.lineno - 1
    for decorator in getattr(node, "decorator_list", []):
        start = min(start, decorator.lineno - 1)
    return start, node.end_lineno


def apply_symbol(content: str, patch: dict) -> str:
    edits = patch.get("edits")
    if not edits:
        raise PatchError("patch 'symbol' thieu 'edits' (ten ham/class)")
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        raise PatchError(f"file khong parse duoc nen khong the thay theo symbol: {exc.msg}") from exc

    trailing_newline = content.endswith("\n") or content.endswith("\r")
    newline = _detect_newline(content)
    lines = _lines(content)
    # Ap dung tu cuoi len dau de so dong phia truoc khong bi lech.
    spans = []
    for edit in edits:
        name = edit.get("name")
        replace = edit.get("replace")
        if not name or not isinstance(replace, str):
            raise PatchError("moi edit symbol can 'name' va 'replace'")
        start, end = _symbol_span(tree, name)
        spans.append((start, end, _dedent_block(replace)))
    for start, end, replacement in sorted(spans, reverse=True):
        indent = _leading(lines[start]) if start < len(lines) else ""
        body = [("" if not line.strip() else indent + line) for line in replacement]
        lines = lines[:start] + body + lines[end:]
    return _join(lines, trailing_newline=trailing_newline, newline=newline)


# --------------------------------------------------------------------------
# Dieu phoi thang
# --------------------------------------------------------------------------

_APPLY = {LEVEL_WHOLE: apply_whole, LEVEL_REPLACE: apply_replace, LEVEL_SYMBOL: apply_symbol}


def _declared_level(patch: dict) -> int:
    fmt = str(patch.get("format") or "whole").strip().lower()
    for level, name in LEVEL_NAME.items():
        if name == fmt:
            return level
    return LEVEL_WHOLE


def candidate_levels(content: str, declared: int, *, max_lines: int = MAX_WHOLE_FILE_LINES) -> list[int]:
    """Thu tu cac bac se thu: bat dau tu bac Coder khai bao, roi ha dan.

    Bac 0 bi loai neu file dai hon gioi han (dung luat, khong phai loi).
    """
    if declared == LEVEL_WHOLE and count_lines(content) > max_lines:
        declared = LEVEL_REPLACE
    return [declared] + [level for level in (LEVEL_WHOLE, LEVEL_REPLACE, LEVEL_SYMBOL)
                         if level > declared]


def apply_patch(content: str, patch: dict, *, max_lines: int = MAX_WHOLE_FILE_LINES,
                max_attempts: int = MAX_ATTEMPTS) -> dict:
    """Ap dung patch, tu ha bac khi that bai. Raise PatchFailed neu het cach."""
    declared = _declared_level(patch)
    levels = candidate_levels(content, declared, max_lines=max_lines)
    history: list[dict] = []

    for attempt, level in enumerate(levels[:max_attempts], start=1):
        try:
            new_content = _APPLY[level](content, patch)
            _assert_sane(content, new_content)
        except PatchError as exc:
            history.append({"attempt": attempt, "level": level,
                            "level_name": LEVEL_NAME[level], "ok": False, "error": str(exc)})
            continue
        history.append({"attempt": attempt, "level": level,
                        "level_name": LEVEL_NAME[level], "ok": True, "error": None})
        return {
            "content": new_content,
            "level": level,
            "level_name": LEVEL_NAME[level],
            "declared_level": declared,
            "attempts": attempt,
            "downgraded": level != declared,
            "history": history,
            "lines_before": count_lines(content),
            "lines_after": count_lines(new_content),
        }

    raise PatchFailed(history)
