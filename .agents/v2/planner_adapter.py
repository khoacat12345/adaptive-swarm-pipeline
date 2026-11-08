#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn a goal, a v1 plan or a tasks file into a valid v2 MasterPlan.

The adapter is deterministic on purpose: it never calls a model. A caller that
wants LLM planning can generate a v1-shaped plan first (``planner.py``) and
then adapt it here. The result is always validated by ``v2.plan`` before it is
returned, so an invalid proposal cannot reach a worktree.

    python .agents/v2/planner_adapter.py --goal "fix src.py" --verify "python check.py"
    python .agents/v2/planner_adapter.py --plan v1_plan.json --out v2_plan.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tier_policy  # noqa: E402

from v2.errors import V2Error  # noqa: E402
from v2.execution import is_sensitive_path, normalize_tier  # noqa: E402
from v2.plan import (  # noqa: E402
    DEFAULT_FORBIDDEN,
    MAX_PHASES,
    PlanValidationError,
    SCHEMA_VERSION,
    plan_hash,
    validate_plan,
)

TARGET_TOKEN_RE = re.compile(
    r"(?<![\w./\\-])("
    r"(?:[\w.-]+[/\\])*[\w.-]+"
    r"\.(?:py|pyi|js|jsx|ts|tsx|mjs|cjs|md|json|ya?ml|toml|ini|cfg|txt|"
    r"html|css|scss|sh|bash|ps1|go|rs|java|kt|kts|c|h|cc|cpp|hpp|cs|rb|php|sql)"
    r")(?![\w/\\-])",
    re.IGNORECASE,
)
TEST_TARGET_RE = re.compile(
    r"(^test_.*\.py$|.*_test\.py$|^conftest\.py$)", re.IGNORECASE
)


class PlanningError(V2Error):
    code = "PLANNING_ERROR"


