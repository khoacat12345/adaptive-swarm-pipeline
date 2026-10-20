#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Project Profile Detector — nhan dien stack cua workspace hien tai.

Sinh ra .agents/profile.json gom:
  - project_type / project_types : NodeJS, Python, Go, Rust, Godot, DotNet, ...
  - verify_commands              : lenh build / test / lint / syntax mac dinh
  - verify_defaults              : map kind -> lenh dau tien (tien dung nhanh)
  - is_git_repo / git_root
  - forbidden_patterns           : file/thu muc cam gui ra ngoai (.env, *.pem, ...)
  - sensitive_files_found        : canh bao file nhay cam dang nam trong cay nguon

Quy uoc placeholder trong verify_commands:
  {file}  -> duong dan 1 file cu the (dung khi verify 1 file vua sua)
  Vi du: "python -m py_compile {file}"

CLI:
  python profile_detector.py --root .
  python profile_detector.py --root . --print
  python profile_detector.py --root . --max-depth 4 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger  # noqa: E402
import safe_writer  # noqa: E402

AGENTS_DIR = Path(__file__).resolve().parent
PROFILE_FILENAME = "profile.json"
SCHEMA_VERSION = 1
DEFAULT_MAX_DEPTH = 3
MAX_DIRS_VISITED = 4000

# Thu muc bo qua khi quet (vua de nhanh, vua de tranh quet du lieu ca nhan)
DEFAULT_IGNORES = {
    "node_modules", "venv", ".venv", "env", "__pycache__", ".git", ".hg", ".svn",
    ".cache", ".npm", ".cargo", ".rustup", ".gradle", ".m2", ".nuget", ".pnpm-store",
    "dist", "build", "out", "target", ".next", ".nuxt", "coverage",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    "AppData", "Application Data", "Local Settings", "Cookies", "NetHood",
    "PrintHood", "Recent", "SendTo", "Templates", "Start Menu", "OneDrive",
    "Downloads", "Pictures", "Music", "Videos", "Favorites", "Links",
    "Contacts", "Saved Games", "Searches", ".agents",
}

DEFAULT_FORBIDDEN_PATTERNS = [
    ".env", ".env.*", "*.env",
    "*.pem", "*.key", "*.pfx", "*.p12", "*.jks", "*.keystore",
    "id_rsa", "id_rsa.*", "id_ed25519", "id_ed25519.*",
    "*credential*", "*credentials*", "*secret*", "*password*",
    ".npmrc", ".pypirc", ".netrc", ".htpasswd",
    "*.tfstate", "*.tfvars",
    ".aws/**", ".ssh/**", ".docker/config.json", ".kube/config",
]

MANIFESTS = {
    "package.json": "NodeJS",
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "setup.py": "Python",
    "Pipfile": "Python",
    "go.mod": "Go",
    "Cargo.toml": "Rust",
    "project.godot": "Godot",
    "pom.xml": "Java-Maven",
    "build.gradle": "Java-Gradle",
    "build.gradle.kts": "Java-Gradle",
    "composer.json": "PHP",
    "Gemfile": "Ruby",
    "CMakeLists.txt": "CMake",
    "Makefile": "Make",
    "Dockerfile": "Docker",
}
MANIFEST_GLOBS = {
    "*.csproj": "DotNet",
    "*.sln": "DotNet",
    "*.fsproj": "DotNet",
}

TYPE_PRIORITY = [
    "NodeJS", "Python", "Go", "Rust", "DotNet", "Godot",
    "Java-Maven", "Java-Gradle", "CMake", "PHP", "Ruby", "Make", "Docker",
]


# --------------------------------------------------------------------------
# Quet cay thu muc
# --------------------------------------------------------------------------

def scan_tree(root: Path, max_depth: int, ignores: set[str]) -> dict:
    root = root.resolve()
    hits: list[dict] = []
    dirs_visited = 0
    pruned: set[str] = set()
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        depth = 0 if rel == Path(".") else len(rel.parts)

        if depth >= max_depth:
            dirnames[:] = []
        else:
            keep = []
            for name in dirnames:
                if name in ignores or name.startswith("."):
                    pruned.add(name)
                    continue
                keep.append(name)
            dirnames[:] = keep

        dirs_visited += 1
        if dirs_visited > MAX_DIRS_VISITED:
            truncated = True
            break

        for filename in filenames:
            if filename in MANIFESTS:
                hits.append({
                    "path": str(Path(dirpath) / filename),
                    "rel": str((rel / filename)),
                    "name": filename,
                    "type": MANIFESTS[filename],
                })
            else:
                for pattern, type_name in MANIFEST_GLOBS.items():
                    if Path(filename).match(pattern):
                        hits.append({
                            "path": str(Path(dirpath) / filename),
                            "rel": str((rel / filename)),
                            "name": filename,
                            "type": type_name,
                        })
                        break

    return {
        "hits": hits,
        "dirs_visited": dirs_visited,
        "pruned_dirs": sorted(pruned)[:40],
        "truncated": truncated,
    }


