#!/usr/bin/env sh
# Open the Adaptive Swarm dashboard on Linux/macOS.
set -eu
dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$dir"
exec python3 .agents/dashboard.py --root . --port 8080 --open
