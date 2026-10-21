#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W4 Planner: mot cau muc tieu -> plan.json (subtasks + verify + depends_on).

Dung lai dung hop dong cua job.py: plan phai qua validate_plan, moi target phai
ton tai, verify phai chay duoc. Model dung chain W4 (Qwen3.8 Max -> GLM-5.3) va
tu ha xuong GLM-5.3-Flash-Free neu het quota.

CLI:
    python .agents/planner.py --goal "Them JWT auth cho API" --root . --out plan.json
    python .agents/planner.py --goal "..." --root . --submit
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import job                           # noqa: E402
import quota                         # noqa: E402
import router                        # noqa: E402
import workers                       # noqa: E402

MAX_SUBTASKS = 5
MAX_FILES_IN_PROMPT = 60
SKIP_DIRS = {".git", ".agents", "node_modules", "__pycache__", ".venv", "venv",
             "dist", "build", ".idea", ".vscode", ".mypy_cache", ".pytest_cache"}

PROMPT = """You are the planner of a coding swarm.
Return ONE JSON object only (no markdown, no prose).

GOAL (natural language, may be Vietnamese):
{goal}

PROJECT FILES (relative path and size):
{tree}

RULES
- 1 to {max_subtasks} subtasks, each independent and verifiable.
- target_files must be files that ALREADY EXIST in the project (1 to 3 files).
- level: 1 = one file; 2 = one file with logic/risk; 3 = two or more files.
- verify_command must be runnable on this machine and exit 0 when the subtask is done.
- depends_on may only reference earlier subtask ids; no cycles.
- boundaries should say exactly which files may be edited.
- Do not invent files that are not in the list above.

SCHEMA
{{"job_id": "J1", "objective": "...", "subtasks": [
  {{"id": "S1", "title": "...", "objective": "...", "boundaries": "...",
    "input_context": "...", "acceptance_criteria": "...",
    "verify_command": "python check.py", "target_files": ["a.py"],
    "sandbox_files": ["a.py", "check.py"], "level": 1, "max_rounds": 1,
    "depends_on": []}}
]}}

Return JSON only.
"""


class PlannerError(RuntimeError):
    pass


def project_tree(root, *, limit: int = MAX_FILES_IN_PROMPT) -> str:
    root = Path(root).resolve()
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        if len(lines) >= limit:
            break
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.is_file():
            lines.append(f"{relative} ({path.stat().st_size} B)")
    return "\n".join(lines) or "(empty project)"


def build_prompt(goal: str, root) -> str:
    return PROMPT.format(goal=str(goal).strip(), tree=project_tree(root),
                         max_subtasks=MAX_SUBTASKS)


def check_plan(plan: dict, root) -> list[str]:
    problems = list(job.validate_plan(plan))
    subtasks = plan.get("subtasks") if isinstance(plan, dict) else None
    if isinstance(subtasks, list) and len(subtasks) > MAX_SUBTASKS:
        problems.append(f"too many subtasks ({len(subtasks)} > {MAX_SUBTASKS})")
    for item in subtasks or []:
        if not isinstance(item, dict):
            continue
        for relative in item.get("target_files") or []:
            if not (Path(root) / relative).exists():
                problems.append(f"{item.get('id')}: target does not exist: {relative}")
    return problems


def parse_plan(text: str) -> dict:
    data = workers.parse_json_object(text)
    if not isinstance(data, dict):
        raise PlannerError("planner did not return a JSON object")
    if not data.get("job_id"):
        data["job_id"] = "J-auto"
    if not data.get("objective"):
        data["objective"] = ""
    return data


def _models(root) -> list[str]:
    tracker = quota.QuotaTracker(root=root).refresh()
    chain = [router.ROUTES["W4"]["main"], router.ROUTES["W4"]["escalate"],
             router.MODELS["glm-flash-free"], router.MODELS["deepseek-flash-free"]]
    models: list[str] = []
    for model in chain:
        if model and model not in models and tracker.remaining(model)["ok"]:
            models.append(model)
    return models or [router.MODELS["glm-flash-free"]]


def _ask(prompt: str, models: list[str], root, runner) -> str:
    failures: list[str] = []
    for model in models:
        try:
            if runner is not None:
                return runner(model, prompt)
            base = Path(tempfile.gettempdir()) / "opencode"
            base.mkdir(parents=True, exist_ok=True)
            workdir = tempfile.mkdtemp(prefix="planner_", dir=str(base))
            text, _usage = workers.run_opencode_cli(
                workers.DEFAULT_OPENCODE_BIN, model, prompt,
                agent="build", cwd=workdir, timeout=240)
            return text
        except Exception as exc:                    # noqa: BLE001
            failures.append(f"{model}: {str(exc)[:200]}")
    raise PlannerError("all planner models failed: " + " | ".join(failures)[:600])


def generate(goal: str, *, root, runner=None, max_attempts: int = 2,
             models: list[str] | None = None) -> dict:
    goal = str(goal or "").strip()
    if not goal:
        raise ValueError("goal is empty")
    models = models or _models(root)
    base = build_prompt(goal, root)
    feedback = ""
    last: list[str] = []
    for _attempt in range(1, max_attempts + 1):
        text = _ask(base + feedback, models, root, runner)
        try:
            plan = parse_plan(text)
        except Exception as exc:                    # noqa: BLE001
            last = [f"invalid JSON: {exc}"]
            feedback = ("\n\nPREVIOUS ANSWER WAS INVALID: " + str(exc)[:300]
                        + "\nReturn valid JSON only, following the schema.")
            continue
        last = check_plan(plan, root)
        if not last:
            return plan
        feedback = ("\n\nPREVIOUS PLAN WAS INVALID:\n- " + "\n- ".join(last[:8])
                    + "\nFix those problems and return JSON only.")
    raise PlannerError("planner could not produce a valid plan: " + "; ".join(last[:8]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="W4 planner: goal -> plan.json")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--root", default=".")
    parser.add_argument("--out", default=".agents/tmp/auto_plan.json")
    parser.add_argument("--submit", action="store_true", help="submit plan + print dashboard URL")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    plan = generate(args.goal, root=args.root)
    out = Path(args.out)
    if not out.is_absolute():
        out = Path(args.root) / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"plan written: {out} ({len(plan['subtasks'])} subtasks)")

    if args.submit:
        info = job.submit_plan(plan, args.root, source=str(args.out))
        print(f"job: {info['job_id']}")
        print(f"dashboard: http://127.0.0.1:{args.port}/?token={info['token']}")
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
