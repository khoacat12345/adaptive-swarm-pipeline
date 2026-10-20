#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Safe Writer — backup truoc, ghi atomic, verify sau, rollback khi that bai.

Hop dong bao ve du lieu (khong duoc vi pham):
  1. Truoc khi ghi de bat ky file nao -> BAT BUOC co ban sao luu trong .agents/backups/.
  2. Ghi atomic: ghi ra file tam -> kiem tra toan ven -> moi replace file that.
     (Khong bao gio de file that o trang thai ghi do.)
  3. Sau khi ghi: verify bang SystemBot. That bai -> rollback ve dung 100% byte goc.

Ten file backup: <filename>.<YYYYmmdd-HHMMSS>.<sha256_8>.bak
Kem sidecar:      <filename>.<YYYYmmdd-HHMMSS>.<sha256_8>.meta.json
  -> meta luu duong dan goc, file co ton tai truoc do hay khong, hash va kich thuoc,
     nho do rollback biet phai RESTORE hay DELETE.

CLI:
  python safe_writer.py write --path scratch/a.py --content-file new.py --verify "python -m py_compile scratch/a.py"
  python safe_writer.py backup --path scratch/a.py
  python safe_writer.py rollback --path scratch/a.py
  python safe_writer.py list --path scratch/a.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger  # noqa: E402
import system_bot  # noqa: E402

AGENTS_DIR = Path(__file__).resolve().parent
BACKUPS_DIRNAME = "backups"


# --------------------------------------------------------------------------
# Tien ich
# --------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    digest = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backups_dir(root=None, backups_dir=None) -> Path:
    if backups_dir:
        return Path(backups_dir).resolve()
    return ledger.resolve_agents_dir(root=root) / BACKUPS_DIRNAME


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# --------------------------------------------------------------------------
# Backup
# --------------------------------------------------------------------------

