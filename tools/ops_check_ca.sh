#!/usr/bin/env bash
set -euo pipefail
# Read-only: never display certificate contents or alter permissions.
ca_path="${1:-${HOME}/hmt-secrets/supabase-ca.crt}"

passed=true
report() {
  local label="$1"
  local result="$2"
  printf '%s: %s\n' "$label" "$result"
  [[ "$result" == PASS ]] || passed=false
}

if [[ $# -le 1 && -e "$ca_path" ]]; then
  report exists PASS
else
  report exists FAIL
fi

if [[ $# -le 1 && ! -L "$ca_path" ]]; then
  report 'not a symlink' PASS
else
  report 'not a symlink' FAIL
fi

mode=""
if [[ $# -le 1 && -e "$ca_path" ]]; then
  if [[ "$(uname -s)" == Darwin ]]; then
    mode="$(stat -f '%Lp' "$ca_path" 2>/dev/null || true)"
  else
    mode="$(stat -c '%a' "$ca_path" 2>/dev/null || true)"
  fi
fi
if [[ "$mode" == 600 ]]; then
  report 'mode 600' PASS
else
  report 'mode 600' FAIL
fi

if [[ $# -le 1 ]] && openssl x509 -in "$ca_path" -noout >/dev/null 2>&1; then
  report 'parses with openssl x509' PASS
  parses=true
else
  report 'parses with openssl x509' FAIL
  parses=false
fi

if [[ "$parses" == true ]] && openssl x509 -in "$ca_path" -noout -checkend 0 \
  >/dev/null 2>&1; then
  report 'not expired' PASS
else
  report 'not expired' FAIL
fi

[[ "$passed" == true ]] || exit 1
openssl x509 -in "$ca_path" -noout -sha256 -fingerprint -enddate 2>/dev/null
