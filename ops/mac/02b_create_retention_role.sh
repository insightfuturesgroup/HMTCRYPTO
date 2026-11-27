#!/usr/bin/env bash
set -euo pipefail
root=$(git rev-parse --show-toplevel)
cd "$root"
python_bin="$HOME/hmt-venv-3.13.10/bin/python"
[[ -x "$python_bin" ]] || { echo 'STOP: Python 3.13.10 venv unavailable'; exit 1; }
exec "$python_bin" tools/ops_create_retention_role.py "$@"
