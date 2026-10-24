#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lop worker cho Recipe C: Coder sinh patch, Reviewer doc diff.

Vi sao phai tach lop nay: mot script Python KHONG the goi tool `subagent` cua
OpenCode. Neu nhet thang loi goi model vao recipe thi recipe khong test duoc va
cung khong doi model duoc. Nen:

  Coder    : ScriptedCoder (tat dinh, do engine) | OpenCodeCoder (goi CLI that)
  Reviewer : HeuristicReviewer (luat, 0 request)  | OpenCodeReviewer (LLM)

Cung mot recipe_c.py chay duoc voi ca 4 to hop. Smoke test dung ban scripted de
do *engine*; production dung ban OpenCode.

Hop dong patch (JSON):
  {"format": "whole",   "content": "<ca file>"}
  {"format": "replace", "edits": [{"search": "...", "replace": "..."}]}
  {"format": "symbol",  "edits": [{"name": "ham_x", "replace": "<code moi>"}]}
"""

from __future__ import annotations

import codecs
import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import secret_scan

_NPM_BIN = os.path.join(os.environ.get("APPDATA") or "", "npm")
_SHIM = os.path.join(_NPM_BIN, "opencode.cmd")


def unwrap_cmd_shim(binary: str) -> str:
    """Doi `opencode.cmd` (batch shim cua npm) sang binary that.

    Tren Windows, `subprocess` chay file `.cmd` qua `cmd.exe /c`, ma `cmd.exe` cat
    argv ngay tai newline dau tien. Prompt cua planner/coder luon nhieu dong, nen
    di qua shim thi model chi nhan duoc DONG DAU (goal, file tree, schema deu mat).
    Goi thang `opencode.exe` thi argv duoc giu nguyen tung byte.
    """
    path = Path(binary)
    if path.suffix.lower() not in (".cmd", ".bat"):
        return str(binary)
    for candidate in (path.parent / "node_modules" / "@opencode" / "cli" / "bin" / "opencode.exe",
                      path.parent / "node_modules" / "@opencode" / "cli" / "bin" / "opencode"):
        if candidate.is_file():
            return str(candidate)
    return str(binary)


def resolve_binary(binary: str | None = None) -> str:
    """Binary uu tien: tham so -> $OPENCODE_BIN -> PATH -> shim npm (da unwrap)."""
    chosen = (binary or os.environ.get("OPENCODE_BIN")
              or shutil.which("opencode") or _SHIM)
    return unwrap_cmd_shim(chosen)


DEFAULT_OPENCODE_BIN = unwrap_cmd_shim(_SHIM)

# 4 reviewer cho wave (Level 2/3, opt-in). Moi role 1 request.
WAVE_ROLES = {
    "security": "opencode-go/glm-5.3",
    "qa": "opencode-go/claude-haiku-5-5",
    "perf": "opencode/deepseek-v4.1-flash-free",
    "docs": "opencode-go/gpt-6-luna",
}


def _same_text(left: str, right: str) -> bool:
    """So sanh noi dung bo qua kieu xuong dong (CRLF vs LF) va khoang trang 2 dau."""
    normalize = lambda text: (text or "").replace("\r\n", "\n").strip()
    return normalize(left) == normalize(right)

DANGEROUS_ADDED = (
    (r"\beval\s*\(", "eval() tren du lieu moi"),
    (r"\bexec\s*\(", "exec() tren du lieu moi"),
    (r"\bos\.system\s*\(", "os.system() (command injection)"),
    (r"shell\s*=\s*True", "subprocess shell=True"),
    (r"\bpickle\.loads?\s*\(", "pickle (RCE khi doc du lieu ngoai)"),
    (r"__import__\s*\(", "__import__ dong"),
    (r"\bverify\s*=\s*False\b", "tat verify SSL"),
)
SECRET_ADDED = (
    r"(?i)\b(password|passwd|api_key|apikey|secret|token)\s*=\s*[\"'][^\"']{8,}[\"']",
)


# --------------------------------------------------------------------------
# Parse patch
# --------------------------------------------------------------------------

def _json_candidates(text: str):
    """Quet cac object JSON can bang ngoac trong 1 doan text co nhieu rac."""
    depth, start, in_string, escape = 0, None, False, False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth:
                depth -= 1
                if depth == 0 and start is not None:
                    yield text[start:index + 1]
                    start = None


class WorkerError(RuntimeError):
    """Loi khi goi model, co phan loai de ben goi quyet dinh dung hay bo."""

    def __init__(self, message: str, kind: str = "error") -> None:
        self.kind = kind
        super().__init__(f"[{kind}] {message}")


def classify_cli_error(blob: str) -> str:
    """Phan loai loi CLI/model: quota, timeout, model khong ton tai, con lai la cli."""
    text = (blob or "").lower()
    if any(key in text for key in ("insufficient", "funds", "quota", "402",
                                   "rate limit", "too many requests", "credit")):
        return "quota"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "model not found" in text or "unknown model" in text or "no such model" in text:
        return "model"
    return "cli"


def extract_text_events(raw: str) -> tuple[str, dict]:
    """Doc output `opencode run --format json` (JSON Lines) -> (text, usage)."""
    texts: list[str] = []
    usage: dict = {}
    saw_event = False
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        saw_event = True
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        if event.get("type") == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
        for key in ("tokens", "usage"):
            candidate = event.get(key) or part.get(key)
            if isinstance(candidate, dict):
                usage.update(candidate)
        if isinstance(part.get("cost"), (int, float)):
            usage["cost"] = part["cost"]
    if texts:
        return "\n".join(texts), usage
    # CLI khong ho tro --format json hoac khong co event -> dung stdout tho
    return ("" if saw_event else (raw or "")), usage


def parse_patch_json(text: str) -> dict:
    """Lay object JSON dau tien trong output cua model (bo qua ```json ... ```)."""
    if isinstance(text, dict):
        return normalize_patch(text)
    for candidate in _json_candidates(text or ""):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return normalize_patch(data)
    raise ValueError("khong tim thay object JSON hop le trong output cua model")


def parse_json_object(text: str) -> dict:
    """Lay object JSON dau tien (KHONG chuan hoa patch) - dung cho reviewer."""
    if isinstance(text, dict):
        return text
    for candidate in _json_candidates(text or ""):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("khong tim thay object JSON hop le trong output cua model")


def normalize_patch(patch: dict) -> dict:
    """Chuan hoa + suy ra 'format' neu model quen khai bao."""
    data = dict(patch)
    fmt = str(data.get("format") or "").strip().lower()
    if fmt not in ("whole", "replace", "symbol"):
        if "content" in data:
            fmt = "whole"
        elif data.get("edits"):
            edits = data["edits"]
            fmt = "symbol" if any("name" in edit for edit in edits) else "replace"
        else:
            raise ValueError(f"patch khong ro dinh dang: {sorted(data)}")
    data["format"] = fmt

    if fmt == "whole":
        if not isinstance(data.get("content"), str):
            raise ValueError("patch 'whole' thieu 'content' dang chuoi")
    else:
        edits = data.get("edits")
        if not isinstance(edits, list) or not edits:
            raise ValueError(f"patch '{fmt}' thieu 'edits'")
        required = ("name", "replace") if fmt == "symbol" else ("search", "replace")
        for edit in edits:
            if not isinstance(edit, dict):
                raise ValueError("moi edit phai la object")
            for key in required:
                if not isinstance(edit.get(key), str):
                    raise ValueError(f"edit thieu truong '{key}' dang chuoi")
    return data


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------

def unified_diff(old: str, new: str, *, path: str = "file") -> str:
    lines = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}", n=3,
    )
    return "".join(lines)


def changed_lines(diff: str) -> dict:
    added = [line[1:] for line in diff.splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    removed = [line[1:] for line in diff.splitlines()
               if line.startswith("-") and not line.startswith("---")]
    return {"added": added, "removed": removed}


def read_source_file(path) -> tuple[str, str]:
    """Doc file nguon: giu nguyen kieu xuong dong, do encoding thay vi doan.

    `Path.read_text()` dung universal newlines (CRLF -> LF) va mac dinh utf-8;
    ca hai deu lam hong file that. Doc bytes roi giai ma tuong minh la cach duy
    nhat giu nguyen byte goc khi khong sua gi.
    """
    raw = Path(path).read_bytes()
    if b"\x00" in raw[:4096]:
        raise ValueError("file co byte NUL -> co ve la file binary, tu choi sua")
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", raw, 0, 1, "khong giai ma duoc file nguon")


def run_opencode_cli(binary: str, model: str, prompt: str, *, agent: str | None = None,
                     cwd=None, timeout: int = 300) -> tuple[str, dict]:
    """Chay `opencode run` mot lan, tra ve (text, usage).

    Dung `--format json` de khong dinh spinner/ANSI, va truyen prompt qua argv
    (khong dung shell=True) de prompt khong bi vo.
    """
    command = [binary, "run", "--format", "json"]
    if agent:
        command += ["--agent", agent]
    command += ["-m", model, prompt]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkerError(f"qua thoi gian cho ({timeout}s)", kind="timeout") from exc
    if completed.returncode != 0:
        blob = f"{completed.stderr or ''}\n{completed.stdout or ''}"
        raise WorkerError(f"exit {completed.returncode}: {blob[-400:]}",
                          kind=classify_cli_error(blob))
    return extract_text_events(completed.stdout or "")


# --------------------------------------------------------------------------
# Coder
# --------------------------------------------------------------------------



def _adapter_complete(adapter, prompt: str) -> tuple[str, dict]:
    """Call a provider without knowing which provider it is."""
    reply = adapter.complete([{"role": "user", "content": prompt}])
    return str(getattr(reply, "text", "") or ""), dict(getattr(reply, "usage", {}) or {})

class Coder:
    name = "coder"
    kind = "abstract"

    def __init__(self) -> None:
        self.calls = 0
        self.errors: list[str] = []
        self.error_kinds: list[str] = []
        self.project_root: Path | None = None
        self.cwd: str | None = None
        self.usage: dict = {}
        # Kinh nghiem tu memory.py (Phase 4), recipe tu gan truoc khi chay.
        self.hints: list[str] = []
        # recipe co the dat tran rieng de giu request cho reviewer wave.
        self.limit_check = None

    def bind_workspace(self, project_root, sandbox=None) -> None:
        """Cho worker biet du an that va thu muc tam duoc phep lam viec."""
        self.project_root = Path(project_root)
        if sandbox is not None:
            self.cwd = str(sandbox)

    def _hints_block(self) -> str:
        if not self.hints:
            return ""
        lines = "\n".join(f"- {hint}" for hint in self.hints[:5])
        return f"\nKINH NGHIEM TU CAC LAN CHAY TRUOC (memory):\n{lines}\n"

    def patch(self, work_order, *, current_content: str, feedback: str | None,
              round_index: int) -> dict:
        raise NotImplementedError

    def patch_set(self, work_order, *, current_files: dict, feedback: str | None,
                  round_index: int) -> dict:
        """Mac dinh: giao tung file mot (N request). Coder nao gop duoc thi override."""
        patches: dict[str, dict] = {}
        for relative, content in current_files.items():
            patches[relative] = self.patch(
                work_order.for_file(relative), current_content=content,
                feedback=feedback, round_index=round_index,
            )
        return patches

    def stats(self) -> dict:
        return {"name": self.name, "kind": self.kind, "calls": self.calls,
                "errors": list(self.errors), "usage": dict(self.usage)}


class ScriptedCoder(Coder):
    """Coder tat dinh cho smoke test.

    `script[task_id]` la danh sach patch theo tung vong. Het kich ban -> tra ve
    lai patch cuoi (mo phong model "van khong sua duoc").
    """

    kind = "scripted"

    def __init__(self, script: dict[str, list[dict]], name: str = "scripted-coder") -> None:
        super().__init__()
        self.name = name
        self.script = script

    def patch(self, work_order, *, current_content: str, feedback: str | None,
              round_index: int) -> dict:
        self.calls += 1
        rounds = self.script.get(work_order.task_id) or []
        if not rounds:
            self.errors.append(f"khong co kich ban cho task {work_order.task_id}")
            raise KeyError(f"khong co kich ban cho task {work_order.task_id}")
        index = min(round_index - 1, len(rounds) - 1)
        return normalize_patch(rounds[index])

    def patch_set(self, work_order, *, current_files: dict, feedback: str | None,
                  round_index: int) -> dict:
        """Kich ban da file: moi vong la dict {file: patch} (hoac 1 patch don)."""
        self.calls += 1
        rounds = self.script.get(work_order.task_id) or []
        if not rounds:
            self.errors.append(f"khong co kich ban cho task {work_order.task_id}")
            raise KeyError(f"khong co kich ban cho task {work_order.task_id}")
        index = min(round_index - 1, len(rounds) - 1)
        entry = rounds[index]
        if isinstance(entry, dict) and "format" in entry:
            first = next(iter(current_files))
            return {first: normalize_patch(entry)}
        patches: dict[str, dict] = {}
        for relative in current_files:
            raw = entry.get(relative) if isinstance(entry, dict) else None
            if raw is None:
                raise WorkerError(f"kich ban thieu patch cho {relative}", kind="patch")
            patches[relative] = normalize_patch(raw)
        return patches


class OpenCodeCoder(Coder):
    """Goi OpenCode CLI that de sinh patch.

    - `--format json`: output la JSON Lines, khong dinh spinner/ANSI.
    - `cwd` duoc recipe_c tro vao thu muc tam rieng cua run, nen model khong doc
      va khong ghi duoc file du an (noi dung file da nam trong prompt).
    - Khong dung `shell=True` de tranh vo prompt.
    """

    kind = "opencode-cli"

    def __init__(self, model: str, *, binary: str | None = None, timeout: int = 300,
                 max_content_chars: int = 20_000, runner=None, cwd=None,
                 agent: str | None = "plan", debug_dir=None, adapter=None) -> None:
        super().__init__()
        self.name = f"opencode:{model}"
        self.model = model
        self.binary = resolve_binary(binary)
        self.timeout = timeout
        # Windows gioi han command line ~32k ky tu, ma prompt di qua argv.
        # 20k ky tu (~500 dong code) la nguong an toan; vuot qua thi cat bot va
        # ghi ro trong prompt de model biet.
        self.max_content_chars = max_content_chars
        self.adapter = adapter
        self.runner = runner or self._run_cli
        self.cwd = str(cwd) if cwd else None
        # Agent `plan` la read-only (deny edit) -> model khong sua file duoc.
        # Day la lop chan thu 1; lop 2 la snapshot/restore trong recipe_c.
        self.agent = agent
        self.debug_dir = Path(debug_dir) if debug_dir else None

    def _run_cli(self, prompt: str) -> tuple[str, dict]:
        if self.adapter is not None:
            return _adapter_complete(self.adapter, prompt)
        return run_opencode_cli(self.binary, self.model, prompt, agent=self.agent,
                                cwd=self.cwd, timeout=self.timeout)

    def patch(self, work_order, *, current_content: str, feedback: str | None,
              round_index: int) -> dict:
        if self.limit_check is not None and not self.limit_check():
            raise WorkerError("coder request budget reserved for reviewers", kind="budget")
        self.calls += 1
        content = current_content
        if len(content) > self.max_content_chars:
            content = content[: self.max_content_chars] + "\n# ... (cat bot) ...\n"
        prompt = work_order.to_prompt(
            current_file=work_order.target_files[0] if work_order.target_files else "",
            content=content, feedback=feedback,
        )
        prompt += ("\n\nQUY TAC OUTPUT BAT BUOC: chi tra ve DUY NHAT mot object JSON hop le, "
                   "khong markdown, khong giai thich, khong text thua. "
                   "Dung {\"format\":\"whole\",\"content\":\"<toan bo file moi>\"} "
                   "hoac {\"format\":\"replace\",\"edits\":[{\"search\":\"...\",\"replace\":\"...\"}]}.")
        prompt += self._hints_block()
        prompt, _findings = secret_scan.scan_text(prompt, filename="prompt")
        try:
            raw = self.runner(prompt)
            text, usage = raw if isinstance(raw, tuple) else (raw, {})
            if isinstance(usage, dict) and usage:
                self.usage.update(usage)
            if self.debug_dir is not None:
                self.debug_dir.mkdir(parents=True, exist_ok=True)
                stem = f"{self.calls:02d}_{work_order.task_id or 'task'}"
                (self.debug_dir / f"{stem}.prompt.txt").write_text(prompt, encoding="utf-8")
                (self.debug_dir / f"{stem}.output.txt").write_text(
                    text if isinstance(text, str) else str(text), encoding="utf-8")
            return parse_patch_json(text)
        except Exception as exc:                            # noqa: BLE001
            kind = getattr(exc, "kind", "parse")
            self.errors.append(f"[{kind}] {str(exc)[:300]}")
            self.error_kinds.append(kind)
            raise


AGENTIC_PROMPT = (
    "Ban la coder duoc giao sua DUNG MOT file trong thu muc lam viec hien tai.\n"
    "File can sua: {target}\n"
    "\n"
    "MUC TIEU: {objective}\n"
    "RANG BUOC: {boundaries}\n"
    "TIEU CHI NGHIEM THU: {criteria}\n"
    "Lenh verify: {verify}\n"
    "\n"
    "Hay sua truc tiep noi dung file. KHONG can chay lenh verify; he thong se tu verify sau do.\n"
    "Chi sua file tren; khong tao file moi, khong sua file test.\n"
    "Toi da 2 tool call: doc file neu can, roi sua file. Khi xong, tra loi ngan gon (1-3 cau).\n"
)

# Level 3: mot lan goi model, sua NHIEU file trong cung thu muc tam. Cac file
# phai dong bo voi nhau (doi ten ham o file A thi noi goi o file B cung doi).
AGENTIC_PROMPT_MULTI = (
    "Ban la coder duoc giao sua NHIEU file trong thu muc lam viec hien tai.\n"
    "Cac file can sua (dong bo voi nhau, khong duoc sua le):\n"
    "{files}\n"
    "\n"
    "MUC TIEU: {objective}\n"
    "RANG BUOC: {boundaries}\n"
    "TIEU CHI NGHIEM THU: {criteria}\n"
    "Lenh verify (chay cho CA du an): {verify}\n"
    "\n"
    "Hay sua truc tiep noi dung cac file. KHONG can chay lenh verify; he thong se tu verify sau do.\n"
    "Chi sua cac file liet ke tren; khong tao file moi, khong sua file test.\n"
    "Neu mot file khong can doi, hay de nguyen (he thong giu ban goc).\n"
    "Toi da 2 tool call: doc file neu can, roi sua file. Khi xong, tra loi ngan gon (1-3 cau).\n"
)


class SessionPolicy:
    """Chinh sach phien lam viec cua Coder (Task 3.4).

    Vong 1..`reuse_rounds`: giu nguyen phien (model nhin lai chinh ban sua cua no,
    giong mot session lien tuc).
    Tu vong `reuse_rounds + 1`: cat phien, mo moi, kem brief ngan gom ban nhap
    gan nhat + loi chua xu ly -> chong phinh context window va ao giac.
    """

    def __init__(self, reuse_rounds: int = 2, brief_chars: int = 3000) -> None:
        self.reuse_rounds = max(1, int(reuse_rounds))
        self.brief_chars = max(200, int(brief_chars))

    def reuse(self, round_index: int) -> bool:
        return int(round_index) <= self.reuse_rounds

    def is_reset(self, round_index: int) -> bool:
        return int(round_index) > self.reuse_rounds

    def brief(self, *, target: str, draft: str = "", feedback: str | None = None) -> str:
        draft = (draft or "").strip()
        if len(draft) > self.brief_chars:
            draft = draft[: self.brief_chars] + "\n# ... (cat bot) ..."
        parts = [
            "PHIEN MOI (context cu da bi cat de tranh phinh to). TOM TAT NGAN:",
            f"- File dich: {target}",
            "- Ban nhap gan nhat (co the con loi, day la diem xuat phat):",
            "```",
            draft or "(chua co ban nhap)",
            "```",
        ]
        if feedback:
            parts += ["- Loi chua xu ly:", str(feedback).strip()[:1200]]
        parts.append("Hay sua tiep tu ban nhap tren, khong lam lai tu dau.")
        return "\n".join(parts)


class AgenticCoder(Coder):
    """Coder kieu agent: de model sua file truc tiep trong mot ban sao tam.

    Vi sao can lop nay: qua `opencode run`, cac model hanh xu nhu agent — chung
    goi tool edit/shell de sua file chu khong tra ve JSON. Ep tra JSON thi vua
    mat cong suat vua de ra prose (da do bang thuc nghiem). Nen:

      1. copy cac file cua task sang thu muc tam (model tha ho sua, ke ca chay test)
      2. doc lai file sau khi model sua -> dua ve dang patch `whole`
      3. phan con lai giu nguyen hop dong cu: ladder -> verify ban nhap ->
         reviewer -> safe_writer (Lead van la nguoi ghi file duy nhat)

    Nho vay model duoc dung tool (loi the cua no) ma bat bien "chi Lead ghi file"
    van dung: moi thay doi deu phai di qua kiem chung doc lap.
    """

    kind = "opencode-agentic"

    def __init__(self, model: str, *, binary: str | None = None, timeout: int = 300,
                 agent: str = "build", debug_dir=None, runner=None,
                 session_policy: SessionPolicy | None = None, adapter=None) -> None:
        super().__init__()
        self.name = f"agentic:{model}"
        self.model = model
        self.binary = resolve_binary(binary)
        self.timeout = timeout
        self.agent = agent
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self.adapter = adapter
        self.runner = runner or self._run_cli
        self._active_cwd: str | None = None
        self.session_policy = session_policy or SessionPolicy()
        self.session_events: list[dict] = []
        self.secrets_blocked: list[dict] = []
        self._last_attempt: str = ""

    def _run_cli(self, prompt: str) -> tuple[str, dict]:
        if self.adapter is not None:
            if hasattr(self.adapter, "cwd"):
                self.adapter.cwd = self._active_cwd or self.cwd
            return _adapter_complete(self.adapter, prompt)
        return run_opencode_cli(self.binary, self.model, prompt, agent=self.agent,
                                cwd=self._active_cwd or self.cwd, timeout=self.timeout)

    def _copy_project_files(self, work_order, round_dir: Path, targets) -> None:
        """Copy file cua task sang thu muc tam, chan secret truoc khi model doc."""
        targets = list(targets)
        wanted = set(targets)
        for relative in work_order.sandbox_files or targets:
            source = (self.project_root or Path.cwd()) / relative
            project_root = (self.project_root or Path.cwd()).resolve()
            try:
                source.resolve().relative_to(project_root)
            except (ValueError, OSError):
                self.secrets_blocked.append({"file": relative, "kind": "path_escape"})
                continue
            if not source.exists():
                continue
            reason = secret_scan.is_sensitive_path(source)
            if reason:
                self.secrets_blocked.append({"file": relative, "kind": "sensitive_path",
                                             "detail": reason})
                continue
            findings = secret_scan.scan_file(source)
            if findings and relative in wanted:
                raise WorkerError("file dich chua secret, khong duoc gui ra model",
                                  kind="secret")
            if findings:
                self.secrets_blocked.append({"file": relative, "kind": "content",
                                             "count": len(findings),
                                             "preview": secret_scan.summarize(findings)})
                continue
            destination = round_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def patch(self, work_order, *, current_content: str, feedback: str | None,
              round_index: int) -> dict:
        target = work_order.target_files[0]
        changed = self._patch_files(work_order, {target: current_content},
                                    feedback=feedback, round_index=round_index)
        content = changed.get(target)
        if content is None:
            raise WorkerError("model khong sua gi trong file dich", kind="noop")
        return {"format": "whole", "content": content}

    def patch_set(self, work_order, *, current_files: dict, feedback: str | None,
                  round_index: int) -> dict:
        """Level 3: MOT lan goi model sua TAT CA file trong cung thu muc tam."""
        changed = self._patch_files(work_order, dict(current_files),
                                    feedback=feedback, round_index=round_index)
        return {relative: ({"format": "whole", "content": text}
                           if text is not None else None)
                for relative, text in changed.items()}

    def _patch_files(self, work_order, files: dict, *, feedback: str | None,
                     round_index: int) -> dict:
        if self.limit_check is not None and not self.limit_check():
            raise WorkerError("coder request budget reserved for reviewers", kind="budget")
        self.calls += 1
        if not self.cwd:
            raise WorkerError("chua bind_workspace -> khong co thu muc tam", kind="config")
        targets = list(files)
        round_dir = Path(self.cwd) / f"r{round_index}"
        previous_dir = Path(self.cwd) / f"r{round_index - 1}"
        shutil.rmtree(round_dir, ignore_errors=True)

        # Session policy: vong 1..2 noi tiep phien (xuat phat tu ban sua truoc);
        # tu vong 3 cat phien, xuat phat lai tu ban goc + brief.
        reuse = (round_index > 1 and self.session_policy.reuse(round_index)
                 and previous_dir.exists())
        if reuse:
            shutil.copytree(previous_dir, round_dir)
        else:
            round_dir.mkdir(parents=True, exist_ok=True)
            self._copy_project_files(work_order, round_dir, targets)
        fresh_session = self.session_policy.is_reset(round_index)
        self.session_events.append({"round": round_index,
                                    "mode": "resume" if reuse else "fresh",
                                    "context_reset": fresh_session,
                                    "files": targets})

        for relative, content in files.items():
            destination = round_dir / relative
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content, encoding="utf-8", newline="")

        if len(targets) == 1:
            target = targets[0]
            prompt = AGENTIC_PROMPT.format(
                target=target, objective=work_order.objective,
                boundaries=work_order.boundaries, criteria=work_order.acceptance_criteria,
                verify=work_order.render_verify_command(target),
            )
        else:
            listing = "\n".join(f"- {item}" for item in targets)
            prompt = AGENTIC_PROMPT_MULTI.format(
                files=listing, objective=work_order.objective,
                boundaries=work_order.boundaries, criteria=work_order.acceptance_criteria,
                verify=work_order.render_verify_command(targets[0]),
            )
        prompt += self._hints_block()
        if fresh_session:
            prompt += "\n" + self.session_policy.brief(
                target=", ".join(targets),
                draft=self._last_attempt or "\n\n".join(files.values()),
                feedback=feedback,
            )
        if feedback:
            prompt += f"\nLAN TRUOC THAT BAI, SUA THEO PHAN HOI NAY:\n{feedback}\n"
        prompt, prompt_findings = secret_scan.scan_text(prompt, filename="prompt")
        if prompt_findings:
            self.secrets_blocked.append({"file": "prompt", "kind": "content",
                                         "count": len(prompt_findings),
                                         "preview": secret_scan.summarize(prompt_findings)})

        stem = f"{self.calls:02d}_{work_order.task_id or 'task'}"
        self._active_cwd = str(round_dir)
        try:
            text, usage = self.runner(prompt)
            if isinstance(usage, dict) and usage:
                self.usage.update(usage)
            changed: dict = {}
            for relative in targets:
                destination = round_dir / relative
                if not destination.exists():
                    raise WorkerError(f"model da xoa file {relative}", kind="deleted")
                new_text, _encoding = read_source_file(destination)
                changed[relative] = (None if _same_text(new_text, files[relative])
                                     else new_text)
            touched = [item for item in changed.values() if item is not None]
            if not touched:
                raise WorkerError("model khong sua gi trong cac file dich", kind="noop")
            self._last_attempt = touched[0]
            if self.debug_dir is not None:
                self.debug_dir.mkdir(parents=True, exist_ok=True)
                (self.debug_dir / f"{stem}.prompt.txt").write_text(prompt, encoding="utf-8")
                (self.debug_dir / f"{stem}.output.txt").write_text(text or "", encoding="utf-8")
                for relative, new_text in changed.items():
                    if new_text is None:
                        continue
                    name = f"{stem}.{relative.replace('/', '_')}.file.txt"
                    (self.debug_dir / name).write_text(new_text, encoding="utf-8")
            return changed
        except Exception as exc:                            # noqa: BLE001
            kind = getattr(exc, "kind", "patch")
            self.errors.append(f"[{kind}] {str(exc)[:300]}")
            self.error_kinds.append(kind)
            raise


# --------------------------------------------------------------------------
# Reviewer
# --------------------------------------------------------------------------
class Reviewer:
    name = "reviewer"
    kind = "abstract"

    def __init__(self) -> None:
        self.calls = 0

    def review(self, work_order, *, diff: str, new_content: str, old_content: str,
               level: int, sink) -> dict:
        raise NotImplementedError

    def review_set(self, work_order, *, diffs: dict, new_contents: dict,
                   old_contents: dict, level: int, sink) -> dict:
        """Mac dinh: review tung file roi hop nhat (reject neu bat ky file reject)."""
        findings = []
        for relative, diff in diffs.items():
            findings.append(self.review(
                work_order, diff=diff, new_content=new_contents.get(relative, ""),
                old_content=old_contents.get(relative, ""), level=level, sink=sink,
            ))
        reasons: list[str] = []
        for item in findings:
            reasons.extend(item.get("reasons") or [])
        verdict = ("approve" if findings and all(i.get("verdict") == "approve"
                                                for i in findings) else "reject")
        return {"verdict": verdict, "level": level, "task_id": work_order.task_id,
                "reasons": reasons[:10], "reviewer": self.name,
                "files": list(diffs), "per_file": findings}

    def stats(self) -> dict:
        return {"name": self.name, "kind": self.kind, "calls": self.calls}


class HeuristicReviewer(Reviewer):
    """Reviewer bang luat: doc diff, chan cac mau nguy hiem. 0 request.

    Khong thay the duoc con nguoi, nhung chan duoc nhom loi hay gap nhat:
    chen eval/exec, tat verify, hardcode secret, xoa test, xoa qua nhieu code.
    """

    kind = "heuristic"

    def __init__(self, *, max_shrink_ratio: float = 0.5, name: str = "heuristic-reviewer") -> None:
        super().__init__()
        self.name = name
        self.max_shrink_ratio = max_shrink_ratio

    def _evaluate(self, diff: str, new_content: str, old_content: str,
                  level: int) -> tuple[list[str], dict]:
        reasons: list[str] = []
        delta = changed_lines(diff)

        for line in delta["added"]:
            for pattern, label in DANGEROUS_ADDED:
                if re.search(pattern, line):
                    reasons.append(f"chen ma nguy hiem: {label}")
            for pattern in SECRET_ADDED:
                if re.search(pattern, line):
                    reasons.append("hardcode secret/token trong code")
                    break

        if not new_content.strip():
            reasons.append("ket qua rong")
        if len(new_content.splitlines()) < len(old_content.splitlines()) * self.max_shrink_ratio:
            reasons.append("xoa qua nhieu code (>50% so dong)")

        removed_asserts = [line for line in delta["removed"] if "assert" in line]
        if removed_asserts and not any("assert" in line for line in delta["added"]):
            reasons.append("xoa test/assert ma khong them test thay the")
        return reasons, delta

    def review(self, work_order, *, diff: str, new_content: str, old_content: str,
               level: int, sink) -> dict:
        self.calls += 1
        reasons, delta = self._evaluate(diff, new_content, old_content, level)
        if level >= 2 and not diff.strip():
            reasons.append("Level 2 nhung diff rong")

        verdict = "reject" if reasons else "approve"
        findings = {
            "verdict": verdict, "level": level, "task_id": work_order.task_id,
            "reasons": reasons[:10],
            "checked": {"added": len(delta["added"]), "removed": len(delta["removed"])},
            "reviewer": self.name,
        }
        if sink is not None:
            sink.write(findings)
        return findings

    def review_set(self, work_order, *, diffs: dict, new_contents: dict,
                   old_contents: dict, level: int, sink) -> dict:
        """Doc TOAN BO diff cua tap file: mot ket luan cho ca giao dich."""
        self.calls += 1
        reasons: list[str] = []
        added = removed = 0
        per_file: list[dict] = []
        for relative, diff in diffs.items():
            local, delta = self._evaluate(diff, new_contents.get(relative, ""),
                                          old_contents.get(relative, ""), level)
            added += len(delta["added"])
            removed += len(delta["removed"])
            reasons.extend(f"{relative}: {reason}" for reason in local)
            per_file.append({"file": relative, "added": len(delta["added"]),
                             "removed": len(delta["removed"]),
                             "reasons": local[:5]})
        if level >= 2 and not any(str(item).strip() for item in diffs.values()):
            reasons.append("Level 2 nhung diff rong")

        verdict = "reject" if reasons else "approve"
        findings = {
            "verdict": verdict, "level": level, "task_id": work_order.task_id,
            "reasons": reasons[:10], "files": list(diffs),
            "checked": {"added": added, "removed": removed},
            "per_file": per_file, "reviewer": self.name,
        }
        if sink is not None:
            sink.write(findings)
        return findings


class OpenCodeReviewer(Reviewer):
    """Reviewer bang LLM (tuy chon). Chi duoc trao `sink`, khong co quyen ghi file."""

    kind = "opencode-cli"

    def __init__(self, model: str, *, binary: str | None = None, timeout: int = 240,
                 runner=None, raise_on_error: bool = False, adapter=None) -> None:
        super().__init__()
        self.name = f"opencode-review:{model}"
        self.model = model
        self.binary = resolve_binary(binary)
        self.timeout = timeout
        self.adapter = adapter
        self.runner = runner or self._run_cli
        # True: loi ha tang (429/timeout) -> raise de FallbackReviewer chuyen model.
        self.raise_on_error = raise_on_error

    def _run_cli(self, prompt: str) -> str:
        if self.adapter is not None:
            return _adapter_complete(self.adapter, prompt)[0]
        base = Path(tempfile.gettempdir()) / "opencode"
        base.mkdir(parents=True, exist_ok=True)
        workdir = tempfile.mkdtemp(prefix="reviewer_", dir=str(base))
        try:
            text, _usage = run_opencode_cli(self.binary, self.model, prompt,
                                            agent="build", cwd=workdir,
                                            timeout=self.timeout)
            return text
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def review(self, work_order, *, diff: str, new_content: str, old_content: str,
               level: int, sink) -> dict:
        self.calls += 1
        prompt = (
            "Ban la reviewer doc lap, quyen READ-ONLY. Doc diff sau va tim loi that.\n"
            "Chi duyet khi: dung muc tieu, khong chen ma nguy hiem, khong xoa test.\n"
            'Tra ve JSON: {"verdict":"approve"|"reject","reasons":["..."]}\n\n'
            f"MUC TIEU: {work_order.objective}\n"
            f"TIEU CHI: {work_order.acceptance_criteria}\n\n"
            f"```diff\n{diff[:20000]}\n```"
        )
        try:
            raw = self.runner(prompt)
            data = parse_json_object(raw)
            reasons = [str(item) for item in (data.get("reasons") or [])][:10]
            verdict = "approve" if str(data.get("verdict", "")).lower().startswith("approve") else "reject"
        except Exception as exc:                            # noqa: BLE001
            if self.raise_on_error:
                raise
            verdict, reasons = "reject", [f"reviewer loi: {exc}"[:200]]
        findings = {"verdict": verdict, "level": level, "task_id": work_order.task_id,
                    "reasons": reasons, "reviewer": self.name}
        if sink is not None:
            sink.write(findings)
        return findings

    def review_set(self, work_order, *, diffs: dict, new_contents: dict,
                   old_contents: dict, level: int, sink) -> dict:
        """Level 3: mot request, doc ca tap diff de bat loi lech logic giua module."""
        self.calls += 1
        budget = max(2000, 20000 // max(len(diffs), 1))
        blocks = [f"### {relative}\n```diff\n{str(diff)[:budget]}\n```"
                  for relative, diff in diffs.items()]
        prompt = (
            "Ban la reviewer doc lap, quyen READ-ONLY. Day la mot task sua NHIEU file.\n"
            "Doc TOAN BO diff va tim loi that, dac biet loi lech logic giua cac module\n"
            "(doi ten ham o file nay nhung noi goi o file kia khong doi...).\n"
            "Chi duyet khi: dung muc tieu, cac file dong bo, khong chen ma nguy hiem, khong xoa test.\n"
            'Tra ve JSON: {"verdict":"approve"|"reject","reasons":["..."]}\n\n'
            f"MUC TIEU: {work_order.objective}\n"
            f"TIEU CHI: {work_order.acceptance_criteria}\n"
            f"FILES: {', '.join(diffs)}\n\n"
            + "\n\n".join(blocks)
        )
        try:
            raw = self.runner(prompt)
            data = parse_json_object(raw)
            reasons = [str(item) for item in (data.get("reasons") or [])][:10]
            verdict = ("approve" if str(data.get("verdict", "")).lower().startswith("approve")
                       else "reject")
        except Exception as exc:                            # noqa: BLE001
            if self.raise_on_error:
                raise
            verdict, reasons = "reject", [f"reviewer loi: {exc}"[:200]]
        findings = {"verdict": verdict, "level": level, "task_id": work_order.task_id,
                    "reasons": reasons, "files": list(diffs), "reviewer": self.name}
        if sink is not None:
            sink.write(findings)
        return findings


# --------------------------------------------------------------------------
def build_reviewer(kind: str, *, model: str | None = None, adapter=None) -> Reviewer:
    if kind == "none":
        return None
    if kind == "llm":
        if not model:
            raise ValueError("reviewer llm can --model")
        return OpenCodeReviewer(model, adapter=adapter)
    if kind == "meeting":
        if adapter is not None:
            panel = {role: RoleReviewer(role, model, adapter=adapter)
                     for role, model in WAVE_ROLES.items()}
            return MeetingReviewer(panel)
        return MeetingReviewer()
    if kind == "wave":
        roles = None
        raw = os.environ.get("SWARM_WAVE_ROLES")
        if raw:
            roles = {}
            for item in raw.split(","):
                role, _, model = item.partition("=")
                if role.strip() and model.strip():
                    roles[role.strip()] = model.strip()
        if adapter is not None:
            return WaveReviewer(roles or None,
                                factory=lambda role, model: RoleReviewer(role, model, adapter=adapter))
        return WaveReviewer(roles or None)
    return HeuristicReviewer()


def build_coder(kind: str, *, model: str | None = None, script: dict | None = None,
                adapter=None) -> Coder:
    if kind == "scripted":
        if not script:
            raise ValueError("coder scripted can ScriptedCoder(script)")
        return ScriptedCoder(script)
    if kind == "opencode":
        if not model:
            raise ValueError("coder opencode can model")
        return OpenCodeCoder(model, adapter=adapter)
    kwargs = {"adapter": adapter} if adapter is not None else {}
    if kind == "agentic":
        if not model:
            raise ValueError("coder agentic can model")
        return AgenticCoder(model, **kwargs)
    raise ValueError(f"coder khong ho tro: {kind}")

class FallbackReviewer(Reviewer):
    """Chuoi reviewer: thu tung model, ket qua dau tien thang.

    Cung nguyen tac voi FallbackCoder: khong ping truoc; loi/quota moi chuyen
    con ke tiep. Dung cho vai kiem toan khi model chinh bi 429.
    """

    kind = "fallback-reviewer"

    def __init__(self, models, *, factory=None, name: str = "fallback-reviewer") -> None:
        super().__init__()
        self.models = [str(model) for model in models if str(model).strip()]
        if not self.models:
            raise ValueError("FallbackReviewer can it nhat 1 model")
        self.name = f"{name}:" + "->".join(self.models)
        self.factory = factory or (lambda model: OpenCodeReviewer(model, raise_on_error=True))
        self.attempts: list[dict] = []
        self.errors: list[str] = []

    def _run(self, method: str, *args, **kwargs):
        failures: list[str] = []
        for model in self.models:
            child = self.factory(model)
            self.calls += 1
            try:
                result = getattr(child, method)(*args, **kwargs)
            except Exception as exc:                    # noqa: BLE001
                failures.append(f"{model}: {exc}")
                self.errors.append(f"{model}: {str(exc)[:200]}")
                self.attempts.append({"model": model, "status": "fail",
                                      "error": str(exc)[:200]})
                continue
            self.attempts.append({"model": model, "status": "ok"})
            return result
        raise WorkerError("tat ca reviewer that bai: " + " | ".join(failures)[:500],
                          kind="exhausted")

    def review(self, work_order, *, diff, new_content, old_content, level, sink):
        return self._run("review", work_order, diff=diff, new_content=new_content,
                         old_content=old_content, level=level, sink=sink)

    def review_set(self, work_order, *, diffs, new_contents, old_contents, level, sink):
        return self._run("review_set", work_order, diffs=diffs,
                         new_contents=new_contents, old_contents=old_contents,
                         level=level, sink=sink)

    def stats(self) -> dict:
        base = super().stats()
        base.update({"models": list(self.models), "attempts": list(self.attempts)})
        return base


# --------------------------------------------------------------------------
# Wave: 4 reviewer chay song song (chi doc, khong ghi file)
# --------------------------------------------------------------------------
ROLE_PROMPTS = {
    "security": ("auth bypass, injection (SQL/command/path), secret leakage, unsafe "
                 "deserialization, missing permission checks, SSRF"),
    "qa": ("missing edge cases, boundary values, None/empty inputs, error handling, "
           "regressions, tests that do not actually catch the bug"),
    "perf": ("quadratic loops, repeated IO, unnecessary allocations, blocking calls, "
             "N+1 queries, unbounded growth"),
    "docs": ("wrong or missing docstrings, misleading comments, type hints, API docs "
             "that no longer match the code"),
}


class RoleReviewer(Reviewer):
    """Mot reviewer theo vai (security/qa/perf/docs), read-only, 1 request."""

    kind = "role-reviewer"

    def __init__(self, role: str, model: str, *, binary=None, timeout: int = 180,
                 runner=None, adapter=None) -> None:
        super().__init__()
        self.role = str(role)
        self.model = str(model)
        self.name = f"{self.role}:{self.model}"
        self.binary = resolve_binary(binary)
        self.timeout = timeout
        self.adapter = adapter
        self.runner = runner or self._run_cli

    def _run_cli(self, prompt: str) -> str:
        if self.adapter is not None:
            return _adapter_complete(self.adapter, prompt)[0]
        base = Path(tempfile.gettempdir()) / "opencode"
        base.mkdir(parents=True, exist_ok=True)
        workdir = tempfile.mkdtemp(prefix="review_", dir=str(base))
        try:
            text, _usage = run_opencode_cli(self.binary, self.model, prompt,
                                            agent="build", cwd=workdir,
                                            timeout=self.timeout)
            return text
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _prompt(self, work_order, diff: str) -> str:
        focus = ROLE_PROMPTS.get(self.role, "general correctness")
        return (
            f"Ban la reviewer {self.role.upper()}, quyen READ-ONLY.\n"
            f"Chi tap trung: {focus}.\n"
            "BAN DANG O MOT THU MUC TAM RONG. Chi duoc danh gia diff + tieu chi ben duoi.\n"
            "KHONG duoc ket luan reject chi vi khong thay file/repo trong cwd; file that\n"
            "do Lead va verify quan ly. Dung git/ls de tim repo trong cwd la sai.\n"
            "Chi bao loi THAT, co bang chung trong diff. Khong binh luan style.\n"
            f"MUC TIEU: {work_order.objective}\n"
            f"TIEU CHI: {work_order.acceptance_criteria}\n"
            f"VERIFY: {work_order.verify_command}\n"
            f"FILES: {', '.join(work_order.target_files or [])}\n"
            f"```diff\n{diff[:20000]}\n```\n"
            'Tra ve JSON: {"verdict":"approve"|"reject","reasons":["..."]}'
        )

    def review(self, work_order, *, diff: str, new_content: str, old_content: str,
               level: int, sink) -> dict:
        self.calls += 1
        try:
            data = parse_json_object(self.runner(self._prompt(work_order, diff)))
            reasons = [str(item) for item in (data.get("reasons") or [])][:6]
            verdict = ("approve" if str(data.get("verdict", "")).lower().startswith("approve")
                       else "reject")
            if verdict == "reject" and not reasons:
                reasons = ["reject without a reason"]
        except Exception as exc:                    # noqa: BLE001
            verdict, reasons = "error", [f"{type(exc).__name__}: {str(exc)[:160]}"]
        return {"verdict": verdict, "level": level, "task_id": work_order.task_id,
                "reasons": reasons, "role": self.role, "model": self.model,
                "reviewer": self.name}

    def review_set(self, work_order, *, diffs: dict, new_contents: dict,
                   old_contents: dict, level: int, sink) -> dict:
        combined = "\n".join(f"### {path}\n{diff}" for path, diff in diffs.items())
        return self.review(work_order, diff=combined,
                           new_content="\n".join(new_contents.values()),
                           old_content="\n".join(old_contents.values()),
                           level=level, sink=sink)


class WaveReviewer(Reviewer):
    """4 reviewer chay SONG SONG, chi doc; tong hop thanh 1 ket luan.

    `budget_left()` (tuy chon) tra so request con lai; wave chi chay toi da
    so do. Neu khong du ca 4 role, wave fallback heuristic 0 request thay vi
    reject oan; hard cap cua recipe la 16.
    """

    kind = "wave-reviewer"

    def __init__(self, roles: dict | None = None, *, factory=None,
                 name: str = "wave-reviewer", max_parallel: int = 4) -> None:
        super().__init__()
        self.roles = dict(roles or WAVE_ROLES)
        self.factory = factory or (lambda role, model: RoleReviewer(role, model))
        self.name = name
        self.max_parallel = max(1, int(max_parallel))
        self.budget_left = None
        self.last_results: list[dict] = []

    def _run(self, method: str, work_order, **kwargs) -> dict:
        all_roles = list(self.roles)
        reviewers = [(role, self.factory(role, self.roles[role])) for role in all_roles]
        allowed = len(reviewers)
        if self.budget_left is not None:
            try:
                allowed = max(0, min(allowed, int(self.budget_left())))
            except Exception:                       # noqa: BLE001
                allowed = 0
        reviewers = reviewers[:allowed]
        skipped = all_roles[len(reviewers):]

        if len(reviewers) < len(all_roles):
            # Graceful degradation: khong du ca 4 reviewer -> dung heuristic 0
            # request de khong reject oan mot ban da verify exit 0.
            fallback = HeuristicReviewer()
            findings = getattr(fallback, method)(work_order, **kwargs)
            findings.update({
                "reviewer": self.name + ":heuristic-fallback",
                "fallback_reason": "wave budget exhausted",
                "skipped": all_roles,
                "per_role": [],
                "budget_skipped": True,
            })
            sink = kwargs.get("sink")
            if sink is not None:
                sink.write(findings)
            return findings

        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(self.max_parallel, len(reviewers))) as pool:
            futures = {pool.submit(getattr(reviewer, method), work_order, **kwargs): role
                       for role, reviewer in reviewers}
            for future in as_completed(futures):
                role = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:            # noqa: BLE001
                    results.append({"verdict": "error", "role": role,
                                    "reasons": [f"{type(exc).__name__}: {str(exc)[:160]}"]})
        self.calls += len(reviewers)
        self.last_results = results

        reasons: list[str] = []
        for item in results:
            for reason in item.get("reasons") or []:
                reasons.append(f"[{item.get('role', '?')}] {reason}")
        verdict = "approve" if all(item.get("verdict") == "approve" for item in results) else "reject"
        findings = {"verdict": verdict, "level": work_order.level,
                    "task_id": work_order.task_id, "reasons": reasons[:10],
                    "reviewer": self.name, "per_role": results, "skipped": skipped}
        sink = kwargs.get("sink")
        if sink is not None:
            sink.write(findings)
        return findings

    def review(self, work_order, *, diff, new_content, old_content, level, sink) -> dict:
        return self._run("review", work_order, diff=diff, new_content=new_content,
                         old_content=old_content, level=level, sink=sink)

    def review_set(self, work_order, *, diffs, new_contents, old_contents, level, sink) -> dict:
        return self._run("review_set", work_order, diffs=diffs,
                         new_contents=new_contents, old_contents=old_contents,
                         level=level, sink=sink)

class MeetingReviewer(Reviewer):
    """Phase 2 adapter: review one diff through the meeting orchestrator.

    It is intentionally still a Reviewer, so Recipe C's draft verify / apply
    flow stays unchanged.  Round 2 is added in Phase 3; for now a conflict
    produces a reject and the Lead never writes.
    """

    kind = "meeting-reviewer"
    wants_verify = True

    def __init__(self, panel: dict | None = None, *, factory=None,
                 name: str = "meeting-reviewer", max_workers: int = 4) -> None:
        super().__init__()
        self.name = name
        self.max_workers = max(1, int(max_workers))
        if panel is None:
            panel = {role: (factory or (lambda r, m: RoleReviewer(r, m)))(role, model)
                     for role, model in WAVE_ROLES.items()}
        self.panel = dict(panel)
        self.roles = dict(self.panel)
        self.budget_left = None
        self.event_sink = None
        self.run_id = "meeting"
        self.last_orchestrator = None
        self.last_result = None
        self.coder = None
        self._coder_base = 0
        self.prepare_patch = None
        self.rebuttal_used = False
        self.breaker = None

    def bind_run(self, run_id: str, root, log: bool = True) -> None:
        """Recipe calls this before review so meeting events land in its run."""
        self.run_id = str(run_id or "meeting")
        self.rebuttal_used = False
        self.last_result = None
        self.breaker = None
        if not log:
            # Keep a custom sink supplied by a test/embedding caller; without
            # one there is simply no ledger event.
            return
        import ledger
        root_path = Path(root)

        def sink(event_type: str, data: dict, status: str = "info") -> None:
            ledger.append_event(
                event_type, run_id=self.run_id,
                result={"status": status}, data=data, root=root_path,
            )
        self.event_sink = sink

    def bind_coder(self, coder) -> None:
        self.coder = coder
        self._coder_base = int(getattr(coder, "calls", 0) or 0)

    def bind_patch_preparer(self, prepare) -> None:
        """Recipe injects a callback that draft-verifies a new patch."""
        self.prepare_patch = prepare

    def _run(self, work_order, *, context: dict, sink=None) -> dict:
        from core.budget import BudgetExceeded, CircuitBreaker, MeetingBudget
        from core.meeting import MeetingContext, MeetingOrchestrator
        from core.policy import VetoPolicy
        from core.protocol import Decision

        before = sum(int(getattr(item, "calls", 0) or 0)
                     for item in self.panel.values())
        orchestrator = MeetingOrchestrator(
            self.panel, event_sink=self.event_sink,
            max_workers=self.max_workers, name=self.name,
            run_id=self.run_id,
        )
        meeting_context = MeetingContext(work_order=work_order, **context)
        policy = VetoPolicy()
        self.breaker = CircuitBreaker()
        decision = None
        result = None
        budget = None
        prepared = None
        try:
            orchestrator.open(work_order, meeting_context)
            result = orchestrator.run_round1(
                work_order, meeting_context, sink=sink)
            coder_calls = 0
            if self.coder is not None:
                coder_calls = max(0, int(getattr(self.coder, "calls", 0) or 0)
                                  - self._coder_base)
            budget = MeetingBudget()
            budget.seed(coder_initial=coder_calls, round1=len(result.findings))

            if result.all_approved:
                decision = Decision(verdict="approve",
                                    reasons=["4/4 reviewer approve"], round=1)
            else:
                participants = orchestrator.participants_for_rebuttal(
                    result.conflicts, new_patch=False)
                prepared = None
                if (self.coder is None or self.prepare_patch is None
                        or self.rebuttal_used or not budget.can_open_round2()):
                    if budget is not None and budget.reason:
                        orchestrator.abort(budget.reason, work_order=work_order)
                    decision = policy.evaluate(
                        orchestrator.blackboard.votes, result.conflicts,
                        verify_result=meeting_context.verify_result, round_no=1)
                    if decision.verdict == "approve":
                        decision = Decision("escalate",
                                            ["cannot open rebuttal without coder/verifier"],
                                            round=1)
                else:
                    self.rebuttal_used = True
                    feedback = self._rebuttal_feedback(result)
                    try:
                        if not budget.can_spend("coder", "rebuttal"):
                            raise BudgetExceeded(budget.reason or "coder rebuttal budget")
                        budget.spend("coder", "rebuttal")
                        if context.get("diffs") is not None:
                            raw = self.coder.patch_set(
                                work_order,
                                current_files=context.get("new_contents") or {},
                                feedback=feedback, round_index=2)
                        else:
                            one = self.coder.patch(
                                work_order,
                                current_content=context.get("new_content", ""),
                                feedback=feedback, round_index=2)
                            target = (context.get("target") or
                                      (work_order.target_files[0]
                                       if work_order.target_files else ""))
                            raw = {target: one}
                        prepared = self.prepare_patch(raw, budget)
                    except BudgetExceeded as exc:
                        orchestrator.abort(str(exc), work_order=work_order, round_no=2)
                        decision = Decision("reject", [str(exc)[:300]], round=2)
                    except Exception as exc:  # noqa: BLE001
                        signature = f"coder:{type(exc).__name__}:{str(exc)[:120]}"
                        if self.breaker is not None and self.breaker.observe(signature):
                            orchestrator.abort("circuit breaker: repeated coder error",
                                               work_order=work_order, round_no=2)
                        decision = Decision(
                            "reject", [f"rebuttal patch/verify failed: {type(exc).__name__}: {str(exc)[:240]}"],
                            round=2)
                    if prepared is not None and prepared.get("error"):
                        signature = f"prepare:{prepared['error'][:160]}"
                        if self.breaker is not None and self.breaker.observe(signature):
                            orchestrator.abort("circuit breaker: repeated patch error",
                                               work_order=work_order, round_no=2)
                        decision = Decision(
                            "reject", [str(prepared["error"])[:300]], round=2)
                        prepared = None
                    if prepared is not None and not decision:
                        verify = prepared.get("verify") or {}
                        if verify.get("exit_code") not in (0, "0"):
                            decision = Decision(
                                "reject", ["rebuttal patch verify failed"], round=2)
                            prepared = None
                    if prepared is not None and not decision:
                        participants = orchestrator.participants_for_rebuttal(
                            result.conflicts, new_patch=True)
                        if len(participants) < 2 or "security" not in participants or "qa" not in participants:
                            decision = Decision("reject",
                                                ["new patch requires Security + QA re-check"],
                                                round=2)
                        else:
                            # Record Coder's proposal before the second vote.
                            orchestrator.record_rebuttal(
                                work_order, sender="coder", recipients=participants,
                                body="rebuttal patch prepared for re-check", sink=sink)
                            if not budget.can_spend("reviewer", "round2", len(participants)):
                                orchestrator.abort(budget.reason or "round2 budget",
                                                   work_order=work_order, round_no=2)
                                decision = Decision("reject", [budget.reason or "round2 budget"],
                                                    round=2)
                            else:
                                budget.spend("reviewer", "round2", len(participants))
                                new_context = MeetingContext(
                                    work_order=work_order,
                                    brief=meeting_context.brief,
                                    diff=prepared.get("diff", ""),
                                    new_content=prepared.get("new_content", ""),
                                    old_content=meeting_context.old_content,
                                    level=meeting_context.level,
                                    verify_result=verify,
                                    diffs=prepared.get("diffs"),
                                    new_contents=prepared.get("new_contents"),
                                    old_contents=prepared.get("old_contents"),
                                )
                                result2 = orchestrator.run_round2(
                                    work_order, new_context, participants,
                                    sink=sink, new_patch=True)
                                decision = policy.evaluate(
                                    orchestrator.blackboard.votes,
                                    orchestrator.last_result.conflicts,
                                    verify_result=verify, round_no=2)
                                if decision.verdict == "approve":
                                    prepared["participants"] = participants
                                    prepared["round2"] = [dict(item)
                                                          for item in result2.findings]
                                else:
                                    prepared = None
            if decision is None:
                decision = Decision("reject", ["meeting could not reach a decision"], round=2)
            orchestrator.close(decision, work_order=work_order)
            aggregate = orchestrator.aggregate(result)
            aggregate["decision"] = decision.to_dict()
            aggregate["budget"] = budget.snapshot() if budget else None
            if decision.verdict == "approve":
                aggregate["verdict"] = "approve"
            else:
                aggregate["verdict"] = "reject"
            if prepared:
                aggregate["rebuttal"] = prepared
            aggregate["meeting"] = orchestrator.blackboard.snapshot()
        except Exception as exc:  # noqa: BLE001
            # A meeting infrastructure failure must never auto-approve.
            orchestrator.abort(f"{type(exc).__name__}: {str(exc)[:240]}",
                               work_order=work_order)
            aggregate = {
                "verdict": "reject",
                "level": getattr(work_order, "level", 2),
                "reasons": [f"meeting error: {type(exc).__name__}: {str(exc)[:240]}"],
                "reviewer": self.name, "per_role": [], "skipped": [],
                "meeting": orchestrator.blackboard.snapshot(),
                "budget": budget.snapshot() if budget else None,
            }
            result = None
        self.calls += max(0, sum(int(getattr(item, "calls", 0) or 0)
                                 for item in self.panel.values()) - before)
        self.last_orchestrator = orchestrator
        self.last_result = result
        if sink is not None:
            sink.write(aggregate)
        return aggregate

    @staticmethod
    def _rebuttal_feedback(result) -> str:
        lines = ["ROUND 1 OBJECTIONS - chi phan bien co muc tieu, toi da 1 vong:"]
        for conflict in result.conflicts or []:
            lines.append(f"- [{conflict.role}] {conflict.body}")
        return "\n".join(lines)[:4000]

    def review(self, work_order, *, diff, new_content, old_content, level, sink,
               verify_result=None) -> dict:
        return self._run(work_order, sink=sink, context={
            "brief": getattr(work_order, "objective", ""),
            "diff": diff, "new_content": new_content,
            "old_content": old_content, "level": level,
            "verify_result": verify_result or {},
        })

    def review_set(self, work_order, *, diffs, new_contents, old_contents, level, sink,
                   verify_result=None) -> dict:
        return self._run(work_order, sink=sink, context={
            "brief": getattr(work_order, "objective", ""),
            "diffs": diffs, "new_contents": new_contents,
            "old_contents": old_contents, "level": level,
            "verify_result": verify_result or {},
        })

