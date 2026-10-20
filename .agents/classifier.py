#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bo phan loai task (Task 1.1) - rules-first.

Nguyen tac: ~90% truong hop phan loai duoc bang luat, KHONG ton request nao.
Chi khi tin hieu xung dot/khong du (mo ho) moi goi LLM, va chi voi prompt ngan
(< 200 token). Neu LLM cung khong ro -> mac dinh Level 2 (lam chac truoc, lam
nhanh sau).

  Level 0  khong sua code  (tra loi cau hoi, giai thich, chi dan)
  Level 1  sua 1 cho ro rang trong 1 file (typo, doi ten, sua 1 dong)
  Level 2  bug logic / refactor 1 file (can reviewer doc diff)

Cach dem file: uu tien danh sach `paths` do ben goi truyen vao (chinh xac);
neu khong co moi trich tu prompt theo duoi file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata

LEVEL_0 = 0
LEVEL_1 = 1
LEVEL_2 = 2
LEVEL_LABEL = {LEVEL_0: "no-code", LEVEL_1: "quick-fix", LEVEL_2: "logic-fix"}

MAX_LLM_PROMPT_TOKENS = 200
_LLM_TASK_CHARS = 600
_FILE_RE = re.compile(
    r"[\w./\\-]+\.(?:py|js|mjs|cjs|ts|tsx|jsx|go|rs|cs|java|kt|rb|php|gd|gdscript|"
    r"html|css|scss|json|ya?ml|md|toml|cfg|ini|sh|ps1|sql|c|h|cpp|hpp|lua|swift|dart)\b",
    re.IGNORECASE,
)

RISK_KEYWORDS = (
    "auth", "xac thuc", "token", "jwt", "oauth", "login", "dang nhap", "password",
    "mat khau", "secret", "permission", "phan quyen", "session", "payment",
    "thanh toan", "migration", "migrate", "schema", "database", "sql", "query",
    "encrypt", "ma hoa", "giai ma", "api key", "credential", "role", "cors",
    "cookie", "csrf", "xss", "injection",
)
COSMETIC_KEYWORDS = (
    "typo", "chinh ta", "comment", "chu thich", "docstring", "readme", "tai lieu",
    "format", "dinh dang", "rename", "doi ten", "khoang trang", "log message",
    "text", "label", "thong bao", "message",
)
LOGIC_KEYWORDS = (
    "bug", "loi", "sai", "khong dung", "logic", "refactor", "tach ham", "toi uu",
    "optimize", "cong thuc", "tinh toan", "off-by-one", "exception", "crash",
    "treo", "race", "deadlock", "memory leak", "hieu nang", "performance",
    "viet lai", "rewrite", "restructure", "tach file", "gop ham",
)
ACTION_VERBS = (
    "sua", "fix", "doi", "thay", "update", "them", "bo", "xoa", "remove", "add",
    "refactor", "viet lai", "rewrite", "chuyen", "convert", "nang cap", "cai thien",
)
QUESTION_MARKERS = (
    "tai sao", "why", "lam sao", "how", "nhu the nao", "what", "gi ", "o dau",
    "when", "khi nao", "co nen", "should i", "giai thich", "explain", "nghia la",
    "khac nhau", "so sanh", "compare",
)


# --------------------------------------------------------------------------
# Chuan hoa tieng Viet (bo dau) de so khop tu khoa on dinh
# --------------------------------------------------------------------------

def normalize(text: str) -> str:
    text = (text or "").lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def _hits(normalized: str, keywords) -> list[str]:
    found = []
    for keyword in keywords:
        key = normalize(keyword).strip()
        if not key:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", normalized):
            found.append(key)
    return found


def extract_paths(text: str) -> list[str]:
    seen, paths = set(), []
    for raw in _FILE_RE.findall(text or ""):
        cleaned = raw.strip().strip(".,;:()[]'\"`")
        if cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        paths.append(cleaned)
    return paths


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


# --------------------------------------------------------------------------
# Phan loai
# --------------------------------------------------------------------------

def _llm_prompt(task: str, paths: list[str]) -> str:
    snippet = " ".join((task or "").split())[:_LLM_TASK_CHARS]
    files = ", ".join(paths[:5]) or "(khong ro)"
    return (
        "Phan loai yeu cau sau thanh dung 1 muc:\n"
        "0 = chi hoi/dap hoac sua vai ky tu, khong can sua code\n"
        "1 = sua ro rang 1 cho trong 1 file\n"
        "2 = bug logic hoac refactor\n"
        f"Files: {files}\n"
        f"Yeu cau: {snippet}\n"
        "Chi tra ve 1 so (0, 1 hoac 2)."
    )


