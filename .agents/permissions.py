#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phan quyen agent (Task 1.3).

Nguyen tac:

  | Vai tro    | Quyen ghi                                  | Quyen doc |
  |------------|--------------------------------------------|-----------|
  | Coder      | CHI trong `.agents/draft/`                 | file du an |
  | Reviewer   | KHONG. Chi ghi findings vao ledger         | file du an (tru secret) |
  | Lead       | file du an, VA CHI qua `safe_writer`       | tat ca |
  | SystemBot  | KHONG (chi chay lenh)                      | tat ca |

Day la lop phong thu thu 2. Lop thu 1 la `safe_writer` (backup -> atomic -> verify
-> rollback). Ly do phai co ca hai: `safe_writer` bao ve du lieu khi ghi, con lop nay
bao ve *pham vi* — mot agent bi loi/ao giac khong the ghi bua ra ngoai vung cua no,
du noi dung co hop le den dau.

Cach Reviewer bi ep read-only: no khong duoc trao mot ham ghi file nao ca, chi duoc
trao `FindingsSink` — mot cua duy nhat append vao ledger.
"""

from __future__ import annotations

import re
from pathlib import Path

import ledger

ROLE_CODER = "coder"
ROLE_REVIEWER = "reviewer"
ROLE_LEAD = "lead"
ROLE_SYSTEM_BOT = "system_bot"
ROLES = (ROLE_CODER, ROLE_REVIEWER, ROLE_LEAD, ROLE_SYSTEM_BOT)

# Pham vi ghi cua tung vai tro (xem docstring).
WRITE_SCOPE = {
    ROLE_CODER: "draft",
    ROLE_REVIEWER: "none",
    ROLE_LEAD: "project",
    ROLE_SYSTEM_BOT: "none",
}

SENSITIVE_PATTERNS = (
    r"(^|[\\/])\.env($|\.)", r"\.pem$", r"\.key$", r"\.pfx$", r"\.p12$",
    r"\.jks$", r"id_rsa", r"id_ed25519", r"credential", r"secret", r"password",
    r"(^|[\\/])\.npmrc$", r"(^|[\\/])\.netrc$", r"(^|[\\/])\.pypirc$",
)


class PermissionDenied(Exception):
    """Agent lam viec ngoai pham vi duoc cap."""

    def __init__(self, role: str, action: str, path, reason: str) -> None:
        self.role = role
        self.action = action
        self.path = str(path)
        self.reason = reason
        super().__init__(f"[{role}] {action} bi tu choi: {path} -- {reason}")


def is_sensitive(path) -> bool:
    """File khong duoc phep dua vao ngu canh cua agent (secret, khoa, credential)."""
    text = str(path).lower()
    return any(re.search(pattern, text) for pattern in SENSITIVE_PATTERNS)


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


class PermissionGate:
    """Cong kiem soat quyen. Moi thao tac ghi/doc cua agent phai di qua day."""

    def __init__(self, root, *, run_id: str | None = None, log: bool = True) -> None:
        self.root = Path(root).resolve()
        self.agents_dir = ledger.resolve_agents_dir(root=self.root)
        self.draft_dir = self.agents_dir / "draft"
        self.run_id = run_id
        self.log = log

    # -- duong dan ---------------------------------------------------------
    def draft_path(self, task_id: str, relative_target) -> Path:
        """Vi tri nhap cua Coder, giu nguyen cay thu muc tuong doi."""
        relative = Path(str(relative_target).replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise PermissionDenied(ROLE_CODER, "draft", relative_target,
                                   "draft phai la duong dan tuong doi, khong chua '..'")
        return self.draft_dir / task_id / relative

    def project_path(self, relative_target) -> Path:
        relative = Path(str(relative_target).replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise PermissionDenied(ROLE_LEAD, "resolve", relative_target,
                                   "target phai la duong dan tuong doi trong du an")
        return self.root / relative

    # -- kiem tra ----------------------------------------------------------
    def check_write(self, role: str, path) -> dict:
        """Tra ve {allowed, reason, resolved}. Khong raise, de ben goi quyet dinh."""
        target = Path(path)
        scope = WRITE_SCOPE.get(role)
        if scope is None:
            return {"allowed": False, "reason": f"vai tro khong ton tai: {role}",
                    "resolved": str(target)}

        if scope == "none":
            return {"allowed": False, "reason": f"{role} la read-only, khong co quyen ghi",
                    "resolved": str(target)}

        if scope == "draft":
            if not _is_inside(target, self.draft_dir):
                return {"allowed": False,
                        "reason": f"coder chi duoc ghi trong {self.draft_dir}",
                        "resolved": str(target)}
            return {"allowed": True, "reason": "trong .agents/draft", "resolved": str(target)}

        # scope == "project" (Lead)
        if not _is_inside(target, self.root):
            return {"allowed": False, "reason": "ngoai project root", "resolved": str(target)}
        if _is_inside(target, self.agents_dir):
            return {"allowed": False,
                    "reason": ".agents/ la vung quan tri, Lead khong ghi truc tiep",
                    "resolved": str(target)}
        if is_sensitive(target):
            return {"allowed": False, "reason": "file nhay cam (secret/key/credential)",
                    "resolved": str(target)}
        return {"allowed": True, "reason": "file du an (qua safe_writer)",
                "resolved": str(target)}

    def assert_can_write(self, role: str, path) -> Path:
        decision = self.check_write(role, path)
        if not decision["allowed"]:
            self._record_denial(role, "write", path, decision["reason"])
            raise PermissionDenied(role, "write", path, decision["reason"])
        return Path(decision["resolved"])

    def assert_can_read(self, role: str, path) -> Path:
        target = Path(path)
        if not _is_inside(target, self.root):
            self._record_denial(role, "read", path, "ngoai project root")
            raise PermissionDenied(role, "read", path, "ngoai project root")
        if is_sensitive(target):
            self._record_denial(role, "read", path, "file nhay cam")
            raise PermissionDenied(role, "read", path, "file nhay cam (secret/key)")
        return target

    def _record_denial(self, role: str, action: str, path, reason: str) -> None:
        if not self.log:
            return
        ledger.append_event(
            "permission.denied",
            run_id=self.run_id,
            result={"status": "blocked", "error": reason},
            data={"role": role, "action": action, "path": str(path)},
            root=self.root,
        )


class FindingsSink:
    """Cua DUY NHAT de Reviewer ghi: ledger. Khong co ham nao cham file.

    Reviewer duoc trao object nay thay vi mot duong dan hay mot ham `write`.
    Nho vay "read-only" duoc bao dam bang *kha nang*, khong phai bang loi hua.
    """

    def __init__(self, root, *, run_id: str | None = None, log: bool = True) -> None:
        self.root = Path(root)
        self.run_id = run_id
        self.log = log
        self.written: list[dict] = []

    def write(self, findings: dict, *, event_type: str = "reviewer.findings") -> dict:
        entry = {
            "verdict": findings.get("verdict"),
            "level": findings.get("level"),
            "task_id": findings.get("task_id"),
            "reasons": list(findings.get("reasons") or [])[:10],
        }
        for key in ("files", "skipped", "per_role"):
            if key in findings:
                entry[key] = findings[key]
        self.written.append(entry)
        if self.log:
            ledger.append_event(
                event_type,
                run_id=self.run_id,
                result={"status": "pass" if entry["verdict"] == "approve" else "fail",
                        "error": None if entry["verdict"] == "approve"
                        else "; ".join(entry["reasons"])[:300]},
                data=entry,
                root=self.root,
            )
        return entry

    # Minh hoa co y: moi thao tac ghi file deu bi tu choi.
    def write_file(self, *args, **kwargs):
        raise PermissionDenied(ROLE_REVIEWER, "write_file", args[0] if args else "?",
                               "reviewer la read-only: chi duoc ghi findings vao ledger")


def capabilities() -> dict:
    """Bang mo ta quyen, dung cho tai lieu va ledger."""
    return {
        role: {
            "write": WRITE_SCOPE[role],
            "read": "project (tru secret)" if role != ROLE_LEAD else "tat ca",
        }
        for role in ROLES
    }
