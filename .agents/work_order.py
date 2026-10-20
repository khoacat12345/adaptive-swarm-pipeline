#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Work Order Schema & Validator (Task 1.2).

Mot Work Order la hop dong giao viec giua Lead va Coder/Reviewer. Dung 5 truong:

  objective            : phai lam gi (1-3 cau, khong mo ho)
  boundaries           : duoc phep va KHONG duoc phep cham vao dau
  input_context        : file/duong dan/doan code lien quan
  acceptance_criteria  : the nao la dung
  verify_command       : lenh CHAY THAT de chung minh dung

Quy tac cung: **thieu `verify_command` -> tu choi ngay**. Khong co verify thi
"xong" chi la loi hua; he thong nay lay may that lam chuan.

Validator con kiem tra `verify_command` co qua duoc guard cua SystemBot khong
(som hon 1 vong lap: fail fast thay vi phat hien sau khi da goi LLM).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import system_bot

# Phase 3: tran 4 vong de chinh sach session (vong 3+ phai mo session moi) co
# hieu luc. Mac dinh cua Work Order van la 2; ngan sach request (8) van chan.
MAX_ROUNDS_HARD_CAP = 4
REQUIRED_FIELDS = ("objective", "boundaries", "input_context",
                   "acceptance_criteria", "verify_command")
FIELD_LABEL = {
    "objective": "MUC TIEU (lam gi)",
    "boundaries": "RANH GIOI (duoc/khong duoc cham)",
    "input_context": "NGU CANH (file, doan code lien quan)",
    "acceptance_criteria": "TIEU CHI NGHIEM THU (the nao la dung)",
    "verify_command": "LENH VERIFY (chay that de chung minh)",
}
VALID_LEVELS = (0, 1, 2)


class WorkOrderRejected(Exception):
    """Work Order khong hop le -> khong duoc phep bat dau."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("Work Order bi tu choi: " + "; ".join(self.problems))


@dataclass
class WorkOrder:
    objective: str = ""
    boundaries: str = ""
    input_context: str = ""
    acceptance_criteria: str = ""
    verify_command: str = ""
    task_id: str = ""
    title: str = ""
    level: int = 2
    target_files: list[str] = field(default_factory=list)
    sandbox_files: list[str] = field(default_factory=list)
    max_rounds: int = 2
    notes: str = ""

    # -- validate ----------------------------------------------------------
    def problems(self) -> list[str]:
        found: list[str] = []

        for name in REQUIRED_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                found.append(f"thieu truong bat buoc '{name}' ({FIELD_LABEL[name]})")

        if not self.target_files:
            found.append("thieu 'target_files' (khong biet sua file nao)")
        for relative in self.target_files:
            if not _is_safe_relative(relative):
                found.append(f"target_files khong hop le (phai tuong doi, khong '..'): {relative}")

        if self.level not in VALID_LEVELS:
            found.append(f"level phai thuoc {VALID_LEVELS}, nhan duoc {self.level!r}")

        if not isinstance(self.max_rounds, int) or not 1 <= self.max_rounds <= MAX_ROUNDS_HARD_CAP:
            found.append(f"max_rounds phai trong 1..{MAX_ROUNDS_HARD_CAP} (circuit breaker), "
                         f"nhan duoc {self.max_rounds!r}")

        # verify_command la dieu kien TUYET DOI + phai chay duoc qua guard
        if self.verify_command.strip():
            guard = system_bot.check_command(self.verify_command)
            if not guard["allowed"]:
                found.append(
                    f"verify_command bi SystemBot chan ({guard['category']}): {guard['reason']}"
                )
        return found

    def validate(self) -> "WorkOrder":
        found = self.problems()
        if found:
            raise WorkOrderRejected(found)
        return self

    def is_valid(self) -> bool:
        return not self.problems()

    # -- tieu thu ----------------------------------------------------------
    def render_verify_command(self, file_path=None) -> str:
        """Thay placeholder `{file}` (dung nhu profile.json cua Phase 0)."""
        command = self.verify_command
        if "{file}" in command:
            if file_path is None:
                raise WorkOrderRejected(["verify_command co {file} nhung khong truyen file_path"])
            command = command.replace("{file}", str(file_path))
        return command

    def to_prompt(self, *, current_file: str | None = None, content: str = "",
                  feedback: str | None = None) -> str:
        """Prompt giao viec cho Coder. Giu gon de khong dot token vo ich."""
        parts = [
            f"# WORK ORDER {self.task_id or '(khong ma)'} - Level {self.level}",
            "",
            f"## {FIELD_LABEL['objective']}",
            self.objective.strip(),
            "",
            f"## {FIELD_LABEL['boundaries']}",
            self.boundaries.strip(),
            "",
            f"## {FIELD_LABEL['input_context']}",
            self.input_context.strip(),
            "",
            f"## {FIELD_LABEL['acceptance_criteria']}",
            self.acceptance_criteria.strip(),
            "",
            f"## {FIELD_LABEL['verify_command']}",
            "```",
            self.verify_command.strip(),
            "```",
            "",
            "## DINH DANG TRA VE (bat buoc)",
            "Chi tra ve 1 object JSON, khong giai thich ngoai JSON:",
            '{"format": "whole"|"replace"|"symbol", ...}',
            '- whole  : {"format":"whole","content":"<toan bo file moi>"}',
            '- replace: {"format":"replace","edits":[{"search":"...","replace":"..."}]}',
            '- symbol : {"format":"symbol","edits":[{"name":"<ten ham/class>","replace":"<code moi>"}]}',
            f"- File <= 150 dong moi duoc dung 'whole'. File dai hon phai dung replace/symbol.",
        ]
        if current_file:
            parts += ["", f"## FILE HIEN TAI: {current_file}", "```", content.rstrip(), "```"]
        if feedback:
            parts += ["", "## LAN TRUOC THAT BAI - SUA THEO DAY", feedback.strip()]
        return "\n".join(parts)

    def prompt_tokens(self, **kwargs) -> int:
        return estimate_tokens(self.to_prompt(**kwargs))

    # -- serialize ---------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "level": self.level,
            "objective": self.objective,
            "boundaries": self.boundaries,
            "input_context": self.input_context,
            "acceptance_criteria": self.acceptance_criteria,
            "verify_command": self.verify_command,
            "target_files": list(self.target_files),
            "sandbox_files": list(self.sandbox_files),
            "max_rounds": self.max_rounds,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkOrder":
        known = {key: data[key] for key in cls.__dataclass_fields__ if key in data}
        return cls(**known)

    # -- hien thi ----------------------------------------------------------
    def to_markdown(self) -> str:
        lines = [f"### Work Order {self.task_id} - {self.title or self.objective[:60]}",
                 f"- level: {self.level} | max_rounds: {self.max_rounds}",
                 f"- target: {', '.join(self.target_files)}"]
        for name in REQUIRED_FIELDS:
            lines.append(f"- **{name}**: {getattr(self, name).strip()}")
        return "\n".join(lines)


def create(**kwargs) -> WorkOrder:
    """Tao va validate ngay. Raise WorkOrderRejected neu thieu verify_command."""
    return WorkOrder(**kwargs).validate()


def _is_safe_relative(path) -> bool:
    text = str(path).replace("\\", "/")
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return False
    return ".." not in Path(text).parts


def estimate_tokens(text: str) -> int:
    """Uoc luong tho (4 ky tu ~ 1 token). Du de kiem tra nguong 200 token."""
    return max(1, len(text) // 4)