def find_git_root(root: Path) -> str | None:
    current = root.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return str(candidate)
    return None


# --------------------------------------------------------------------------
# Sinh verify_commands theo tung stack
# --------------------------------------------------------------------------

def _entry(kind: str, cmd: str, source: str) -> dict:
    return {"kind": kind, "cmd": cmd, "source": source}


def _detect_node(manifest_path: Path) -> list[dict]:
    entries: list[dict] = []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return entries

    scripts = data.get("scripts") or {}
    base_dir = manifest_path.parent
    if (base_dir / "pnpm-lock.yaml").exists():
        runner = "pnpm"
    elif (base_dir / "yarn.lock").exists():
        runner = "yarn"
    else:
        runner = "npm"

    if "test" in scripts:
        entries.append(_entry("test", f"{runner} test", "package.json:scripts.test"))
    if "lint" in scripts:
        entries.append(_entry("lint", f"{runner} run lint", "package.json:scripts.lint"))
    if "build" in scripts:
        entries.append(_entry("build", f"{runner} run build", "package.json:scripts.build"))
    if "typecheck" in scripts:
        entries.append(_entry("typecheck", f"{runner} run typecheck", "package.json:scripts.typecheck"))
    entries.append(_entry("syntax", "node --check {file}", "node builtin"))
    return entries


def _detect_python(root: Path, manifests: list[dict]) -> list[dict]:
    entries: list[dict] = []
    haystack = ""
    for item in manifests:
        try:
            haystack += Path(item["path"]).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass
    has_tests = (root / "tests").is_dir() or (root / "test").is_dir()
    if "pytest" in haystack or has_tests:
        entries.append(_entry("test", "python -m pytest -q", "pytest"))
    if "ruff" in haystack:
        entries.append(_entry("lint", "python -m ruff check .", "ruff"))
    elif "flake8" in haystack:
        entries.append(_entry("lint", "python -m flake8 .", "flake8"))
    if "mypy" in haystack:
        entries.append(_entry("typecheck", "python -m mypy .", "mypy"))
    entries.append(_entry("syntax", "python -m py_compile {file}", "python builtin"))
    return entries


def build_verify_commands(project_types: list[str], manifests: list[dict], root: Path) -> list[dict]:
    entries: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(items: list[dict]) -> None:
        for item in items:
            key = (item["kind"], item["cmd"])
            if key not in seen:
                seen.add(key)
                entries.append(item)

    node_manifests = [m for m in manifests if m["type"] == "NodeJS"]
    python_manifests = [m for m in manifests if m["type"] == "Python"]

    for item in node_manifests:
        add(_detect_node(Path(item["path"])))
    if python_manifests:
        add(_detect_python(root, python_manifests))

    static_map = {
        "Go": [("build", "go build ./..."), ("test", "go test ./..."),
               ("lint", "go vet ./..."), ("syntax", "gofmt -l {file}")],
        "Rust": [("build", "cargo build"), ("test", "cargo test"),
                 ("lint", "cargo clippy -- -D warnings")],
        "DotNet": [("build", "dotnet build"), ("test", "dotnet test")],
        "Godot": [("syntax", "godot --headless --quit")],
        "Java-Maven": [("test", "mvn -q test")],
        "Java-Gradle": [("test", ("./gradlew test" if os.name != "nt" else "gradlew.bat test"))],
        "Ruby": [("test", "bundle exec rspec")],
        "CMake": [("build", "cmake --build build")],
        "Make": [("test", "make test")],
        "PHP": [("lint", "composer validate --no-check-publish")],
    }
    for type_name in project_types:
        for kind, cmd in static_map.get(type_name, []):
            add([_entry(kind, cmd, type_name.lower())])

    # Lenh test tong quat cho Make/CMake chi them neu that su co file tuong ung
    return entries


def find_sensitive_files(root: Path, max_entries: int = 50) -> list[str]:
    found: list[str] = []
    patterns = ["*.env", ".env", "*.pem", "*.key", "*.pfx", "*.p12", "id_rsa", ".npmrc", ".netrc"]
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in DEFAULT_IGNORES]
        for filename in filenames:
            name = filename.lower()
            if (name == ".env" or name.endswith((".pem", ".key", ".pfx", ".p12"))
                    or name in {"id_rsa", ".npmrc", ".netrc"}):
                found.append(str(Path(dirpath) / filename))
                if len(found) >= max_entries:
                    return found
        if len(Path(dirpath).relative_to(root).parts) >= 2:
            dirnames[:] = []
    return found