@dataclass(frozen=True)
class PlanAdapterResult:
    plan: dict[str, Any]
    plan_hash: str
    tier: str
    planner: str
    source: str
    source_hash: str
    summary: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "plan_hash": self.plan_hash,
            "tier": self.tier,
            "planner": self.planner,
            "source": self.source,
            "source_hash": self.source_hash,
            "summary": self.summary,
        }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _sha256_json(payload: Any) -> str:
    return _sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _normalize_relative(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip("/")


def _is_test_target(relative: str) -> bool:
    path = _normalize_relative(relative).lower()
    name = path.rsplit("/", 1)[-1]
    return "/tests/" in f"/{path}" or bool(TEST_TARGET_RE.match(name))


def _require_targets(targets: list[str], *, root: Path, allow_new_files: bool) -> list[str]:
    cleaned: list[str] = []
    for value in targets:
        relative = _normalize_relative(value)
        if not relative or relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
            raise PlanningError(f"unsafe target path: {value!r}")
        if ".." in Path(relative).parts:
            raise PlanningError(f"unsafe target path: {value!r}")
        if _is_test_target(relative):
            raise PlanningError(f"v2 cannot target a test file: {relative}")
        if not allow_new_files and not (root / relative).is_file():
            raise PlanningError(f"target does not exist: {relative}")
        if relative not in cleaned:
            cleaned.append(relative)
    if not cleaned:
        raise PlanningError("plan has no target files")
    return cleaned


def detect_targets(
    goal: str, root: str | Path = ".", *, allow_new_files: bool = False
) -> list[str]:
    """Extract path-like tokens from a natural-language goal.

    By default only existing files are returned. When ``allow_new_files`` is
    requested, tokens that do not exist yet are also accepted so a goal can
    create a new module without repeating it via ``--target``.
    """
    base = Path(root).expanduser().resolve()
    found: list[str] = []
    for match in TARGET_TOKEN_RE.finditer(str(goal or "")):
        relative = _normalize_relative(match.group(1))
        if not relative or relative in found:
            continue
        if (base / relative).is_file() or allow_new_files:
            found.append(relative)
    return found


def _extra_allowed_paths(
    values: Any,
    *,
    root: Path,
    allow_new_files: bool,
) -> list[str]:
    """Validate extra scopes a v1 plan declared as writable.

    Targets stay the only files the coder may submit; these scopes only keep
    plan validation from rejecting a declared helper (for example an existing
    check script). Test files are never widened.
    """
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for value in values:
        relative = _normalize_relative(value)
        if not relative or relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
            raise PlanningError(f"unsafe allowed path: {value!r}")
        if ".." in Path(relative).parts:
            raise PlanningError(f"unsafe allowed path: {value!r}")
        if _is_test_target(relative):
            # Tests are frozen by the execution engine; declaring them as
            # writable is a configuration error, not a licence to edit them.
            continue
        if not allow_new_files and not (root / relative).is_file() and not (
            root / relative
        ).is_dir():
            raise PlanningError(f"allowed path does not exist: {relative}")
        if relative not in cleaned:
            cleaned.append(relative)
    return cleaned


def default_verify_command(targets: list[str]) -> str:
    """A conservative, runnable verify for a target set."""
    if targets and all(str(item).lower().endswith((".py", ".pyi")) for item in targets):
        joined = " ".join(f'"{item}"' for item in targets)
        return f"python -m py_compile {joined}"
    first = _normalize_relative(targets[0]) if targets else "README.md"
    if first.lower().endswith((".md", ".txt")):
        return (
            "python -c \"from pathlib import Path; "
            f"assert Path('{first}').is_file()\""
        )
    return (
        "python -c \"from pathlib import Path; "
        f"assert Path('{first}').is_file()\""
    )


def _planner_for_tier(tier: str) -> str:
    spec = tier_policy.TIERS.get(str(tier), tier_policy.TIERS["E2"])
    return spec.planner_level


def _propose_tier(
    phases: list[Mapping[str, Any]],
    targets: list[str],
    *,
    explicit: str | None,
    low_risk_opt_in: bool,
) -> str:
    if explicit:
        tier = normalize_tier(explicit)
    else:
        sensitive = any(is_sensitive_path(item) for item in targets)
        multi_phase = len(phases) > 1
        risky = any(
            int(work_order.get("level", 1) or 1) >= 2
            for phase in phases
            for work_order in phase.get("work_orders") or []
        )
        tier = "E3" if sensitive or multi_phase or risky else "E2"
    if tier == "E1":
        if not low_risk_opt_in:
            raise PlanningError(
                "E1 is low-risk opt-in: pass low_risk_opt_in=True explicitly"
            )
        blocked = [item for item in targets if is_sensitive_path(item)]
        if blocked:
            raise PlanningError(
                "E1 cannot target sensitive paths: " + ", ".join(blocked)
            )
    return tier


def _phase_from_subtask(
    subtask: Mapping[str, Any],
    *,
    index: int,
    phase_id: str,
    targets: list[str],
    root: Path,
    allow_new_files: bool,
    extra_allowed: list[str] | None = None,
) -> dict[str, Any]:
    max_rounds = int(subtask.get("max_rounds", 1) or 1)
    max_rounds = max(1, min(4, max_rounds))
    verify = str(subtask.get("verify_command") or "").strip()
    if not verify:
        verify = default_verify_command(targets)
    objective = str(subtask.get("objective") or subtask.get("title") or "").strip()
    if not objective:
        raise PlanningError(f"subtask {subtask.get('id') or index} has no objective")
    acceptance = str(
        subtask.get("acceptance_criteria")
        or f"verify exits 0 and {', '.join(targets)} contains the requested change"
    )
    return {
        "id": phase_id,
        "title": str(subtask.get("title") or f"Phase {index + 1}"),
        "depends_on": [],
        "allowed_paths": sorted(set(targets) | set(extra_allowed or [])),
        "forbidden_paths": list(DEFAULT_FORBIDDEN),
        "budget": {
            "requests": max(4, max_rounds + 4),
            "cost_usd": 0.50,
            "wall_seconds": 1200,
        },
        "work_orders": [
            {
                "id": str(subtask.get("id") or f"W{index + 1}"),
                "objective": objective,
                "boundaries": str(
                    subtask.get("boundaries")
                    or "Only the target files listed in this phase"
                ),
                "input_context": str(subtask.get("input_context") or ""),
                "acceptance_criteria": acceptance,
                "verify_command": verify,
                "target_files": targets,
                "level": int(subtask.get("level", 1) or 1),
                "max_rounds": max_rounds,
            }
        ],
        "rollback": {"mode": "last_checkpoint"},
        "cleanup": "disabled",
        "allow_new_files": bool(allow_new_files),
    }


def _normalize_subtasks(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = source.get("subtasks")
    if raw is None:
        raw = source.get("work_orders")
    if not isinstance(raw, list) or not raw:
        raise PlanningError("source plan has no subtasks/work_orders")
    subtasks: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise PlanningError(f"subtask {index} must be an object")
        subtasks.append(dict(item))
    return subtasks


def from_v1_plan(
    source: Mapping[str, Any],
    *,
    run_id: str,
    base_sha: str,
    root: str | Path = ".",
    tier: str | None = None,
    low_risk_opt_in: bool = False,
    allow_new_files: bool = False,
    verify_command: str | None = None,
) -> PlanAdapterResult:
    """Adapt an approved v1 plan into a validated v2 MasterPlan."""
    base = Path(root).expanduser().resolve()
    if not isinstance(source, Mapping):
        raise PlanningError("source plan must be an object")
    subtasks = _normalize_subtasks(source)
    if len(subtasks) > MAX_PHASES:
        raise PlanningError(
            f"v1 plan has {len(subtasks)} subtasks; v2 allows at most {MAX_PHASES} phases"
        )

    phases: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    subtask_to_phase: dict[str, str] = {}
    all_targets: list[str] = []
    job_allowed = _extra_allowed_paths(
        source.get("allowed_paths"), root=base, allow_new_files=allow_new_files,
    )
    job_verify = str(source.get("verify_command") or "").strip()
    for index, subtask in enumerate(subtasks):
        raw_id = str(subtask.get("id") or f"S{index + 1}")
        phase_id = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_id).strip("-") or f"P{index + 1}"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", phase_id):
            phase_id = f"P{index + 1}"
        candidate = phase_id
        suffix = 2
        while candidate in used_ids:
            candidate = f"{phase_id}-{suffix}"
            suffix += 1
        phase_id = candidate
        used_ids.add(phase_id)
        subtask_to_phase[raw_id] = phase_id

        targets = _require_targets(
            [str(item) for item in (subtask.get("target_files") or [])],
            root=base,
            allow_new_files=allow_new_files,
        )
        for item in targets:
            if item not in all_targets:
                all_targets.append(item)
        declared_scope = subtask.get("allowed_paths")
        if declared_scope is None:
            declared_scope = subtask.get("allowed")
        extra_allowed = list(job_allowed)
        for relative in _extra_allowed_paths(
            declared_scope, root=base, allow_new_files=allow_new_files,
        ):
            if relative not in extra_allowed:
                extra_allowed.append(relative)
        item = dict(subtask)
        if verify_command:
            item["verify_command"] = str(verify_command)
        elif job_verify and not str(item.get("verify_command") or "").strip():
            item["verify_command"] = job_verify
        phases.append(
            _phase_from_subtask(
                item,
                index=index,
                phase_id=phase_id,
                targets=targets,
                root=base,
                allow_new_files=allow_new_files,
                extra_allowed=extra_allowed,
            )
        )

    for index, subtask in enumerate(subtasks):
        phase = phases[index]
        raw_id = str(subtask.get("id") or f"S{index + 1}")
        deps: list[str] = []
        for dep in subtask.get("depends_on") or []:
            mapped = subtask_to_phase.get(str(dep))
            if not mapped:
                raise PlanningError(f"{raw_id}: unknown dependency {dep}")
            if mapped != phase["id"] and mapped not in deps:
                deps.append(mapped)
        phase["depends_on"] = deps

    proposed = _propose_tier(
        phases, all_targets, explicit=tier, low_risk_opt_in=low_risk_opt_in
    )
    total_requests = sum(int(phase["budget"]["requests"]) for phase in phases) + 8
    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(run_id),
        "goal": str(source.get("objective") or source.get("goal") or "").strip()
        or "Adapted plan",
        "base_sha": str(base_sha),
        "execution_tier": proposed,
        "planner_level": _planner_for_tier(proposed),
        "budget": {
            "requests": total_requests,
            "cost_usd": round(0.70 * max(1, len(phases)), 2),
            "wall_seconds": 1200 * max(1, len(phases)),
            "emergency_reserve_pct": 10,
        },
        "environment": {
            "env_allow": ["PATH", "PYTHONPATH"],
            "network_for_verify": False,
            "seed_files": [],
        },
        "phases": phases,
    }
    if proposed == "E1":
        plan["low_risk_opt_in"] = True

    validated = validate_plan(plan, worktree=base)
    digest = validated["plan_hash"]
    source_digest = _sha256_json(source)
    summary = (
        f"{proposed}/{plan['planner_level']}: {len(phases)} phase(s), "
        f"{len(all_targets)} target(s), tier proposed from "
        f"{'explicit request' if tier else 'risk heuristic'}"
    )
    return PlanAdapterResult(
        plan=plan,
        plan_hash=digest,
        tier=proposed,
        planner=str(plan["planner_level"]),
        source="v1_plan",
        source_hash=source_digest,
        summary=summary,
    )


