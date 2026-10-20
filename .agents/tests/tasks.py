#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""10 task mau cho smoke suite (Task 1.7).

5 bug logic, 3 refactor, 2 task bay (file dai, indent la). Moi task la mot du an
nho doc lap: file nguon + file test that (stdlib, khong can pytest). Duong dan
verify duoc render bang dung interpreter dang chay de khong phu thuoc PATH.

`patches` la kich ban cho ScriptedCoder: phan tu thu i la patch cho vong i+1.
Voi T05, vong 1 co tinh sai de kiem tra co che feedback + thu lai.
"""

from __future__ import annotations

import sys

PY = f'"{sys.executable}"'


def _cmd(name: str) -> str:
    return f"{PY} {name}"


# --------------------------------------------------------------------------
# 1. Off-by-one
# --------------------------------------------------------------------------
T01 = {
    "id": "T01",
    "kind": "bug",
    "title": "off-by-one trong sum_range",
    "prompt": "Sua dong `for i in range(n - 1)` thanh `for i in range(n)` trong calc.py "
              "de tinh tong dung.",
    "paths": ["calc.py"],
    "expect_level": 1,
    "target": "calc.py",
    "files": {
        "calc.py": (
            "def sum_range(n):\n"
            "    total = 0\n"
            "    for i in range(n - 1):\n"
            "        total += i\n"
            "    return total\n"
        ),
        "test_calc.py": (
            "import calc\n"
            "\n"
            "def _check(name, got, want):\n"
            "    if got != want:\n"
            "        raise AssertionError(f\"{name}: got {got!r}, want {want!r}\")\n"
            "\n"
            "_check('sum_range(5)', calc.sum_range(5), 10)\n"
            "_check('sum_range(0)', calc.sum_range(0), 0)\n"
            "print('OK T01')\n"
        ),
    },
    "sandbox_files": ["calc.py", "test_calc.py"],
    "verify": _cmd("test_calc.py"),
    "patches": [
        {"format": "replace",
         "edits": [{"search": "    for i in range(n - 1):", "replace": "    for i in range(n):"}]},
    ],
    "expect": {"status": "applied", "patch_level": "replace"},
}

# --------------------------------------------------------------------------
# 2. divide phai raise ValueError
# --------------------------------------------------------------------------
T02 = {
    "id": "T02",
    "kind": "bug",
    "title": "divide nuot loi chia cho 0",
    "prompt": "Ham divide trong calc.py sai logic: khi chia cho 0 phai raise ValueError "
              "thay vi tra ve 0.",
    "paths": ["calc.py"],
    "expect_level": 2,
    "target": "calc.py",
    "files": {
        "calc.py": (
            "def divide(a, b):\n"
            "    if b == 0:\n"
            "        return 0\n"
            "    return a / b\n"
        ),
        "test_calc.py": (
            "import calc\n"
            "\n"
            "try:\n"
            "    calc.divide(1, 0)\n"
            "except ValueError:\n"
            "    pass\n"
            "else:\n"
            "    raise AssertionError('divide(1, 0) phai raise ValueError')\n"
            "\n"
            "if abs(calc.divide(6, 3) - 2.0) > 1e-9:\n"
            "    raise AssertionError('divide(6, 3) sai')\n"
            "print('OK T02')\n"
        ),
    },
    "sandbox_files": ["calc.py", "test_calc.py"],
    "verify": _cmd("test_calc.py"),
    "patches": [
        {"format": "symbol",
         "edits": [{"name": "divide",
                    "replace": (
                        "def divide(a, b):\n"
                        "    if b == 0:\n"
                        "        raise ValueError(\"b phai khac 0\")\n"
                        "    return a / b\n"
                    )}]},
    ],
    "expect": {"status": "applied", "patch_level": "symbol"},
}

# --------------------------------------------------------------------------
# 3. mutable default argument
# --------------------------------------------------------------------------
T03 = {
    "id": "T03",
    "kind": "bug",
    "title": "mutable default argument",
    "prompt": "Bug trong store.py: add_item dung list mac dinh nen bi chia se giua cac lan goi.",
    "paths": ["store.py"],
    "expect_level": 2,
    "target": "store.py",
    "files": {
        "store.py": (
            "def add_item(item, items=[]):\n"
            "    items.append(item)\n"
            "    return items\n"
            "\n"
            "\n"
            "def total(items):\n"
            "    return sum(items)\n"
        ),
        "test_store.py": (
            "import store\n"
            "\n"
            "a = store.add_item('x')\n"
            "b = store.add_item('y')\n"
            "if a != ['x']:\n"
            "    raise AssertionError(f'lan dau phai la [\\'x\\'], nhan duoc {a!r}')\n"
            "if b != ['y']:\n"
            "    raise AssertionError(f'lan hai phai la [\\'y\\'], nhan duoc {b!r}')\n"
            "print('OK T03')\n"
        ),
    },
    "sandbox_files": ["store.py", "test_store.py"],
    "verify": _cmd("test_store.py"),
    "patches": [
        {"format": "replace",
         "edits": [{
             "search": ("def add_item(item, items=[]):\n"
                        "    items.append(item)\n"
                        "    return items"),
             "replace": ("def add_item(item, items=None):\n"
                         "    if items is None:\n"
                         "        items = []\n"
                         "    items.append(item)\n"
                         "    return items"),
         }]},
    ],
    "expect": {"status": "applied", "patch_level": "replace"},
}

# --------------------------------------------------------------------------
# 4. split khong gioi han
# --------------------------------------------------------------------------
T04 = {
    "id": "T04",
    "kind": "bug",
    "title": "split('=')[1] lam mat du lieu",
    "prompt": "Doi cach split trong parser.py: dung split('=', 1) de giu nguyen phan gia tri.",
    "paths": ["parser.py"],
    "expect_level": 1,
    "target": "parser.py",
    "files": {
        "parser.py": (
            "def parse_pair(line):\n"
            "    key, value = line.split(\"=\")[0], line.split(\"=\")[1]\n"
            "    return key.strip(), value.strip()\n"
        ),
        "test_parser.py": (
            "import parser\n"
            "\n"
            "def _check(name, got, want):\n"
            "    if got != want:\n"
            "        raise AssertionError(f\"{name}: got {got!r}, want {want!r}\")\n"
            "\n"
            "_check('a=b=c', parser.parse_pair('a=b=c'), ('a', 'b=c'))\n"
            "_check('x = 1 ', parser.parse_pair('x = 1 '), ('x', '1'))\n"
            "print('OK T04')\n"
        ),
    },
    "sandbox_files": ["parser.py", "test_parser.py"],
    "verify": _cmd("test_parser.py"),
    "patches": [
        {"format": "replace",
         "edits": [{"search": "    key, value = line.split(\"=\")[0], line.split(\"=\")[1]",
                    "replace": "    key, value = line.split(\"=\", 1)"}]},
    ],
    "expect": {"status": "applied", "patch_level": "replace"},
}

# --------------------------------------------------------------------------
# 5. vong 1 sai -> feedback -> vong 2 dung
# --------------------------------------------------------------------------
T05 = {
    "id": "T05",
    "kind": "bug",
    "title": "clamp sai thu tu min/max (co thu lai)",
    "prompt": "Ham clamp trong bounds.py tra ve sai gia tri. Sua lai cho dung.",
    "paths": ["bounds.py"],
    "expect_level": 2,
    "target": "bounds.py",
    "files": {
        "bounds.py": (
            "def clamp(value, low, high):\n"
            "    return min(low, max(high, value))\n"
        ),
        "test_bounds.py": (
            "import bounds\n"
            "\n"
            "def _check(name, got, want):\n"
            "    if got != want:\n"
            "        raise AssertionError(f\"{name}: got {got!r}, want {want!r}\")\n"
            "\n"
            "_check('clamp(5,0,3)', bounds.clamp(5, 0, 3), 3)\n"
            "_check('clamp(-1,0,3)', bounds.clamp(-1, 0, 3), 0)\n"
            "_check('clamp(2,0,3)', bounds.clamp(2, 0, 3), 2)\n"
            "print('OK T05')\n"
        ),
    },
    "sandbox_files": ["bounds.py", "test_bounds.py"],
    "verify": _cmd("test_bounds.py"),
    "patches": [
        # Vong 1: search khong ton tai trong file -> ladder that bai -> feedback
        {"format": "replace",
         "edits": [{"search": "    return max(high, min(low, value))",
                    "replace": "    return max(low, min(high, value))"}]},
        # Vong 2: dung
        {"format": "replace",
         "edits": [{"search": "    return min(low, max(high, value))",
                    "replace": "    return max(low, min(high, value))"}]},
    ],
    "expect": {"status": "applied", "patch_level": "replace", "min_rounds": 2},
}

# --------------------------------------------------------------------------
# 6. refactor: tach ham (whole file)
# --------------------------------------------------------------------------
T06 = {
    "id": "T06",
    "kind": "refactor",
    "title": "tach dieu kien ra ham rieng",
    "prompt": "Refactor utils.py: tach phan kiem tra dieu kien ra ham rieng de de doc.",
    "paths": ["utils.py"],
    "expect_level": 2,
    "target": "utils.py",
    "files": {
        "utils.py": (
            "def process(items):\n"
            "    result = []\n"
            "    for item in items:\n"
            "        if item is not None:\n"
            "            if item > 0:\n"
            "                result.append(item * 2)\n"
            "    return result\n"
        ),
        "test_utils.py": (
            "import utils\n"
            "\n"
            "def _check(name, got, want):\n"
            "    if got != want:\n"
            "        raise AssertionError(f\"{name}: got {got!r}, want {want!r}\")\n"
            "\n"
            "_check('loc', utils.process([1, None, -2, 3]), [2, 6])\n"
            "_check('rong', utils.process([]), [])\n"
            "print('OK T06')\n"
        ),
    },
    "sandbox_files": ["utils.py", "test_utils.py"],
    "verify": _cmd("test_utils.py"),
    "patches": [
        {"format": "whole",
         "content": (
             "def _keep(item):\n"
             "    return item is not None and item > 0\n"
             "\n"
             "\n"
             "def process(items):\n"
             "    return [item * 2 for item in items if _keep(item)]\n"
         )},
    ],
    "expect": {"status": "applied", "patch_level": "whole"},
}

# --------------------------------------------------------------------------
# 7. refactor: thay than ham (symbol)
# --------------------------------------------------------------------------
T07 = {
    "id": "T07",
    "kind": "refactor",
    "title": "format_row xu ly score None",
    "prompt": "Refactor report.py: format_row phai hien thi 'n/a' khi score la None.",
    "paths": ["report.py"],
    "expect_level": 2,
    "target": "report.py",
    "files": {
        "report.py": (
            "def format_row(name, score):\n"
            "    return name + \": \" + str(score)\n"
            "\n"
            "\n"
            "def format_rows(rows):\n"
            "    return [format_row(name, score) for name, score in rows]\n"
        ),
        "test_report.py": (
            "import report\n"
            "\n"
            "def _check(name, got, want):\n"
            "    if got != want:\n"
            "        raise AssertionError(f\"{name}: got {got!r}, want {want!r}\")\n"
            "\n"
            "_check('none', report.format_row('a', None), 'a: n/a')\n"
            "_check('co diem', report.format_row('a', 1), 'a: 1')\n"
            "_check('nhieu dong', report.format_rows([('a', 1), ('b', None)]), ['a: 1', 'b: n/a'])\n"
            "print('OK T07')\n"
        ),
    },
    "sandbox_files": ["report.py", "test_report.py"],
    "verify": _cmd("test_report.py"),
    "patches": [
        {"format": "symbol",
         "edits": [{"name": "format_row",
                    "replace": (
                        "def format_row(name, score):\n"
                        "    if score is None:\n"
                        "        return name + \": n/a\"\n"
                        "    return name + \": \" + str(score)\n"
                    )}]},
    ],
    "expect": {"status": "applied", "patch_level": "symbol"},
}

# --------------------------------------------------------------------------
# 8. refactor: doi ten + sua falsy (2 edit)
# --------------------------------------------------------------------------
T08 = {
    "id": "T08",
    "kind": "refactor",
    "title": "doi ten hang so + sua falsy 0",
    "prompt": "Refactor config.py: doi ten DEFAULT_RETRIES thanh MAX_RETRIES va sua build "
              "de retries=0 van dung.",
    "paths": ["config.py"],
    "expect_level": 2,
    "target": "config.py",
    "files": {
        "config.py": (
            "DEFAULT_TIMEOUT = 10\n"
            "DEFAULT_RETRIES = 1\n"
            "\n"
            "\n"
            "def build(timeout=None, retries=None):\n"
            "    return {\n"
            "        \"timeout\": timeout or DEFAULT_TIMEOUT,\n"
            "        \"retries\": retries or DEFAULT_RETRIES,\n"
            "    }\n"
        ),
        "test_config.py": (
            "import config\n"
            "\n"
            "mac_dinh = config.build()\n"
            "if mac_dinh != {'timeout': 10, 'retries': 1}:\n"
            "    raise AssertionError(f'mac dinh sai: {mac_dinh!r}')\n"
            "\n"
            "khong_retry = config.build(retries=0)\n"
            "if khong_retry['retries'] != 0:\n"
            "    raise AssertionError(f'retries=0 bi doi thanh {khong_retry[\"retries\"]!r}')\n"
            "print('OK T08')\n"
        ),
    },
    "sandbox_files": ["config.py", "test_config.py"],
    "verify": _cmd("test_config.py"),
    "patches": [
        {"format": "replace",
         "edits": [
             {"search": "DEFAULT_RETRIES = 1", "replace": "MAX_RETRIES = 1"},
             {"search": ("def build(timeout=None, retries=None):\n"
                         "    return {\n"
                         "        \"timeout\": timeout or DEFAULT_TIMEOUT,\n"
                         "        \"retries\": retries or DEFAULT_RETRIES,\n"
                         "    }"),
              "replace": ("def build(timeout=None, retries=None):\n"
                          "    return {\n"
                          "        \"timeout\": DEFAULT_TIMEOUT if timeout is None else timeout,\n"
                          "        \"retries\": MAX_RETRIES if retries is None else retries,\n"
                          "    }")},
         ]},
    ],
    "expect": {"status": "applied", "patch_level": "replace"},
}

# --------------------------------------------------------------------------
# 9. BAY: file dai hon 150 dong, coder tra ve whole-file
# --------------------------------------------------------------------------
def _long_module(broken: bool = True) -> str:
    lines = [f"# dong {index:03d}: filler de file dai hon 150 dong" for index in range(180)]
    lines += ["", "", "def target(x):", "    return x - 1" if broken else "    return x + 1"]
    return "\n".join(lines) + "\n"


T09 = {
    "id": "T09",
    "kind": "trap",
    "title": "file 184 dong + coder tra whole-file",
    "prompt": "Sua loi trong bigmodule.py: ham target tra ve sai (x - 1 thay vi x + 1).",
    "paths": ["bigmodule.py"],
    "expect_level": 2,
    "target": "bigmodule.py",
    "files": {
        "bigmodule.py": _long_module(broken=True),
        "test_bigmodule.py": (
            "import bigmodule\n"
            "\n"
            "if bigmodule.target(5) != 6:\n"
            "    raise AssertionError(f'target(5) phai la 6, nhan duoc {bigmodule.target(5)!r}')\n"
            "print('OK T09')\n"
        ),
    },
    "sandbox_files": ["bigmodule.py", "test_bigmodule.py"],
    "verify": _cmd("test_bigmodule.py"),
    "patches": [
        # Coder vi pham luat 150 dong -> ladder phai tu ha bac 0 -> 1
        {"format": "whole", "content": _long_module(broken=False)},
    ],
    "expect": {"status": "applied", "patch_level": "replace", "downgraded": True},
}

# --------------------------------------------------------------------------
# 10. BAY: indent la (tab + trailing space)
# --------------------------------------------------------------------------
T10 = {
    "id": "T10",
    "kind": "trap",
    "title": "file indent tab + trailing space",
    "prompt": "Sua loi trong legacy.py: ham total phai bo qua gia tri None.",
    "paths": ["legacy.py"],
    "expect_level": 2,
    "target": "legacy.py",
    "files": {
        "legacy.py": (
            "def total(values):\n"
            "\tresult = 0\n"
            "\tfor value in values:\n"
            "\t\tresult += value\n"
            "\treturn result   \n"
        ),
        "test_legacy.py": (
            "import legacy\n"
            "\n"
            "def _check(name, got, want):\n"
            "    if got != want:\n"
            "        raise AssertionError(f\"{name}: got {got!r}, want {want!r}\")\n"
            "\n"
            "_check('co None', legacy.total([1, None, 2]), 3)\n"
            "_check('rong', legacy.total([]), 0)\n"
            "print('OK T10')\n"
        ),
    },
    "sandbox_files": ["legacy.py", "test_legacy.py"],
    "verify": _cmd("test_legacy.py"),
    "patches": [
        # SEARCH dung 4 space + trailing space, file dung tab -> phai chuan hoa
        {"format": "replace",
         "edits": [{
             "search": ("        for value in values:\n"
                        "            result += value\n"
                        "        return result   "),
             "replace": ("        for value in values:\n"
                         "            if value is not None:\n"
                         "                result += value\n"
                         "        return result"),
         }]},
    ],
    "expect": {"status": "applied", "patch_level": "replace"},
}

TASKS = [T01, T02, T03, T04, T05, T06, T07, T08, T09, T10]
