#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bo test KHO cho benchmark chon model (Phase 5B).

Moi domain 1 bai, dung de phan biet model that su, khong phai task de. Tat ca
deu do bang MAY (verify exit 0), khong phu thuoc pytest/third-party.

Domains (khong dau, de tien group):
  backend   : LRU cache O(1), eviction dung thu tu (thuat toan + bien)
  frontend  : JS classNames (node) - xu ly object/nested/falsy/so
  db        : sqlite migration - them cot NOT NULL + backfill + idempotent
  security  : path traversal + command injection
  tests     : viet test bat dung loi (phan biet ban buggy vs ban dung)
  architect : viet plan.json phan ra buoc dung thu tu + du phu thuoc
  docs      : viet API.md chinh xac tu ma nguon (AST check)

Moi task theo dung hop dong cua eval_suite.py: id/title/prompt/target/files/
sandbox_files/verify/expect_level. `domain` va `difficulty` la metadata them.
"""

from __future__ import annotations

import sys

PY = f'"{sys.executable}"'
NODE = "node"


def _cmd(name: str) -> str:
    return f"{PY} {name}"


def _node_cmd(name: str) -> str:
    return f"{NODE} {name}"


# --------------------------------------------------------------------------
# 1. backend: LRU cache O(1)
# --------------------------------------------------------------------------
B1 = {
    "id": "B1",
    "domain": "backend",
    "difficulty": "hard",
    "kind": "bug",
    "title": "LRU cache eviction sai thu tu",
    "prompt": (
        "Sua lru.py: lop LRUCache hien evict SAI phan tu (xoa phan tu moi dung gan "
        "nhat thay vi cu nhat). get() phai cap nhat thu tu, put() phai evict phan tu "
        "it dung nhat khi vuot capacity. Tat ca phai O(1) amotise. Khong doi ten lop/"
        "phuong thuc, khong doi chu ky test."
    ),
    "paths": ["lru.py"],
    "expect_level": 2,
    "target": "lru.py",
    "files": {
        "lru.py": (
            "class LRUCache:\n"
            "    def __init__(self, capacity):\n"
            "        self.cap = capacity\n"
            "        self.data = {}\n"
            "        self.order = []\n"
            "\n"
            "    def get(self, key):\n"
            "        return self.data.get(key, -1)\n"
            "\n"
            "    def put(self, key, value):\n"
            "        if key in self.data:\n"
            "            self.order.remove(key)\n"
            "        self.data[key] = value\n"
            "        self.order.append(key)\n"
            "        if len(self.order) > self.cap:\n"
            "            old = self.order.pop(0)\n"
            "            if old in self.data:\n"
            "                del self.data[old]\n"
        ),
        "test_lru.py": (
            "import lru\n"
            "\n"
            "c = lru.LRUCache(2)\n"
            "c.put(1, 1)\n"
            "c.put(2, 2)\n"
            "assert c.get(1) == 1        # get(1) -> 1 thanh moi dung gan nhat\n"
            "c.put(3, 3)                 # evict key 2 (it dung nhat), KHONG phai 1\n"
            "assert c.get(2) == -1\n"
            "assert c.get(1) == 1\n"
            "assert c.get(3) == 3\n"
            "\n"
            "c.put(4, 4)                 # evict key 1\n"
            "assert c.get(1) == -1\n"
            "assert c.get(3) == 3\n"
            "assert c.get(4) == 4\n"
            "\n"
            "# update key da ton tai khong tao phan tu thua\n"
            "c.put(4, 44)\n"
            "assert c.get(4) == 44\n"
            "print('OK B1')\n"
        ),
    },
    "sandbox_files": ["lru.py", "test_lru.py"],
    "verify": _cmd("test_lru.py"),
}

# --------------------------------------------------------------------------
# 2. frontend: classNames (node)
# --------------------------------------------------------------------------
B2 = {
    "id": "B2",
    "domain": "frontend",
    "difficulty": "hard",
    "kind": "bug",
    "title": "classNames xu ly object/nested/falsy",
    "prompt": (
        "Sua className.js: ham classNames(...args) hien chi nhan chuoi va mang phang. "
        "No phai xu ly: object (key true -> them key), mang LONG NHAU, so/boolean/falsy "
        "bo qua (0, '', false, null, undefined), khong trung lap, noi bang 1 dau cach. "
        "Khong doi ten export."
    ),
    "paths": ["className.js"],
    "expect_level": 2,
    "target": "className.js",
    "files": {
        "className.js": (
            "function classNames(...args) {\n"
            "  const out = [];\n"
            "  for (const a of args) {\n"
            "    if (typeof a === 'string') out.push(a);\n"
            "    else if (Array.isArray(a)) out.push(...a);\n"
            "  }\n"
            "  return out.join(' ');\n"
            "}\n"
            "module.exports = classNames;\n"
        ),
        "test_className.js": (
            "const assert = require('assert');\n"
            "const cn = require('./className.js');\n"
            "\n"
            "assert.strictEqual(cn('a', 'b'), 'a b');\n"
            "assert.strictEqual(cn({ a: true, b: false, c: true }), 'a c');\n"
            "assert.strictEqual(cn(['a', ['b', ['c']]]), 'a b c');\n"
            "assert.strictEqual(cn('x', null, undefined, 0, '', false, 'y'), 'x y');\n"
            "assert.strictEqual(cn(['a', { b: true }, 0, [['c']]]), 'a b c');\n"
            "assert.strictEqual(cn('dup', 'dup', { dup: true }), 'dup');\n"
            "console.log('OK B2');\n"
        ),
    },
    "sandbox_files": ["className.js", "test_className.js"],
    "verify": _node_cmd("test_className.js"),
}

# --------------------------------------------------------------------------
# 3. db: sqlite migration NOT NULL + backfill + idempotent
# --------------------------------------------------------------------------
B3 = {
    "id": "B3",
    "domain": "db",
    "difficulty": "hard",
    "kind": "bug",
    "title": "migration them cot NOT NULL + idempotent",
    "prompt": (
        "Sua migrate.py: ham migrate(conn) phai them cot `status` (TEXT NOT NULL DEFAULT "
        "'new') vao bang `users` NEU chua co, backfill cac dong cu gia tri 'new' ma KHONG "
        "mat du lieu, va PHAN IDEMPOTENT (chay 2 lan khong loi, khong trung cot). Dung "
        "sqlite3 stdlib. Khong doi ten ham."
    ),
    "paths": ["migrate.py"],
    "expect_level": 2,
    "target": "migrate.py",
    "files": {
        "migrate.py": (
            "import sqlite3\n"
            "\n"
            "\n"
            "def migrate(conn):\n"
            "    cur = conn.cursor()\n"
            "    cur.execute(\"ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'new'\")\n"
        ),
        "test_migrate.py": (
            "import sqlite3\n"
            "import migrate\n"
            "\n"
            "\n"
            "def _cols(cur, table):\n"
            "    return [r[1] for r in cur.execute(f\"PRAGMA table_info({table})\")]\n"
            "\n"
            "conn = sqlite3.connect(':memory:')\n"
            "conn.execute(\"CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)\")\n"
            "conn.execute(\"INSERT INTO users (name) VALUES ('a'), ('b'), ('c')\")\n"
            "conn.commit()\n"
            "\n"
            "migrate.migrate(conn)   # lan 1\n"
            "migrate.migrate(conn)   # lan 2 -> phai idempotent\n"
            "conn.commit()\n"
            "\n"
            "cur = conn.cursor()\n"
            "assert 'status' in _cols(cur, 'users'), 'thieu cot status'\n"
            "rows = list(cur.execute('SELECT name, status FROM users ORDER BY id'))\n"
            "assert rows == [('a', 'new'), ('b', 'new'), ('c', 'new')], rows\n"
            "print('OK B3')\n"
        ),
    },
    "sandbox_files": ["migrate.py", "test_migrate.py"],
    "verify": _cmd("test_migrate.py"),
}

# --------------------------------------------------------------------------
# 4. security: path traversal + command injection
# --------------------------------------------------------------------------
B4 = {
    "id": "B4",
    "domain": "security",
    "difficulty": "hard",
    "kind": "bug",
    "title": "path traversal + command injection",
    "prompt": (
        "Sua secure.py: ham resolve(root, user_path) phai TU CHOI (raise ValueError) moi "
        "duong dan thoat ra ngoai root (../, ..\\\\, absolute). Ham run(cmd, arg) phai TU "
        "CHOI arg chua ky tu dac biet (| ; & $ ` > < \\n). Khong dung shell. Khong doi ten ham."
    ),
    "paths": ["secure.py"],
    "expect_level": 2,
    "target": "secure.py",
    "files": {
        "secure.py": (
            "import os\n"
            "\n"
            "\n"
            "def resolve(root, user_path):\n"
            "    return os.path.join(root, user_path)\n"
            "\n"
            "\n"
            "def run(cmd, arg):\n"
            "    return os.system(cmd + \" \" + arg)\n"
        ),
        "test_secure.py": (
            "import os\n"
            "import secure\n"
            "\n"
            "root = os.path.abspath('.')\n"
            "assert secure.resolve(root, 'ok.txt') == os.path.join(root, 'ok.txt')\n"
            "\n"
            "def _blocked(fn):\n"
            "    try:\n"
            "        fn()\n"
            "    except ValueError:\n"
            "        return True\n"
            "    return False\n"
            "\n"
            "assert _blocked(lambda: secure.resolve(root, '../../etc/passwd'))\n"
            "assert _blocked(lambda: secure.resolve(root, '..\\\\..\\\\windows'))\n"
            "assert _blocked(lambda: secure.resolve(root, 'C:\\\\Windows\\\\system32'))\n"
            "\n"
            "assert _blocked(lambda: secure.run('echo', 'x; rm -rf /'))\n"
            "assert _blocked(lambda: secure.run('echo', 'a | b'))\n"
            "assert _blocked(lambda: secure.run('echo', 'a$b'))\n"
            "print('OK B4')\n"
        ),
    },
    "sandbox_files": ["secure.py", "test_secure.py"],
    "verify": _cmd("test_secure.py"),
}

# --------------------------------------------------------------------------
# 5. tests: viet test bat dung loi
# --------------------------------------------------------------------------
_STORE_CORRECT = (
    "class Counter:\n"
    "    def __init__(self):\n"
    "        self.n = 0\n"
    "\n"
    "    def add(self, x):\n"
    "        self.n += x\n"
    "\n"
    "    def remove(self, x):\n"
    "        self.n -= x\n"
)
_STORE_BUGGY = (
    "class Counter:\n"
    "    def __init__(self):\n"
    "        self.n = 0\n"
    "\n"
    "    def add(self, x):\n"
    "        self.n += x\n"
    "\n"
    "    def remove(self, x):\n"
    "        self.n -= x\n"
    "        self.n -= x\n"
)
_B5_CHECKER = (
    "import subprocess\n"
    "import sys\n"
    "from pathlib import Path\n"
    "\n"
    "CORRECT = " + repr(_STORE_CORRECT) + "\n"
    "BUGGY = " + repr(_STORE_BUGGY) + "\n"
    "root = Path(__file__).parent\n"
    "store = root / 'store.py'\n"
    "test = root / 'test_store.py'\n"
    "\n"
    "store.write_text(BUGGY, encoding='utf-8')\n"
    "r1 = subprocess.run([sys.executable, str(test)], capture_output=True)\n"
    "store.write_text(CORRECT, encoding='utf-8')\n"
    "r2 = subprocess.run([sys.executable, str(test)], capture_output=True)\n"
    "store.write_text(CORRECT, encoding='utf-8')\n"
    "\n"
    "if r1.returncode == 0:\n"
    "    print('FAIL: test khong bat duoc bug (pass tren ban buggy)')\n"
    "    sys.exit(1)\n"
    "if r2.returncode != 0:\n"
    "    print('FAIL: test sai tren ban dung')\n"
    "    print(r2.stdout.decode()[-500:])\n"
    "    sys.exit(1)\n"
    "print('OK B5')\n"
)
B5 = {
    "id": "B5",
    "domain": "tests",
    "difficulty": "hard",
    "kind": "bug",
    "title": "viet test bat loi remove tru 2 lan",
    "prompt": (
        "Viet test_store.py de BAT loi trong store.py: ham Counter.remove() hien TRU gia "
        "tri HAI lan (loi). Test cua ban phai FAIL khi chay tren ban buggy va PASS tren ban "
        "dung (remove chi tru 1 lan). Dung assert, chi can 1 so truong hop; khong tao file "
        "moi ngoai test_store.py."
    ),
    "paths": ["test_store.py"],
    "expect_level": 2,
    "target": "test_store.py",
    "files": {
        "store.py": _STORE_CORRECT,
        "test_store.py": (
            "import store\n"
            "\n"
            "# TODO: viet assert de bat loi remove tru 2 lan\n"
            "print('chua test gi')\n"
        ),
        "check_tests.py": _B5_CHECKER,
    },
    "sandbox_files": ["store.py", "test_store.py", "check_tests.py"],
    "verify": _cmd("check_tests.py"),
}

# --------------------------------------------------------------------------
# 6. architect: plan.json phan ra buoc dung thu tu
# --------------------------------------------------------------------------
B6 = {
    "id": "B6",
    "domain": "architect",
    "difficulty": "hard",
    "kind": "bug",
    "title": "viet plan.json phan ra buoc co thu tu",
    "prompt": (
        "Viet plan.json phan ra tinh nang 'dang ky + dang nhap + rate limit cho API' thanh "
        "cac buoc nho, DUNG THU TU (co phu thuoc). JSON phai co dang: "
        '{"objective": "...", "tasks": [{"id":"...","title":"...",'
        '"target_files":["..."],"verify":"...","depends_on":["..."]}]}. '
        "Phai co it nhat 3 buoc, bao gom ca buoc auth va buoc rate_limit; thu tu khong "
        "vong lap (acyclic); moi buoc phai co target_files va verify."
    ),
    "paths": ["plan.json"],
    "expect_level": 2,
    "target": "plan.json",
    "files": {
        "plan.json": "{}",
        "check_plan.py": (
            "import json\n"
            "import sys\n"
            "from pathlib import Path\n"
            "\n"
            "plan = json.loads(Path('plan.json').read_text(encoding='utf-8'))\n"
            "tasks = plan.get('tasks')\n"
            "assert isinstance(tasks, list) and len(tasks) >= 3, 'can >= 3 buoc'\n"
            "\n"
            "ids = [t.get('id') for t in tasks]\n"
            "assert all(ids), 'moi buoc can id'\n"
            "assert len(ids) == len(set(ids)), 'id trung lap'\n"
            "for t in tasks:\n"
            "    assert t.get('target_files'), f\"{t.get('id')}: thieu target_files\"\n"
            "    assert t.get('verify'), f\"{t.get('id')}: thieu verify\"\n"
            "    for dep in (t.get('depends_on') or []):\n"
            "        assert dep in ids, f\"{t.get('id')}: dep {dep} khong ton tai\"\n"
            "\n"
            "# acyclic\n"
            "for t in tasks:\n"
            "    seen = set()\n"
            "    stack = [t.get('id')]\n"
            "    while stack:\n"
            "        cur = stack.pop()\n"
            "        if cur in seen:\n"
            "            continue\n"
            "        seen.add(cur)\n"
            "        for other in tasks:\n"
            "            if other.get('id') == cur:\n"
            "                stack.extend(other.get('depends_on') or [])\n"
            "    assert t.get('id') not in set(\n"
            "        d for o in tasks for d in (o.get('depends_on') or [])), 'self-dep'\n"
            "\n"
            "blob = json.dumps(plan).lower()\n"
            "assert 'auth' in blob and 'rate' in blob, 'thieu auth hoac rate limit'\n"
            "print('OK B6')\n"
        ),
    },
    "sandbox_files": ["plan.json", "check_plan.py"],
    "verify": _cmd("check_plan.py"),
}

# --------------------------------------------------------------------------
# 7. docs: API.md chinh xac tu ma nguon
# --------------------------------------------------------------------------
B7 = {
    "id": "B7",
    "domain": "docs",
    "difficulty": "hard",
    "kind": "bug",
    "title": "viet API.md chinh xac tu lib.py",
    "prompt": (
        "Viet API.md (markdown) mo ta CHINH XAC cac ham trong lib.py: dung ten ham, dung "
        "danh sach tham so (ke ca keyword-only va tham so mac dinh). Khong bia them ham khong "
        "ton tai. check_docs.py se doi chieu bang AST."
    ),
    "paths": ["API.md"],
    "expect_level": 2,
    "target": "API.md",
    "files": {
        "lib.py": (
            "def parse_query(raw, *, default_limit=10, allow_empty=False):\n"
            "    pass\n"
            "\n"
            "\n"
            "def fetch_records(conn, query, limit):\n"
            "    pass\n"
            "\n"
            "\n"
            "async def stream_events(source, handler, retries=3):\n"
            "    pass\n"
        ),
        "API.md": "# API\n\n(trong)\n",
        "check_docs.py": (
            "import ast\n"
            "import sys\n"
            "from pathlib import Path\n"
            "\n"
            "tree = ast.parse(Path('lib.py').read_text(encoding='utf-8'))\n"
            "funcs = {}\n"
            "for node in ast.walk(tree):\n"
            "    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):\n"
            "        args = [a.arg for a in node.args.args]\n"
            "        args += [a.arg for a in node.args.kwonlyargs]\n"
            "        funcs[node.name] = args\n"
            "\n"
            "doc = Path('API.md').read_text(encoding='utf-8')\n"
            "for name, args in funcs.items():\n"
            "    assert name in doc, f'API.md thieu ham {name}'\n"
            "    for arg in args:\n"
            "        assert arg in doc, f'API.md thieu tham so {arg} cua {name}'\n"
            "print('OK B7')\n"
        ),
    },
    "sandbox_files": ["lib.py", "API.md", "check_docs.py"],
    "verify": _cmd("check_docs.py"),
}

HARD_TASKS = [B1, B2, B3, B4, B5, B6, B7]
