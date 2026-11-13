#!/usr/bin/env sh
# Open the Unified War Room on Linux/macOS (loopback only).
set -eu
dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$dir"
exec python3 .agents/dashboard.py --root . --port 8501 --open
