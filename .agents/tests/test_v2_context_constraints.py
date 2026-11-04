#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 4 tests: memory caps, read-only constitution, locks and recovery."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from v2.context_runtime import (  # noqa: E402
    ConstrainedContext,
    MemoryConstraintError,
    MemoryLock,
    recover_jsonl,
)

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode" / "v2_context_runtime_tests"


class ContextRuntimeTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)
        TEMP_ROOT.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)

    def test_file_character_cap(self):
        context = ConstrainedContext(max_file_characters=10, max_core_characters=100)
        with self.assertRaises(MemoryConstraintError):
            context.add("large", "x" * 11)

    def test_constitution_is_read_only_and_never_evicted(self):
        context = ConstrainedContext(max_file_characters=100, max_core_characters=25)
        context.add("constitution.md", "constitution", read_only=True)
        context.add("constitution_hash", "abc", read_only=True)
        context.add("working", "x" * 20, tier="working")
        packed = context.pack()
        labels = [item.label for item in packed.items]
        self.assertIn("constitution.md", labels)
        self.assertIn("constitution_hash", labels)
        with self.assertRaises(MemoryConstraintError):
            context.add("constitution.md", "overwrite", read_only=True)

    def test_eviction_removes_non_read_only_first(self):
        context = ConstrainedContext(max_file_characters=100, max_core_characters=20)
        context.add("constitution.md", "law", read_only=True)
        context.add("old-archival", "x" * 20, tier="archival")
        packed = context.pack()
        self.assertIn("old-archival", packed.evicted)
        self.assertEqual([item.label for item in packed.items], ["constitution.md"])

    def test_memory_lock_is_exclusive(self):
        path = TEMP_ROOT / "memory.jsonl"
        with MemoryLock(path, timeout=0.2):
            with self.assertRaises(MemoryConstraintError):
                with MemoryLock(path, timeout=0.05):
                    pass
        with MemoryLock(path, timeout=0.2):
            pass

    def test_corrupt_jsonl_is_quarantined_and_recovered(self):
        path = TEMP_ROOT / "lessons.jsonl"
        path.write_text(
            json.dumps({"signature": "ok"}) + "\n"
            + "{broken json\n"
            + json.dumps({"signature": "ok2"}) + "\n",
            encoding="utf-8",
        )
        result = recover_jsonl(path, quarantine_dir=TEMP_ROOT / "quarantine")
        self.assertTrue(result.recovered)
        self.assertEqual(result.corrupt_lines, 1)
        self.assertEqual(result.valid_lines, 2)
        self.assertTrue(Path(result.quarantine).exists())
        self.assertNotIn("broken json", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
