#!/usr/bin/env bash
set -euo pipefail
root=$(git rev-parse --show-toplevel)
cd "$root"
python_bin="$HOME/hmt-venv-3.13.10/bin/python"
[[ -x "$python_bin" ]] || { echo 'STOP: Python 3.13.10 venv unavailable'; exit 1; }
echo 'PLAN: migrate, grant retention EXECUTE, verify reviewed schema.'
echo 'REQUIRE: ops-1 complete; CI green; reviewed SHA; app/workers stopped.'
"$python_bin" tools/migrate_mumbai.py
"$python_bin" tools/ops_post_migration.py
echo 'NEXT: GET-only Data API review must show hmt_crypto absent.'
