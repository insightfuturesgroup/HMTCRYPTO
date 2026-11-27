#!/usr/bin/env bash
set -euo pipefail
# Read-only: no configuration, credentials or identifiers are printed or saved.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
command -v python3 >/dev/null || { echo 'FAIL: python3 unavailable'; exit 1; }
exec python3 "$script_dir/verify_infra.py"
