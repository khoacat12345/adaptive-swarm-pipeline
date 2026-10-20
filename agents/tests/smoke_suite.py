"""`python -m agents.tests.smoke_suite` -> chay that `.agents/tests/smoke_suite.py`."""

from __future__ import annotations

import runpy
from pathlib import Path

TARGET = Path(__file__).resolve().parents[2] / ".agents" / "tests" / "smoke_suite.py"
runpy.run_path(str(TARGET), run_name="__main__")