def backup_file(
    path,
    *,
    root=None,
    backups_dir=None,
    reason: str = "",
    run_id: str | None = None,
    log: bool = True,
) -> dict:
    """Tao ban sao luu cho `path` (ke ca khi file chua ton tai -> ghi nhan 'existed: False')."""
    target = Path(path).resolve()
    bdir = _backups_dir(root=root, backups_dir=backups_dir)
    bdir.mkdir(parents=True, exist_ok=True)

    existed = target.exists()
    original = target.read_bytes() if existed else b""
    digest = sha256_bytes(original)
    stamp = _stamp()

    base = f"{target.name}.{stamp}.{digest[:8]}"
    backup_path = bdir / f"{base}.bak"
    meta_path = bdir / f"{base}.meta.json"

    with open(backup_path, "wb") as fh:
        fh.write(original)
        fh.flush()
        os.fsync(fh.fileno())

    meta = {
        "target": str(target),
        "existed": existed,
        "sha256": digest,
        "size": len(original),
        "created_at": ledger.utc_now_iso(),
        "reason": reason,
    }
    with open(meta_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    # Tu kiem tra: backup phai khop hash ghi trong ten file
    if sha256_file(backup_path) != digest:
        raise RuntimeError(f"Backup hong ngay sau khi tao: {backup_path}")

    info = {
        "target": str(target),
        "backup_path": str(backup_path),
        "meta_path": str(meta_path),
        "existed": existed,
        "original_sha256": digest,
        "size": len(original),
    }
    if log:
        ledger.append_event(
            "safe_writer.backup",
            run_id=run_id,
            result={"status": "ok", "error": None},
            data={
                "target": str(target),
                "backup_path": str(backup_path),
                "existed": existed,
                "original_sha256": digest,
                "size": len(original),
                "reason": reason,
            },
            root=root,
        )
    return info


def read_backup_meta(meta_path) -> dict:
    with open(meta_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def list_backups(path, *, root=None, backups_dir=None) -> list[dict]:
    """Liet ke cac ban backup cua 1 file, moi nhat truoc."""
    target = str(Path(path).resolve())
    bdir = _backups_dir(root=root, backups_dir=backups_dir)
    if not bdir.exists():
        return []
    items: list[dict] = []
    for meta_path in sorted(bdir.glob("*.meta.json"), reverse=True):
        try:
            meta = read_backup_meta(meta_path)
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("target") != target:
            continue
        items.append({
            "meta_path": str(meta_path),
            "backup_path": str(meta_path.with_name(meta_path.name.replace(".meta.json", ".bak"))),
            "created_at": meta.get("created_at"),
            "existed": meta.get("existed"),
            "sha256": meta.get("sha256"),
            "size": meta.get("size"),
        })
    return items


def find_latest_backup(path, *, root=None, backups_dir=None) -> dict | None:
    items = list_backups(path, root=root, backups_dir=backups_dir)
    return items[0] if items else None


# --------------------------------------------------------------------------
# Ghi atomic
# --------------------------------------------------------------------------

def atomic_write(path, content, *, encoding: str = "utf-8") -> dict:
    """
    Ghi file theo kieu atomic:
      ghi file tam cung thu muc -> fsync -> kiem tra hash -> os.replace.
    Tra ve {sha256, size, path}.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode(encoding) if isinstance(content, str) else bytes(content)
    digest = sha256_bytes(data)

    tmp_path = target.with_name(f".{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    try:
        with open(tmp_path, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())

        if sha256_file(tmp_path) != digest:
            raise RuntimeError("File tam khong toan ven sau khi ghi")

        try:
            os.replace(tmp_path, target)
        except PermissionError:
            # File that dang read-only -> bo co va thu lai 1 lan
            try:
                os.chmod(target, 0o666)
            except OSError:
                pass
            os.replace(tmp_path, target)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    return {"path": str(target.resolve()), "sha256": digest, "size": len(data)}


# --------------------------------------------------------------------------
# Rollback
# --------------------------------------------------------------------------

def rollback(
    path,
    *,
    backup_path=None,
    root=None,
    backups_dir=None,
    run_id: str | None = None,
    log: bool = True,
) -> dict:
    """Khoi phuc file ve dung trang thai truoc khi ghi."""
    target = Path(path).resolve()
    entry = None
    if backup_path is None:
        entry = find_latest_backup(target, root=root, backups_dir=backups_dir)
        if not entry:
            raise FileNotFoundError(f"Khong tim thay ban backup nao cho {target}")
        meta_path = Path(entry["meta_path"])
    else:
        bpath = Path(backup_path)
        meta_path = bpath.with_name(bpath.name.replace(".bak", ".meta.json"))
        if not meta_path.exists():
            raise FileNotFoundError(f"Thieu meta cua backup: {meta_path}")

    meta = read_backup_meta(meta_path)
    bpath = meta_path.with_name(meta_path.name.replace(".meta.json", ".bak"))

    existed = bool(meta.get("existed"))
    expected_sha = meta.get("sha256")

    if existed:
        data = bpath.read_bytes()
        if sha256_bytes(data) != expected_sha:
            raise RuntimeError(f"Backup bi hong (hash khong khop): {bpath}")
        atomic_write(target, data)
        restored_sha = sha256_file(target)
        ok = restored_sha == expected_sha
        action = "restored"
    else:
        if target.exists():
            target.unlink()
        restored_sha = None
        ok = not target.exists()
        action = "deleted"

    result = {
        "target": str(target),
        "backup_path": str(bpath),
        "action": action,
        "existed": existed,
        "expected_sha256": expected_sha,
        "restored_sha256": restored_sha,
        "ok": ok,
    }
    if log:
        ledger.append_event(
            "safe_writer.rollback",
            run_id=run_id,
            result={"status": "pass" if ok else "fail",
                    "error": None if ok else "rollback khong khop hash"},
            data=result,
            root=root,
        )
    return result


# --------------------------------------------------------------------------
# Ghi an toan (backup -> atomic write -> verify -> rollback neu that bai)
# --------------------------------------------------------------------------

def write_safely(
    path,
    content,
    *,
    verify_cmd: str | None = None,
    verify_timeout: int = system_bot.DEFAULT_TIMEOUT,
    cwd=None,
    root=None,
    backups_dir=None,
    run_id: str | None = None,
    encoding: str = "utf-8",
    allow_network: bool = False,
    reason: str = "",
    log: bool = True,
) -> dict:
    """
    Ghi file theo quy trinh an toan day du. Tra ve dict mo ta toan bo qua trinh.
    `verified` = True/False/None (None = khong chay verify).
    """
    target = Path(path)
    run_id = run_id or ledger.new_run_id("sw")

    # Buoc 1: backup
    backup_info = backup_file(
        target, root=root, backups_dir=backups_dir,
        reason=reason or "write_safely", run_id=run_id, log=log,
    )

    # Buoc 2: ghi atomic
    try:
        written = atomic_write(target, content, encoding=encoding)
    except Exception as exc:
        if log:
            ledger.append_event(
                "safe_writer.write",
                run_id=run_id,
                result={"status": "error", "error": str(exc)},
                data={"target": str(target.resolve()), "phase": "atomic_write"},
                root=root,
            )
        raise

    # Buoc 3: kiem tra toan ven sau khi ghi
    on_disk = sha256_file(target)
    integrity_ok = on_disk == written["sha256"]
    if log:
        ledger.append_event(
            "safe_writer.write",
            run_id=run_id,
            result={"status": "pass" if integrity_ok else "fail",
                    "error": None if integrity_ok else "hash tren dia khac hash ghi ra"},
            data={
                "target": written["path"],
                "bytes_written": written["size"],
                "sha256": written["sha256"],
                "sha256_on_disk": on_disk,
                "atomic": True,
                "backup_path": backup_info["backup_path"],
            },
            root=root,
        )

    report = {
        "target": written["path"],
        "written": True,
        "integrity_ok": integrity_ok,
        "sha256": written["sha256"],
        "bytes_written": written["size"],
        "backup": backup_info,
        "verify": None,
        "verified": None,
        "rolled_back": False,
        "final_sha256": on_disk,
        "final_exists": Path(target).exists(),
    }

    if not integrity_ok:
        rollback(target, backup_path=backup_info["backup_path"], root=root,
                 backups_dir=backups_dir, run_id=run_id, log=log)
        report["rolled_back"] = True
        report["final_sha256"] = sha256_file(target)
        report["final_exists"] = Path(target).exists()
        return report

    # Buoc 4: verify bang may that (neu co lenh)
    if verify_cmd:
        bot = system_bot.run_command(
            verify_cmd,
            cwd=cwd or str(Path(target).parent),
            timeout=verify_timeout,
            root=root,
            run_id=run_id,
            allow_network=allow_network,
            log=log,
        )
        report["verify"] = bot
        report["verified"] = bot["exit_code"] == 0

        if log:
            ledger.append_event(
                "safe_writer.verify",
                run_id=run_id,
                duration_ms=bot["duration_ms"],
                result={"status": "pass" if report["verified"] else "fail",
                        "error": None if report["verified"] else f"exit_code={bot['exit_code']}"},
                data={
                    "target": written["path"],
                    "verify_cmd": verify_cmd,
                    "exit_code": bot["exit_code"],
                },
                root=root,
            )

        # Buoc 5: verify that bai -> rollback ngay
        if not report["verified"]:
            rb = rollback(
                target, backup_path=backup_info["backup_path"], root=root,
                backups_dir=backups_dir, run_id=run_id, log=log,
            )
            report["rolled_back"] = True
            report["rollback"] = rb
            report["final_sha256"] = sha256_file(target)
            report["final_exists"] = Path(target).exists()

    return report


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _cmd_backup(args) -> int:
    info = backup_file(args.path, root=args.root, reason=args.reason)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def _cmd_write(args) -> int:
    content = Path(args.content_file).read_text(encoding="utf-8") if args.content_file else args.content
    report = write_safely(
        args.path, content or "",
        verify_cmd=args.verify, verify_timeout=args.timeout,
        root=args.root, reason=args.reason,
    )
    summary = {k: v for k, v in report.items() if k != "verify"}
    summary["verify"] = (
        {"exit_code": report["verify"]["exit_code"], "result": report["verify"]["result"]}
        if report.get("verify") else None
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report.get("verified") in (True, None) else 1


def _cmd_rollback(args) -> int:
    result = rollback(args.path, backup_path=args.backup, root=args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def _cmd_list(args) -> int:
    print(json.dumps(list_backups(args.path, root=args.root), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe Writer (backup + atomic + verify + rollback)")
    parser.add_argument("--root", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p_b = sub.add_parser("backup")
    p_b.add_argument("--path", required=True)
    p_b.add_argument("--reason", default="")

    p_w = sub.add_parser("write")
    p_w.add_argument("--path", required=True)
    p_w.add_argument("--content", default=None)
    p_w.add_argument("--content-file", default=None)
    p_w.add_argument("--verify", default=None)
    p_w.add_argument("--timeout", type=int, default=system_bot.DEFAULT_TIMEOUT)
    p_w.add_argument("--reason", default="")

    p_r = sub.add_parser("rollback")
    p_r.add_argument("--path", required=True)
    p_r.add_argument("--backup", default=None)

    p_l = sub.add_parser("list")
    p_l.add_argument("--path", required=True)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "backup": _cmd_backup,
        "write": _cmd_write,
        "rollback": _cmd_rollback,
        "list": _cmd_list,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