def from_goal(
    goal: str,
    *,
    run_id: str,
    base_sha: str,
    root: str | Path = ".",
    targets: list[str] | None = None,
    verify_command: str | None = None,
    tier: str | None = None,
    low_risk_opt_in: bool = False,
    allow_new_files: bool = False,
    level: int | None = None,
    max_rounds: int = 2,
) -> PlanAdapterResult:
    """Build a one-phase v2 plan from a natural-language goal."""
    text = str(goal or "").strip()
    if not text:
        raise PlanningError("goal is empty")
    selected = [str(item) for item in (targets or [])]
    if not selected:
        selected = detect_targets(text, root, allow_new_files=allow_new_files)
    if not selected:
        raise PlanningError(
            "goal does not name a target file; pass targets explicitly "
            "(or --allow-new-files when the goal creates a new file)"
        )
    chosen_level = int(level) if level is not None else (3 if len(selected) > 1 else 1)
    subtask = {
        "id": "S1",
        "title": text[:120],
        "objective": text,
        "boundaries": "Only the target files listed by the adapter",
        "input_context": "Generated by v2.planner_adapter from a goal",
        "acceptance_criteria": (
            verify_command
            or f"verify exits 0 and {', '.join(selected)} contains the change"
        ),
        "verify_command": verify_command or default_verify_command(selected),
        "target_files": selected,
        "level": max(0, min(3, chosen_level)),
        "max_rounds": max(1, min(4, int(max_rounds or 1))),
        "depends_on": [],
    }
    source = {"job_id": "J1", "objective": text, "subtasks": [subtask]}
    result = from_v1_plan(
        source,
        run_id=run_id,
        base_sha=base_sha,
        root=root,
        tier=tier,
        low_risk_opt_in=low_risk_opt_in,
        allow_new_files=allow_new_files,
        verify_command=verify_command,
    )
    return PlanAdapterResult(
        plan=result.plan,
        plan_hash=result.plan_hash,
        tier=result.tier,
        planner=result.planner,
        source="goal",
        source_hash=_sha256_text(text + "\n" + "\n".join(selected)),
        summary=result.summary,
    )


