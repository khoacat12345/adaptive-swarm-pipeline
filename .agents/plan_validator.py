#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plan Validator (Task 3.3) - bo tham dinh ke hoach cua Lead.

Pipeline van la "ban dong" (semi-dynamic): Lead duoc tu chon node tuy chon theo
recipe, nhung ke hoach phai qua bo tham dinh nay truoc khi chay. Validator CHAN
CUNG cac truong hop sau:

  1. node type la khong nam trong danh muc dong
  2. > 6 node, hoac > 2 nhanh song song
  3. thieu `verify_command` cua node SystemBot
  4. bo node bat buoc (lead/coder/systembot) ma khong co `reason`

Moi loi deu tra ve kem ly do ro rang; khong co "reject mo ho".
"""

from __future__ import annotations

# Danh muc dong: them node moi phai sua file nay (co y - khong cho tu do mo rong).
ALLOWED_NODE_TYPES = ("architect", "coder", "reviewer", "security", "perf",
                      "qa", "systembot", "lead")
REQUIRED_NODE_TYPES = ("lead", "coder", "systembot")
MAX_NODES = 6
MAX_PARALLEL_BRANCHES = 2


def _node_type(node) -> str:
    if not isinstance(node, dict):
        return ""
    return str(node.get("type") or node.get("id") or "").strip().lower()


def validate_plan(plan) -> dict:
    """Tra ve {"ok": bool, "errors": [...], "warnings": [...], "plan": plan}."""
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(plan, dict):
        return {"ok": False, "errors": ["plan phai la object JSON"], "warnings": [],
                "plan": plan}

    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return {"ok": False, "errors": ["plan thieu danh sach 'nodes'"], "warnings": [],
                "plan": plan}

    if len(nodes) > MAX_NODES:
        errors.append(f"qua nhieu node: {len(nodes)} > {MAX_NODES}")

    seen_ids: set[str] = set()
    types_present: set[str] = set()
    for index, node in enumerate(nodes):
        node_type = _node_type(node)
        if node_type not in ALLOWED_NODE_TYPES:
            errors.append(
                f"node #{index} co type la '{node_type}' - chi cho phep "
                f"{', '.join(ALLOWED_NODE_TYPES)}"
            )
            continue
        types_present.add(node_type)
        node_id = str((node or {}).get("id") or node_type).strip()
        if node_id in seen_ids:
            errors.append(f"node id bi trung: '{node_id}'")
        seen_ids.add(node_id)

    # SystemBot bat buoc phai co lenh verify that
    system_nodes = [node for node in nodes
                    if isinstance(node, dict) and _node_type(node) == "systembot"]
    if not system_nodes:
        errors.append("thieu node 'systembot' (khong co gi kiem chung ket qua)")
    else:
        command = str(system_nodes[0].get("verify_command") or "").strip()
        if not command:
            errors.append("node 'systembot' thieu 'verify_command'")

    # Nhanh song song
    parallel = plan.get("parallel") or []
    if not isinstance(parallel, list):
        errors.append("'parallel' phai la danh sach cac nhom node")
        parallel = []
    if len(parallel) > MAX_PARALLEL_BRANCHES:
        errors.append(f"qua nhieu nhom song song: {len(parallel)} > {MAX_PARALLEL_BRANCHES}")
    for index, group in enumerate(parallel):
        if not isinstance(group, list) or not group:
            errors.append(f"nhom song song #{index} phai la danh sach node khong rong")
            continue
        if len(group) > MAX_PARALLEL_BRANCHES:
            errors.append(
                f"nhom song song #{index} co {len(group)} nhanh > {MAX_PARALLEL_BRANCHES}"
            )
        for member in group:
            if str(member).strip() not in seen_ids:
                errors.append(f"nhom song song #{index} tro toi node khong ton tai: {member}")

    # Bo node bat buoc phai co reason
    omitted = plan.get("omitted") or []
    if not isinstance(omitted, list):
        errors.append("'omitted' phai la danh sach")
        omitted = []
    omitted_types: dict[str, str] = {}
    for item in omitted:
        if not isinstance(item, dict):
            errors.append("moi muc trong 'omitted' phai la object {type, reason}")
            continue
        node_type = str(item.get("type") or "").strip().lower()
        reason = str(item.get("reason") or "").strip()
        if not node_type:
            errors.append("muc 'omitted' thieu 'type'")
            continue
        if not reason:
            errors.append(f"bo node '{node_type}' nhung thieu 'reason'")
        omitted_types[node_type] = reason

    for required in REQUIRED_NODE_TYPES:
        if required in types_present:
            continue
        if required in omitted_types and omitted_types[required]:
            warnings.append(f"node bat buoc '{required}' bi bo: {omitted_types[required]}")
            continue
        errors.append(f"thieu node bat buoc '{required}' va khong co 'reason'")

    return {"ok": not errors, "errors": errors, "warnings": warnings, "plan": plan}


def build_recipe_c_plan(work_order, *, include_reviewer: bool = True,
                        verify_command: str | None = None) -> dict:
    """Ke hoach mac dinh cua Recipe C, de validator tham dinh nhu moi plan khac."""
    command = (verify_command or getattr(work_order, "verify_command", "") or "").strip()
    nodes = [
        {"id": "lead", "type": "lead", "optional": False},
        {"id": "coder", "type": "coder", "optional": False,
         "max_rounds": getattr(work_order, "max_rounds", 2)},
    ]
    if include_reviewer and getattr(work_order, "level", 2) >= 2:
        nodes.append({"id": "reviewer", "type": "reviewer", "optional": True})
    nodes.append({"id": "systembot", "type": "systembot", "optional": False,
                  "verify_command": command})
    plan = {
        "plan_id": f"recipe_c:{getattr(work_order, 'task_id', '') or 'task'}",
        "nodes": nodes,
        "parallel": [],
        "omitted": [],
    }
    return plan


def build_parser():
    import argparse
    parser = argparse.ArgumentParser(description="Tham dinh ke hoach pipeline.")
    parser.add_argument("--plan", required=True, help="file JSON chua plan")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None) -> int:
    import json
    from pathlib import Path
    args = build_parser().parse_args(argv)
    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"khong doc duoc plan '{args.plan}': {exc}")
        return 2
    verdict = validate_plan(plan)
    if args.json:
        print(json.dumps({key: value for key, value in verdict.items() if key != "plan"},
                         ensure_ascii=False, indent=2))
    else:
        print("PLAN: " + ("HOP LE" if verdict["ok"] else "BI TU CHOI"))
        for error in verdict["errors"]:
            print(f"  - {error}")
        for warning in verdict["warnings"]:
            print(f"  ! {warning}")
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
