#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guarded Git worktree isolation for Adaptive Swarm Pipeline v2.1.

The main worktree is read-only from the runner's point of view. All project
writes happen in a worktree under SWARM_WORKTREE_ROOT. Destructive Git commands
are allowed only after assert_isolated_worktree() succeeds.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import (
    CleanTreeRequired,
    IsolationError,
    IsolationGuardFailed,
    WorktreeCreateFailed,
    WorktreeRemoveFailed,
)

DEFAULT_BRANCH_PREFIX = "swarm/"
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def safe_component(value: str) -> str:
    cleaned = _SAFE_ID.sub("-", str(value)).strip(".-")
    return cleaned[:120] or "item"


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _git(
    cwd: str | Path,
    *args: str,
    check: bool = True,
    timeout: int = 60,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    command = ["git", "-C", str(Path(cwd)), *[str(arg) for arg in args]]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={**os.environ, **(env or {})},
    )
    if check and result.returncode != 0:
        raise IsolationError(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{(result.stderr or result.stdout or '').strip()[:600]}"
        )
    return result


def git_root(path: str | Path) -> Path:
    result = _git(path, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise IsolationError(f"not a git repository: {path}")
    return Path((result.stdout or "").strip()).resolve()


def main_root(path: str | Path) -> Path:
    return git_root(path)


def _default_candidate() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "AdaptiveSwarm" / "worktrees"
        return Path.home() / "AppData" / "Local" / "AdaptiveSwarm" / "worktrees"
    return Path.home() / ".local" / "state" / "adaptive-swarm" / "worktrees"


def default_worktree_root(root: str | Path) -> Path:
    """Return an external worktree root.

    The special home-directory repository layout used by this project can make
    %LOCALAPPDATA% a descendant of the repo root. In that case use a sibling of
    the repo instead of nesting a worktree inside the repository.
    """
    configured = os.environ.get("SWARM_WORKTREE_ROOT")
    root_path = Path(root).resolve()
    candidate = Path(configured).expanduser().resolve() if configured else _default_candidate()
    if _is_within(candidate, root_path) or _is_within(root_path, candidate):
        candidate = root_path.parent / f"{root_path.name}.swarm-worktrees"
    return candidate.resolve()


def assert_external_root(root: str | Path, worktree_root: str | Path) -> Path:
    main = Path(root).resolve()
    wt_root = Path(worktree_root).expanduser().resolve()
    if _same_path(main, wt_root):
        raise IsolationGuardFailed("worktree root must not be the main repository")
    if _is_within(wt_root, main) or _is_within(main, wt_root):
        raise IsolationGuardFailed(
            f"worktree root must be outside the main repository: {wt_root}"
        )
    return wt_root


def assert_clean(main: str | Path) -> None:
    result = _git(main, "status", "--porcelain", "--untracked-files=all", check=False)
    if result.returncode != 0:
        raise IsolationError(f"cannot inspect main worktree: {result.stderr.strip()}")
    if (result.stdout or "").strip():
        raise CleanTreeRequired(
            "main worktree is dirty; commit or stash changes before starting a v2 run"
        )


def resolve_base_sha(main: str | Path, ref: str = "HEAD") -> str:
    result = _git(main, "rev-parse", "--verify", f"{ref}^{{commit}}", check=False)
    if result.returncode != 0:
        raise IsolationError(f"cannot resolve base commit {ref!r}")
    return (result.stdout or "").strip()


def branch_exists(main: str | Path, branch: str) -> bool:
    result = _git(
        main, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False
    )
    return result.returncode == 0


def _check_disk(path: Path, minimum_free_bytes: int) -> None:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    free = shutil.disk_usage(target).free
    if free < minimum_free_bytes:
        raise IsolationError(
            f"not enough disk space at {target}: {free} < {minimum_free_bytes}"
        )


@dataclass(frozen=True)
class Workspace:
    run_id: str
    main_root: Path
    worktree_root: Path
    path: Path
    branch: str
    base_sha: str

    def as_dict(self) -> dict[str, str]:
        return {
            "mode": "git-worktree",
            "run_id": self.run_id,
            "main_root": str(self.main_root),
            "worktree_root": str(self.worktree_root),
            "worktree_path": str(self.path),
            "branch": self.branch,
            "base_sha": self.base_sha,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Workspace":
        return cls(
            run_id=str(data["run_id"]),
            main_root=Path(data["main_root"]).resolve(),
            worktree_root=Path(data["worktree_root"]).resolve(),
            path=Path(data["worktree_path"]).resolve(),
            branch=str(data["branch"]),
            base_sha=str(data["base_sha"]),
        )


def assert_isolated_worktree(
    main: str | Path,
    worktree_path: str | Path,
    worktree_root: str | Path | None = None,
) -> Path:
    """Verify that a path is a real, external git worktree, not the main tree."""
    main_path = Path(main).resolve()
    raw_path = Path(worktree_path).expanduser()
    if raw_path.is_symlink():
        raise IsolationGuardFailed(f"worktree path is a symlink: {raw_path}")
    path = raw_path.resolve()
    root = (
        Path(worktree_root).expanduser().resolve()
        if worktree_root is not None
        else default_worktree_root(main_path)
    )

    if _same_path(path, main_path):
        raise IsolationGuardFailed("worktree path equals the main worktree")
    if not _is_within(path, root):
        raise IsolationGuardFailed(f"worktree path is outside SWARM_WORKTREE_ROOT: {path}")
    if _is_within(root, main_path) or _is_within(path, main_path):
        raise IsolationGuardFailed("worktree must be outside the main repository")
    if not path.is_dir():
        raise IsolationGuardFailed(f"worktree does not exist: {path}")

    actual_root = git_root(path)
    actual_main = git_root(main_path)
    if not _same_path(actual_root, path):
        raise IsolationGuardFailed(f"git worktree root mismatch: {actual_root} != {path}")
    if not _same_path(actual_main, main_path):
        raise IsolationGuardFailed(f"git main root mismatch: {actual_main} != {main_path}")
    return path


def create_worktree(
    root: str | Path,
    run_id: str,
    *,
    worktree_root: str | Path | None = None,
    base_sha: str | None = None,
    require_clean: bool = True,
    branch: str | None = None,
    min_free_bytes: int = 512 * 1024 * 1024,
) -> Workspace:
    main = git_root(root)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", str(run_id)):
        raise WorktreeCreateFailed(f"unsafe run_id: {run_id!r}")
    if require_clean:
        assert_clean(main)
    root_path = (
        assert_external_root(main, worktree_root)
        if worktree_root is not None
        else default_worktree_root(main)
    )
    assert_external_root(main, root_path)
    root_path.mkdir(parents=True, exist_ok=True)
    _check_disk(root_path, min_free_bytes)

    base = resolve_base_sha(main, base_sha or "HEAD")
    branch_name = branch or f"{DEFAULT_BRANCH_PREFIX}{run_id}"
    if branch_exists(main, branch_name):
        raise WorktreeCreateFailed(f"branch already exists: {branch_name}")

    path = (root_path / run_id).resolve()
    if path.exists():
        raise WorktreeCreateFailed(f"worktree path already exists: {path}")

    result = _git(
        main,
        "worktree",
        "add",
        "-b",
        branch_name,
        str(path),
        base,
        check=False,
    )
    if result.returncode != 0:
        raise WorktreeCreateFailed(
            f"git worktree add failed: {(result.stderr or result.stdout).strip()[:800]}"
        )

    try:
        assert_isolated_worktree(main, path, root_path)
    except Exception:
        # Do not leave a half-created registered worktree behind.
        _git(main, "worktree", "remove", "--force", str(path), check=False)
        raise

    return Workspace(
        run_id=str(run_id),
        main_root=main,
        worktree_root=root_path,
        path=path,
        branch=branch_name,
        base_sha=base,
    )


def checkpoint(
    workspace: Workspace,
    phase_id: str,
    *,
    verify_exit: int = 0,
    state_root: str | Path | None = None,
) -> dict[str, Any]:
    """Commit the verified worktree and record an immutable checkpoint ref."""
    if verify_exit != 0:
        raise IsolationError(f"cannot checkpoint phase {phase_id}: verify exit {verify_exit}")
    wt = assert_isolated_worktree(
        workspace.main_root, workspace.path, workspace.worktree_root
    )
    _git(wt, "add", "-A", check=True)
    message = f"swarm: {workspace.run_id} phase {phase_id} verified"
    _git(wt, "-c", "user.name=Adaptive Swarm v2",
         "-c", "user.email=swarm-v2@localhost",
         "commit", "--allow-empty", "-m", message, check=True)
    sha = (_git(wt, "rev-parse", "HEAD", check=True).stdout or "").strip()
    ref = f"refs/swarm/{workspace.run_id}/{safe_component(phase_id)}"
    _git(wt, "update-ref", ref, sha, check=True)
    record = {
        "phase_id": str(phase_id),
        "sha": sha,
        "base_sha": workspace.base_sha,
        "branch": workspace.branch,
        "verify_exit": int(verify_exit),
        "created_at": utc_now_iso(),
        "ref": ref,
    }
    return record


def _untracked_files(wt: Path) -> list[str]:
    result = _git(wt, "ls-files", "--others", "--exclude-standard", check=True)
    return [line for line in (result.stdout or "").splitlines() if line.strip()]


def quarantine_and_rollback(
    workspace: Workspace,
    state_root: str | Path,
    phase_id: str,
    *,
    reason: str,
    last_checkpoint: str | None = None,
    verify: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture failed worktree evidence, then reset only that guarded worktree."""
    wt = assert_isolated_worktree(
        workspace.main_root, workspace.path, workspace.worktree_root
    )
    last = last_checkpoint or workspace.base_sha
    verified = _git(wt, "rev-parse", "--verify", f"{last}^{{commit}}", check=False)
    if verified.returncode != 0:
        raise IsolationGuardFailed(f"last checkpoint is not a commit: {last}")
    last_sha = (verified.stdout or "").strip()
    ancestor = _git(wt, "merge-base", "--is-ancestor", last_sha, "HEAD", check=False)
    if ancestor.returncode != 0:
        raise IsolationGuardFailed(
            f"refusing to roll back to a commit that is not an ancestor: {last_sha}"
        )

    phase_dir = Path(state_root) / "quarantine" / safe_component(phase_id)
    untracked_dir = phase_dir / "untracked"
    phase_dir.mkdir(parents=True, exist_ok=True)
    untracked_dir.mkdir(parents=True, exist_ok=True)

    patch = _git(wt, "diff", "--binary", f"{last_sha}..HEAD", check=True).stdout or ""
    (phase_dir / "patch.diff").write_text(patch, encoding="utf-8", newline="\n")
    status = _git(wt, "status", "--porcelain", "--untracked-files=all", check=True).stdout or ""
    (phase_dir / "status.txt").write_text(status, encoding="utf-8", newline="\n")

    copied: list[str] = []
    for relative in _untracked_files(wt):
        source = (wt / relative).resolve()
        if not _is_within(source, wt) or source.is_symlink() or not source.is_file():
            continue
        if source.stat().st_size > 20 * 1024 * 1024:
            continue
        destination = untracked_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(relative)

    manifest = {
        "phase_id": str(phase_id),
        "reason": str(reason),
        "last_checkpoint": last_sha,
        "patch": str(phase_dir / "patch.diff"),
        "untracked": copied,
        "status": str(phase_dir / "status.txt"),
        "verify": verify or {},
        "created_at": utc_now_iso(),
    }
    (phase_dir / "quarantine.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    # Both commands are guarded: only the isolated worktree is reset/cleaned.
    assert_isolated_worktree(
        workspace.main_root, workspace.path, workspace.worktree_root
    )
    _git(wt, "reset", "--hard", last_sha, check=True)
    _git(wt, "clean", "-fd", check=True)
    assert_isolated_worktree(
        workspace.main_root, workspace.path, workspace.worktree_root
    )
    current = (_git(wt, "rev-parse", "HEAD", check=True).stdout or "").strip()
    if current != last_sha:
        raise IsolationGuardFailed(
            f"rollback sanity check failed: {current} != {last_sha}"
        )
    manifest["rolled_back_to"] = current
    return manifest


def handoff(
    workspace: Workspace,
    state_root: str | Path,
    *,
    report: str | None = None,
) -> dict[str, Any]:
    """Write a reviewable patch/report; never merge into the main worktree."""
    wt = assert_isolated_worktree(
        workspace.main_root, workspace.path, workspace.worktree_root
    )
    handoff_dir = Path(state_root) / "handoff"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    patch = _git(wt, "diff", "--binary", f"{workspace.base_sha}..HEAD", check=True).stdout or ""
    patch_path = handoff_dir / "handoff.patch"
    patch_path.write_text(patch, encoding="utf-8", newline="\n")
    log = _git(wt, "log", "--oneline", "--decorate", f"{workspace.base_sha}..HEAD", check=True).stdout or ""
    report_path = handoff_dir / "report.md"
    report_path.write_text(
        report
        or (
            "# v2.1 handoff\n\n"
            f"- Run: `{workspace.run_id}`\n"
            f"- Branch: `{workspace.branch}`\n"
            f"- Base: `{workspace.base_sha}`\n"
            f"- Worktree: `{wt}`\n\n"
            "## Commits\n\n"
            f"```text\n{log}```\n\n"
            "## Review\n\n"
            "The main worktree was not modified. Review the patch, then merge the "
            "swarm branch manually if accepted.\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    return {
        "handoff_dir": str(handoff_dir),
        "patch": str(patch_path),
        "report": str(report_path),
        "branch": workspace.branch,
        "head": (_git(wt, "rev-parse", "HEAD", check=True).stdout or "").strip(),
        "base_sha": workspace.base_sha,
    }


def remove_worktree(workspace: Workspace, *, keep_branch: bool = True) -> None:
    """Remove a worktree only after explicit operator action (tests/cleanup)."""
    wt = assert_isolated_worktree(
        workspace.main_root, workspace.path, workspace.worktree_root
    )
    result = _git(
        workspace.main_root, "worktree", "remove", "--force", str(wt), check=False
    )
    if result.returncode != 0:
        raise WorktreeRemoveFailed(
            f"git worktree remove failed: {(result.stderr or result.stdout).strip()[:600]}"
        )
    if not keep_branch:
        _git(workspace.main_root, "branch", "-D", workspace.branch, check=False)
