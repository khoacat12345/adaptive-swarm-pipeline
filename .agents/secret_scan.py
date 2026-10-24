#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Secret Scanner (Task 3.5).

Quet truoc khi bat ky noi dung nao duoc gui ra model:

  1. Duong dan nhay cam  -> KHONG BAO GIO gui (`.env`, `*.pem`, `id_rsa`, credential...)
  2. Chuoi bi mat trong noi dung -> thay bang `[REDACTED_SECRET]` va ghi finding

Nguyen tac: scanner khong bao gio ghi lai gia tri bi mat vao ledger. Finding chi
luu loai + preview da che (`sk-ant-...3f2a`) + vi tri dong.

Dung:
    from secret_scan import scan_text, scan_file, is_sensitive_path
    clean, findings = scan_text(prompt)
"""

from __future__ import annotations

import re
from pathlib import Path

import permissions

REDACTED = "[REDACTED_SECRET]"
MAX_SCAN_BYTES = 1_000_000

# (nhan, regex). Sap xep tu cu the -> chung, de preview de doc.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe_key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("private_key_block",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                re.DOTALL)),
    ("connection_string",
     re.compile(r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:@/]+:[^\s@/]+@",
                re.IGNORECASE)),
    ("assigned_secret",
     re.compile(r"(?i)\b(?:api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*"
                r"[\"'][^\"'\s]{12,}[\"']")),
)

# Ten file khong bao gio duoc dua vao ngu canh (bo sung cho permissions.is_sensitive).
SENSITIVE_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".jks", ".keystore")
SENSITIVE_NAMES = (".env", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519",
                   "credentials", "credentials.json")


def _mask(value: str) -> str:
    """Che gia tri bi mat, chi giu dau/cuoi de con nhan ra loai key."""
    value = value.strip().replace("\n", " ")
    if len(value) <= 10:
        return value[:2] + "..."
    return f"{value[:7]}...{value[-4:]}"


def is_sensitive_path(path) -> str | None:
    """Tra ve ly do neu duong dan nay khong duoc phep gui ra ngoai."""
    name = Path(str(path)).name.lower()
    if name in SENSITIVE_NAMES or name.startswith(".env"):
        return f"ten file nhay cam: {name}"
    if name.endswith(SENSITIVE_SUFFIXES):
        return f"duoi file chua khoa: {name}"
    if permissions.is_sensitive(path):
        return f"khop mau nhay cam cua permissions: {name}"
    return None


def scan_text(text: str, *, filename: str | None = None) -> tuple[str, list[dict]]:
    """Tra ve (noi dung da redact, danh sach finding). Khong raise."""
    if not isinstance(text, str) or not text:
        return text or "", []
    findings: list[dict] = []
    redacted = text
    for label, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            findings.append({
                "kind": label,
                "file": filename,
                "line": text.count("\n", 0, match.start()) + 1,
                "preview": _mask(match.group(0)),
            })
        redacted = pattern.sub(REDACTED, redacted)
    return redacted, findings


def scan_file(path) -> list[dict]:
    """Quet 1 file tren dia. File qua lon / khong doc duoc -> bo qua, khong raise."""
    target = Path(path)
    try:
        raw = target.read_bytes()[:MAX_SCAN_BYTES]
    except OSError:
        return []
    text = raw.decode("utf-8", errors="replace")
    _clean, findings = scan_text(text, filename=str(target.name))
    return findings


def scan_paths(paths, root=None) -> list[dict]:
    """Quet danh sach duong dan: chan theo ten file + quet noi dung."""
    findings: list[dict] = []
    for item in paths or []:
        path = Path(item) if root is None else Path(root) / item
        reason = is_sensitive_path(path)
        if reason:
            findings.append({"kind": "sensitive_path", "file": str(item),
                             "line": 0, "preview": reason})
            continue
        findings.extend(scan_file(path))
    return findings


def summarize(findings: list[dict], limit: int = 5) -> str:
    if not findings:
        return "khong phat hien"
    kinds: dict[str, int] = {}
    for finding in findings:
        kinds[finding["kind"]] = kinds.get(finding["kind"], 0) + 1
    detail = ", ".join(f"{kind}={count}" for kind, count in sorted(kinds.items()))
    return f"{len(findings)} finding ({detail})"[:400]


def build_parser():
    import argparse
    parser = argparse.ArgumentParser(description="Quet secret truoc khi gui model.")
    parser.add_argument("paths", nargs="+", help="file can quet")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None) -> int:
    import json
    args = build_parser().parse_args(argv)
    findings = scan_paths(args.paths)
    if args.json:
        print(json.dumps({"findings": findings}, ensure_ascii=False, indent=2))
    else:
        print(summarize(findings))
        for finding in findings[:20]:
            print(f"  {finding['kind']:<18} {finding['file']}:{finding['line']}  {finding['preview']}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
