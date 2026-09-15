#!/usr/bin/env bash
# Run the project's own verification commands on Linux, using the Linux-side venv.
set -uo pipefail
REPO=/mnt/d/work/AI-Native/AI-Native-Evals
VENV="$HOME/ai-native-evals-linux-venv"
cd "$REPO"
export PATH="$HOME/.local/bin:$PATH"
echo "=== platform ==="
"$VENV/bin/python" -c "
import sys; sys.path.insert(0,'src')
from ai_native_evals.runs import docker_cli as d
print('is_windows:', d.is_windows(), '| describe:', d.describe())
print('docker_argv:', d.docker_argv('ps'))
from pathlib import Path
print('host_path   :', d.host_path(Path('/tmp/x')))
print('gateway     :', d.host_gateway())
print('shared_temp :', d.shared_temp_root())
print('build_hint  :', d.build_hint())
"
echo "=== ruff ==="
"$VENV/bin/python" -m ruff check src tests
echo "=== pytest ==="
"$VENV/bin/python" -m pytest -q -p no:warnings --tb=short 2>&1 | tail -40
