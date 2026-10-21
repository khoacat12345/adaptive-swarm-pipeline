"""Render a full, human-readable plan to Markdown.

The dashboard only shows key points. This module produces the long form:
phases (dependency waves), every subtask field, verification contracts,
risks and a reproduce command. No model calls, no writes outside
`.agents/tmp/plans/`.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path


def safe_job_id(value) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "plan")).strip("._")
    return text or "plan"


def markdown_path(root, job_id) -> Path:
    return Path(root) / ".agents" / "tmp" / "plans" / (safe_job_id(job_id) + ".md")


def dependency_waves(subtasks: list[dict]) -> list[list[dict]]:
    """Group subtasks into waves: no deps first, then dependants."""
    items = [item for item in (subtasks or []) if isinstance(item, dict)]
    by_id = {str(item.get("id")): item for item in items}
    remaining = list(items)
    waves: list[list[dict]] = []
    while remaining:
        wave = []
        for item in remaining:
            deps = [str(dep) for dep in (item.get("depends_on") or [])]
            if all(dep not in {str(other.get("id")) for other in remaining}
                   for dep in deps if dep in by_id):
                wave.append(item)
        if not wave:                         # cycle: keep deterministic, never loop
            wave = [remaining[0]]
        waves.append(wave)
        done = {id(item) for item in wave}
        remaining = [item for item in remaining if id(item) not in done]
    return waves


def _phase_name(index: int, waves: int) -> str:
    if index == 0:
        return "Foundation"
    if index == waves - 1:
        return "Final wave"
    return "Builds on phase " + str(index)


def _cell(value, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        text = text[: limit - 1] + "..."
    return text or "-"


def _bullet_files(files) -> str:
    return ", ".join("`" + str(item) + "`" for item in (files or [])) or "-"


def _risks(plan: dict, subtasks: list[dict]) -> list[str]:
    risks: list[str] = []
    missing = [str(item.get("id")) for item in subtasks
               if not str(item.get("verify_command") or "").strip()]
    if missing:
        risks.append("Missing verify command: " + ", ".join(missing) +
                     ". These subtasks cannot be applied.")
    files: dict[str, list[str]] = {}
    for item in subtasks:
        for file in item.get("target_files") or []:
            files.setdefault(str(file), []).append(str(item.get("id")))
    shared = {file: ids for file, ids in files.items() if len(ids) > 1}
    if shared:
        risks.append("Shared target files: " + "; ".join(
            f"`{file}` ({', '.join(ids)})" for file, ids in shared.items()) +
            ". Keep an explicit dependency or the writes may overwrite each other.")
    unknown = []
    ids = {str(item.get("id")) for item in subtasks}
    for item in subtasks:
        for dep in item.get("depends_on") or []:
            if str(dep) not in ids:
                unknown.append(f"{item.get('id')} -> {dep}")
    if unknown:
        risks.append("Unknown dependencies: " + ", ".join(unknown) + ".")
    if any(int(item.get("level") or 0) >= 3 for item in subtasks):
        risks.append("Level 3 transaction: all target files roll back together.")
    if not risks:
        risks.append("No structural risk detected by the local checks.")
    return risks


def _agents(state: dict) -> list[tuple[str, str]]:
    team = (state or {}).get("team") or {}
    rows = [
        ("Lead", "orchestrator (only writer)"),
        ("Coder", team.get("coder") or "not selected yet"),
        ("Fallback", team.get("fallback") or "-"),
        ("Reviewer", team.get("reviewer") or "heuristic"),
        ("Verify", team.get("verify") or "machine command per subtask"),
    ]
    labels = {"security": "Security", "qa": "QA", "perf": "Perf", "docs": "Docs"}
    try:
        import workers
        for role, model in workers.WAVE_ROLES.items():
            rows.append((labels.get(role, role.title()), model))
    except Exception:                       # noqa: BLE001
        pass
    return rows


def build_markdown(plan: dict, decision: dict | None = None,
                   state: dict | None = None, run: dict | None = None) -> str:
    plan = plan or {}
    decision = decision or {}
    state = state or {}
    subtasks = [item for item in (plan.get("subtasks") or []) if isinstance(item, dict)]
    waves = dependency_waves(subtasks)
    applied = {str(item.get("id")) for item in (state.get("subtasks") or [])
               if item.get("status") in ("applied", "done")}
    lines: list[str] = []
    lines.append(f"# Plan {plan.get('job_id') or '-'}")
    lines.append("")
    lines.append(f"- **Objective:** {_cell(plan.get('objective'), 500)}")
    lines.append(f"- **Status:** {state.get('phase') or 'unknown'}")
    lines.append(f"- **Approval:** {decision.get('status') or 'pending'}")
    lines.append(f"- **Source:** `{plan.get('_source') or '.agents/tmp/plan.json'}`")
    lines.append(f"- **Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- **Subtasks:** {len(subtasks)} | **Phases:** {len(waves)}")
    lines.append("")
    lines.append("## Agents")
    lines.append("")
    lines.append("| Role | Model |")
    lines.append("|---|---|")
    for role, model in _agents(state):
        lines.append(f"| {role} | `{model}` |")
    lines.append("")
    lines.append("## Execution phases")
    lines.append("")
    for phase_index, wave in enumerate(waves, start=1):
        lines.append(f"### Phase {phase_index} | {_phase_name(phase_index - 1, len(waves))}")
        lines.append("")
        lines.append(f"- **Wave:** {phase_index}")
        lines.append(f"- **Subtasks:** {', '.join(str(item.get('id')) for item in wave)}")
        lines.append("")
        for item in wave:
            status = str(item.get("status") or ("applied" if str(item.get("id")) in applied else "pending"))
            lines.append(f"#### {item.get('id')} | {item.get('title') or 'Untitled'}")
            lines.append("")
            lines.append(f"- **Status:** {status}")
            lines.append(f"- **Level:** {item.get('level', 1)} | "
                         f"**Rounds:** {item.get('max_rounds', 1)}")
            lines.append(f"- **Objective:** {_cell(item.get('objective'), 600)}")
            lines.append(f"- **Acceptance:** {_cell(item.get('acceptance_criteria'), 600)}")
            lines.append(f"- **Boundaries:** {_cell(item.get('boundaries'), 600)}")
            lines.append(f"- **Input context:** {_cell(item.get('input_context'), 600)}")
            lines.append(f"- **Target files:** {_bullet_files(item.get('target_files'))}")
            lines.append(f"- **Sandbox files:** {_bullet_files(item.get('sandbox_files'))}")
            lines.append(f"- **Depends on:** {', '.join(item.get('depends_on') or []) or 'nothing'}")
            lines.append(f"- **Verify:**")
            lines.append("")
            lines.append("```text")
            lines.append(str(item.get("verify_command") or "MISSING"))
            lines.append("```")
            lines.append("")
    lines.append("## Verification contract")
    lines.append("")
    lines.append("| Subtask | Command |")
    lines.append("|---|---|")
    for item in subtasks:
        command = str(item.get("verify_command") or "MISSING").replace("|", "\\|")
        lines.append(f"| {item.get('id')} | `{command}` |")
    lines.append("")
    lines.append("The real machine runs these commands. Exit code 0 is the only pass.")
    lines.append("")
    lines.append("## Risks / checks")
    lines.append("")
    for risk in _risks(plan, subtasks):
        lines.append(f"- {risk}")
    lines.append("")
    if run:
        lines.append("## Last run")
        lines.append("")
        lines.append(f"- **Run:** `{run.get('run_id') or '-'}`")
        lines.append(f"- **Status:** {run.get('status') or '-'}")
        lines.append(f"- **Requests:** {run.get('requests', '-')}")
        lines.append(f"- **Duration:** {run.get('duration_ms', '-')} ms")
        lines.append(f"- **Models:** {', '.join(run.get('models') or []) or '-'}")
        lines.append("")
    lines.append("## Reproduce")
    lines.append("")
    lines.append("```powershell")
    lines.append("python .agents/job.py --plan " + str(plan.get("_source") or ".agents/tmp/plan.json") +
                 " --root . --wait --open")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def write_markdown(root, plan: dict, decision: dict | None = None,
                   state: dict | None = None, run: dict | None = None) -> Path:
    path = markdown_path(root, plan.get("job_id"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_markdown(plan, decision, state, run), encoding="utf-8")
    return path


def _cli(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Render the plan as Markdown.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    root = Path(args.root)
    try:
        plan = json.loads((root / ".agents" / "tmp" / "plan.json").read_text(encoding="utf-8"))
        decision = json.loads((root / ".agents" / "tmp" / "plan_decision.json").read_text(encoding="utf-8"))
        state = json.loads((root / ".agents" / "tmp" / "job_state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read plan state: {exc}")
        return 2
    text = build_markdown(plan, decision, state)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