# --------------------------------------------------------------------------
# Tong hop profile
# --------------------------------------------------------------------------

def detect(root, *, max_depth: int = DEFAULT_MAX_DEPTH, write: bool = True,
           run_id: str | None = None) -> dict:
    started = time.perf_counter()
    root_path = Path(root).resolve()
    run_id = run_id or ledger.new_run_id("profile")

    scan = scan_tree(root_path, max_depth, DEFAULT_IGNORES)
    hits = scan["hits"]

    project_types: list[str] = []
    for item in hits:
        if item["type"] not in project_types:
            project_types.append(item["type"])
    project_types.sort(key=lambda t: TYPE_PRIORITY.index(t) if t in TYPE_PRIORITY else 99)

    manifests = sorted(hits, key=lambda h: (h["type"], h["rel"]))[:60]
    verify_commands = build_verify_commands(project_types, manifests, root_path)

    git_root = find_git_root(root_path)
    warnings: list[str] = []
    if scan["truncated"]:
        warnings.append(f"Quet bi cat bot sau {MAX_DIRS_VISITED} thu muc -- profile co the thieu manifest")
    if not project_types:
        warnings.append("Khong nhan dien duoc stack nao -- hay tro --root vao thu muc du an")
    if not any(v["kind"] == "test" for v in verify_commands):
        warnings.append("Khong tim thay lenh test -- verify se dua vao kiem tra syntax")
    lockfiles = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}
    root_locks = [n for n in lockfiles if (root_path / n).exists()]
    if root_locks and not (root_path / "package.json").exists():
        warnings.append(f"Co lockfile {root_locks} nhung thieu package.json o goc")

    defaults: dict[str, str] = {}
    for item in verify_commands:
        defaults.setdefault(item["kind"], item["cmd"])

    profile = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": ledger.utc_now_iso(),
        "root": str(root_path),
        "project_type": project_types[0] if project_types else "Unknown",
        "project_types": project_types,
        "is_git_repo": git_root is not None,
        "git_root": git_root,
        "manifests": manifests,
        "verify_commands": verify_commands,
        "verify_defaults": defaults,
        "forbidden_patterns": DEFAULT_FORBIDDEN_PATTERNS,
        "sensitive_files_found": find_sensitive_files(root_path),
        "warnings": warnings,
        "scan": {
            "max_depth": max_depth,
            "dirs_visited": scan["dirs_visited"],
            "pruned_dirs_sample": scan["pruned_dirs"],
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    }

    if write:
        target = ledger.resolve_agents_dir(root=root_path) / PROFILE_FILENAME
        safe_writer.write_safely(
            target,
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
            root=root_path, run_id=run_id, reason="profile_detector",
        )
        profile["profile_path"] = str(target)

    ledger.append_event(
        "profile.scan",
        run_id=run_id,
        duration_ms=profile["scan"]["duration_ms"],
        result={"status": "ok", "error": None},
        data={
            "root": str(root_path),
            "project_type": profile["project_type"],
            "project_types": project_types,
            "is_git_repo": profile["is_git_repo"],
            "manifests_found": len(manifests),
            "verify_kinds": sorted({v["kind"] for v in verify_commands}),
            "warnings": warnings,
        },
        root=root_path,
    )
    return profile


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Nhan dien ho so du an -> .agents/profile.json")
    parser.add_argument("--root", default=".", help="thu muc du an can quet")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument("--no-write", action="store_true", help="chi in, khong ghi profile.json")
    parser.add_argument("--print", dest="do_print", action="store_true", help="in profile ra man hinh")
    parser.add_argument("--json", action="store_true", help="in JSON day du")
    args = parser.parse_args(argv)

    profile = detect(args.root, max_depth=args.max_depth, write=not args.no_write)

    if args.json:
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    elif args.do_print:
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    else:
        print(f"[profile] root          : {profile['root']}")
        print(f"[profile] project_type  : {profile['project_type']}  {profile['project_types']}")
        print(f"[profile] is_git_repo   : {profile['is_git_repo']}  ({profile['git_root']})")
        print(f"[profile] manifests     : {len(profile['manifests'])}")
        for item in profile["verify_commands"]:
            print(f"[profile]   verify {item['kind']:<9}: {item['cmd']}")
        for warning in profile["warnings"]:
            print(f"[profile] CANH BAO      : {warning}")
        if profile.get("profile_path"):
            print(f"[profile] da ghi        : {profile['profile_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