def load_source_plan(path: str | Path) -> dict[str, Any]:
    source_path = Path(path)
    try:
        text = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanningError(f"cannot read plan: {exc}") from exc
    if source_path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise PlanningError(
                "YAML plans need PyYAML; convert the plan to JSON first"
            ) from exc
        try:
            payload = yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001
            raise PlanningError(f"cannot parse YAML plan: {exc}") from exc
    else:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PlanningError(f"cannot parse JSON plan: {exc}") from exc
    if not isinstance(payload, dict):
        raise PlanningError("plan root must be an object")
    return payload


def adapt_source(
    source: Mapping[str, Any],
    *,
    run_id: str,
    base_sha: str,
    root: str | Path = ".",
    tier: str | None = None,
    low_risk_opt_in: bool = False,
    allow_new_files: bool = False,
    verify_command: str | None = None,
) -> PlanAdapterResult:
    """Accept either a v1 plan, a tasks object or an existing v2 MasterPlan."""
    if str(source.get("schema_version") or "") == SCHEMA_VERSION and source.get("phases"):
        existing = dict(source)
        proposed = _propose_tier(
            list(existing.get("phases") or []),
            [
                str(target)
                for phase in existing.get("phases") or []
                for work_order in phase.get("work_orders") or []
                for target in work_order.get("target_files") or []
            ],
            explicit=tier or existing.get("execution_tier"),
            low_risk_opt_in=low_risk_opt_in
            or existing.get("low_risk_opt_in") is True,
        )
        existing["execution_tier"] = proposed
        existing["planner_level"] = _planner_for_tier(proposed)
        if proposed == "E1":
            existing["low_risk_opt_in"] = True
        validated = validate_plan(existing, worktree=Path(root).expanduser().resolve())
        return PlanAdapterResult(
            plan=existing,
            plan_hash=validated["plan_hash"],
            tier=proposed,
            planner=str(existing["planner_level"]),
            source="v2_plan",
            source_hash=_sha256_json(source),
            summary=f"{proposed}/{existing['planner_level']}: existing v2 plan",
        )
    return from_v1_plan(
        source,
        run_id=run_id,
        base_sha=base_sha,
        root=root,
        tier=tier,
        low_risk_opt_in=low_risk_opt_in,
        allow_new_files=allow_new_files,
        verify_command=verify_command,
    )