def _ask_llm(llm, prompt: str) -> int | None:
    try:
        answer = llm(prompt)
    except Exception:                                   # noqa: BLE001 - LLM loi -> mac dinh
        return None
    match = re.search(r"\b([012])\b", str(answer or ""))
    return int(match.group(1)) if match else None


def classify(task: str, *, paths=None, llm=None) -> dict:
    """Phan loai 1 yeu cau. Tra ve dict (khong raise)."""
    task = task or ""
    normalized = normalize(task)
    signals: dict = {}

    resolved_paths = list(paths) if paths else extract_paths(task)
    file_count = len(resolved_paths)
    signals["files"] = resolved_paths[:10]
    signals["file_count"] = file_count
    signals["risk"] = _hits(normalized, RISK_KEYWORDS)
    signals["cosmetic"] = _hits(normalized, COSMETIC_KEYWORDS)
    signals["logic"] = _hits(normalized, LOGIC_KEYWORDS)
    signals["action"] = _hits(normalized, ACTION_VERBS)

    is_question = task.strip().endswith("?") or (
        any(marker in normalized for marker in QUESTION_MARKERS) and not signals["action"]
    )
    signals["question"] = is_question
    signals["multi_file"] = file_count >= 2
    signals["prompt_tokens"] = estimate_tokens(task)

    def result(level: int, reason: str, decided_by: str, llm_tokens: int = 0) -> dict:
        return {
            "level": level,
            "level_label": LEVEL_LABEL[level],
            "reason": reason,
            "decided_by": decided_by,
            "signals": signals,
            "llm_prompt_tokens": llm_tokens,
            "needs_reviewer": level >= LEVEL_2,
            "needs_work_order": level >= LEVEL_1,
        }

    # 1) Cau hoi / khong co dong tu hanh dong -> khong dung code
    if is_question and not signals["action"]:
        return result(LEVEL_0, "la cau hoi/yEU cau giai thich, khong co dong tu sua code", "rules")
    if not task.strip():
        return result(LEVEL_0, "yeu cau rong", "rules")

    # 2) Tu khoa rui ro (auth/db/migration...) -> luon Level 2
    if signals["risk"]:
        return result(LEVEL_2, f"cham vao vung rui ro: {', '.join(signals['risk'][:3])}", "rules")

    # 3) Nhieu file -> Level 2
    if file_count >= 2:
        return result(LEVEL_2, f"dong {file_count} file: {', '.join(resolved_paths[:3])}", "rules")

    # 4) Dung 1 file
    if file_count == 1:
        if signals["logic"]:
            return result(LEVEL_2, f"bug logic/refactor: {', '.join(signals['logic'][:3])}", "rules")
        if signals["cosmetic"]:
            return result(LEVEL_1, f"thay doi nho (cosmetic): {', '.join(signals['cosmetic'][:3])}", "rules")
        if signals["action"]:
            return result(LEVEL_1, "1 file + dong tu sua cu the, khong co tin hieu logic", "rules")

    # 5) Mo ho -> LLM (neu co), neu khong -> Level 2
    prompt = _llm_prompt(task, resolved_paths)
    prompt_tokens = estimate_tokens(prompt)
    if prompt_tokens > MAX_LLM_PROMPT_TOKENS:
        prompt = prompt[: MAX_LLM_PROMPT_TOKENS * 4]
        prompt_tokens = estimate_tokens(prompt)

    if llm is not None:
        level = _ask_llm(llm, prompt)
        if level is None:
            return result(LEVEL_2, "LLM tra loi khong hop le -> mac dinh Level 2", "llm-fallback", prompt_tokens)
        return result(level, "tin hieu xung dot -> LLM phan loai", "llm", prompt_tokens)

    return result(LEVEL_2, "mo ho (khong ro file/pham vi) -> mac dinh Level 2", "rules-default")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phan loai task (rules-first).")
    parser.add_argument("--task", required=True)
    parser.add_argument("--paths", default="", help="danh sach file, cach nhau bang dau phay")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    paths = [item.strip() for item in args.paths.split(",") if item.strip()]
    outcome = classify(args.task, paths=paths or None)
    if args.json:
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
    else:
        print(f"level       : {outcome['level']} ({outcome['level_label']})")
        print(f"decided_by  : {outcome['decided_by']}")
        print(f"reason      : {outcome['reason']}")
        print(f"reviewer    : {outcome['needs_reviewer']}")
        print(f"files       : {outcome['signals']['file_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
