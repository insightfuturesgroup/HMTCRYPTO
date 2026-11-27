#!/usr/bin/env bash
set -euo pipefail
set +x

if ! command -v psql >/dev/null 2>&1; then
  printf '%s\n' 'STOP: psql is missing. Run: brew install libpq' \
    'Then, in bash or zsh: export PATH="$(brew --prefix libpq)/bin:$PATH"'
  exit 1
fi
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec python3 "$root/tools/ops_create_runtime_role.py" "$@"
