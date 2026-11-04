#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 2 tests: read-only windowed ACI and pre-apply edit validation."""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2.aci_context import ACIContext, validate_proposed_files  # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "v2_aci_context_tests"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ACIContextTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)
        (TEMP_ROOT / "src").mkdir(parents=True)
        (TEMP_ROOT / "tests").mkdir()
        self.calc = TEMP_ROOT / "src" / "calc.py"
        self.calc.write_text(
            "def add(a, b):\n    return a - b\n", encoding="utf-8"
        )
        self.test_file = TEMP_ROOT / "tests" / "test_calc.py"
        self.test_file.write_text("def test_x():\n    assert True\n", encoding="utf-8")
        self.aci = ACIContext(TEMP_ROOT)

    def tearDown(self):
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)

    def test_open_returns_numbered_window(self):
        result = self.aci.open("src/calc.py", start_line=1, window=2)
        self.assertEqual(result.status, "ok")
        self.assertIn("1: def add", result.output)
        self.assertIn("2:     return a - b", result.output)
        self.assertEqual(result.metadata["start_line"], 1)
        self.assertEqual(result.metadata["end_line"], 2)

    def test_scroll_moves_window(self):
        result = self.aci.scroll("src/calc.py", start_line=1, offset=2, window=1)
        self.assertEqual(result.status, "ok")
        self.assertIn("2:     return a - b", result.output)

    def test_search_symbol(self):
        result = self.aci.search_symbol("add")
        self.assertEqual(result.status, "ok")
        self.assertIn("calc.py", result.output)

    def test_syntax_invalid_edit_rejected(self):
        result = validate_proposed_files(
            TEMP_ROOT,
            {"src/calc.py": self.calc.read_text(encoding="utf-8")},
            {"src/calc.py": "def add(a, b)\n    return a + b\n"},
            {"target_files": ["src/calc.py"]},
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("syntax error" in issue for issue in result.issues))

    def test_path_escape_rejected(self):
        result = validate_proposed_files(
            TEMP_ROOT,
            {},
            {"../outside.py": "print('x')\n"},
            {"target_files": ["../outside.py"]},
        )
        self.assertFalse(result.ok)

    def test_test_hash_is_frozen(self):
        old_hash = sha(self.test_file)
        self.test_file.write_text("def test_x():\n    assert False\n", encoding="utf-8")
        result = validate_proposed_files(
            TEMP_ROOT,
            {"src/calc.py": self.calc.read_text(encoding="utf-8")},
            {"src/calc.py": "def add(a, b):\n    return a + b\n"},
            {"target_files": ["src/calc.py"]},
            test_hashes={"tests/test_calc.py": old_hash},
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("test file changed" in issue for issue in result.issues))

    def test_valid_edit_passes_without_writing(self):
        before = self.calc.read_text(encoding="utf-8")
        result = validate_proposed_files(
            TEMP_ROOT,
            {"src/calc.py": before},
            {"src/calc.py": "def add(a, b):\n    return a + b\n"},
            {"target_files": ["src/calc.py"]},
        )
        self.assertTrue(result.ok, result.issues)
        self.assertEqual(self.calc.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
