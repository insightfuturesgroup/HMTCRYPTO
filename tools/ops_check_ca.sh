#!/usr/bin/env bash
set -euo pipefail
# Read-only: never display certificate contents or alter permissions.
ca_path="${1:-${HOME}/hmt-secrets/supabase-ca.crt}"
[[ $# -le 1 && -f "$ca_path" && ! -L "$ca_path" ]] || exit 1
if [[ "$(uname -s)" == Darwin ]]; then
  mode="$(stat -f '%Lp' "$ca_path" 2>/dev/null)"
else
  mode="$(stat -c '%a' "$ca_path" 2>/dev/null)"
fi
[[ "$mode" == 600 ]] || exit 1
openssl x509 -in "$ca_path" -noout -sha256 -fingerprint -enddate 2>/dev/null