def adapt_file(
    path: str | Path,
    *,
    run_id: str,
    base_sha: str,
    root: str | Path = ".",
    tier: str | None = None,
    low_risk_opt_in: bool = False,
    allow_new_files: bool = False,
    verify_command: str | None = None,
) -> PlanAdapterResult:
    return adapt_source(
        load_source_plan(path),
        run_id=run_id,
        base_sha=base_sha,
        root=root,
        tier=tier,
        low_risk_opt_in=low_risk_opt_in,
        allow_new_files=allow_new_files,
        verify_command=verify_command,
    )


def write_plan(path: str | Path, result: PlanAdapterResult) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result.plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="goal/v1 plan -> v2 MasterPlan")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--goal")
    source.add_argument("--plan", help="v1 plan JSON/YAML or an existing v2 plan")
    parser.add_argument("--root", default=".")
    parser.add_argument("--run-id", default="plan-preview")
    parser.add_argument("--base-sha", default="HEAD")
    parser.add_argument("--tier", choices=("E1", "E2", "E3", "E4"), default=None)
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--verify", default=None)
    parser.add_argument("--e1-opt-in", dest="e1_opt_in", action="store_true")
    parser.add_argument("--allow-new-files", action="store_true")
    parser.add_argument("--out", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.goal:
            result = from_goal(
                args.goal,
                run_id=args.run_id,
                base_sha=args.base_sha,
                root=args.root,
                targets=args.target,
                verify_command=args.verify,
                tier=args.tier,
                low_risk_opt_in=args.e1_opt_in,
                allow_new_files=args.allow_new_files,
            )
        else:
            result = adapt_file(
                args.plan,
                run_id=args.run_id,
                base_sha=args.base_sha,
                root=args.root,
                tier=args.tier,
                low_risk_opt_in=args.e1_opt_in,
                allow_new_files=args.allow_new_files,
                verify_command=args.verify,
            )
    except (PlanningError, PlanValidationError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    if args.out:
        written = write_plan(args.out, result)
        result.as_dict()["out"] = str(written)
    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.summary)
        print(f"plan_hash: {result.plan_hash}")
        if args.out:
            print(f"out: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
