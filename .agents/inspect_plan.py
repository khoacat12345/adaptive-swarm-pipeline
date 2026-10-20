#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inspect_plan.py — doc config.json va in ra ke hoach model theo vai tro.

Muc dich: tra loi cau hoi "ke hoach nay co chay duoc khong" ma khong phai mo
file JSON 10 KB bang mat thuong.

  python .agents/inspect_plan.py                  # doc .agents/config.json
  python .agents/inspect_plan.py path/to/plan.json  # doc file bat ky
  python .agents/inspect_plan.py --root D:\\project

Dac diem: tu nhan dien encoding. File do PowerShell tao bang `>` la UTF-16LE kem
BOM, file do Python ghi la UTF-8 — ca hai deu doc duoc.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent
ROLES = ("coder", "reviewer", "security", "architect", "test_writer")
ROLE_LABEL = {"coder": "coder", "reviewer": "reviewer", "security": "security",
              "architect": "architect", "test_writer": "test_writer"}


def read_json_any_encoding(path: Path) -> dict:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
    else:
        text = raw.decode("utf-8", errors="replace")
    # Cho phep file co dong thong bao cua CLI phia truoc JSON
    start = text.find("{")
    if start < 0:
        raise ValueError(f"khong tim thay JSON trong {path}")
    data, _ = json.JSONDecoder().raw_decode(text[start:])
    return data


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--root")]
    if argv[1:2] == ["--root"]:
        target = Path(argv[2]) / ".agents" / "config.json"
    elif args:
        target = Path(args[0])
    else:
        target = AGENTS_DIR / "config.json"

    if not target.is_file():
        print(f"Khong thay file: {target}", file=sys.stderr)
        return 2
    try:
        plan = read_json_any_encoding(target)
    except (OSError, ValueError) as exc:
        print(f"Khong doc duoc {target}: {exc}", file=sys.stderr)
        return 2

    registry = plan.get("models") or {}
    constraints = plan.get("constraints") or {}
    verify = plan.get("verify") or {}
    results = verify.get("results") or {}

    print("=" * 78)
    print(f"  KE HOACH MODEL  |  {target}")
    print("=" * 78)
    print(f"  scouted_at  : {plan.get('scouted_at')}")
    print(f"  catalog     : {(plan.get('catalog') or {}).get('source')}"
          f"  ({len(registry)} model dung duoc)")
    cost_policy = plan.get("cost_policy")
    free_only = "ON" if constraints.get("free_only") else "off"
    print(f"  chi phi     : cost_policy={cost_policy} | free_only={free_only}")
    print(f"  verify      : {verify.get('mode', 'off')}")

    for role in ROLES:
        entry = (plan.get("roles") or {}).get(role) or {}
        chain = entry.get("chain") or []
        print("")
        print(f"  {ROLE_LABEL[role].upper()}")
        for index, model_id in enumerate(chain):
            meta = registry.get(model_id) or {}
            tag = "primary " if index == 0 else f"du phong{index}"
            flags = (f"cost={meta.get('cost_tier', '?'):<8} "
                     f"provider={meta.get('provider', '?'):<8} "
                     f"ho={meta.get('family', '?'):<16} "
                     f"free={'yes' if meta.get('is_free') else 'no '}")
            mark = ""
            if index == 0 and role in results:      # verify chi test primary, khong test du phong
                mark = "  [verify OK]" if results[role].get("ok") else "  [verify FAIL]"
            print(f"    {tag:<10} {model_id:<44} {flags}{mark}")

    warnings = plan.get("warnings") or []
    if warnings:
        print("")
        print("  CANH BAO")
        for warning in warnings:
            print(f"    [!] {warning}")

    failed = [role for role, res in results.items() if not res.get("ok")]
    if failed:
        print("")
        print(f"  {len(failed)}/{len(results)} vai tro verify THAT BAI: {', '.join(failed)}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
