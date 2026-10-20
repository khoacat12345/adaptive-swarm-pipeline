"""`python -m agents.scout` -> chay that `.agents/scout.py` (giu nguyen sys.argv)."""

from __future__ import annotations

import runpy
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / ".agents" / "scout.py"
runpy.run_path(str(TARGET), run_name="__main__")
