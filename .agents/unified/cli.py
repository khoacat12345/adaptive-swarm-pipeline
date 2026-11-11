# -*- coding: utf-8 -*-
"""Installed console entrypoint for the Adaptive Swarm Pipeline.

The implementation lives in :mod:`v2.swarm_cli` next to this package.  When
the project is installed from the wheel, the hidden ``.agents`` tree is mapped
to the top-level ``agents`` package; this shim puts that package directory on
``sys.path`` so the runtime's existing absolute imports (``v2``, ``unified``,
``core``) continue to work unchanged.

The console script in ``pyproject.toml`` points here as
``agents.unified.cli:main``.  A literal ``.agents.unified.cli:main`` entrypoint
is not valid Python because module names cannot begin with a dot.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Sequence


def _ensure_runtime_path() -> None:
    # In a wheel: <site-packages>/agents/unified/cli.py -> <site-packages>/agents
    # In a checkout: <repo>/.agents/unified/cli.py -> <repo>/.agents
    runtime_root = Path(__file__).resolve().parents[1]
    value = str(runtime_root)
    if value not in sys.path:
        sys.path.insert(0, value)


def main(argv: Sequence[str] | None = None) -> int:
    """Delegate to the unified ``swarm`` CLI implementation."""

    _ensure_runtime_path()
    from v2.swarm_cli import main as swarm_main

    return swarm_main(list(argv) if argv is not None else None)


if __name__ == "__main__":
    raise SystemExit(main())
