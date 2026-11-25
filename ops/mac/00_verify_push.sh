#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' 'Read-only check: branch, Git status and commits not recorded in origin tracking ref.'
printf '%s\n' 'This script does not push, fetch, change files or read operator configuration.'
root=$(git rev-parse --show-toplevel)
cd "$root"
branch=$(git branch --show-current)
if [[ "$branch" != 'codex/audit-fixes' ]]; then
  printf '%s\n' 'STOP: open the codex/audit-fixes worktree in VS Code.' >&2
  exit 1
fi

git status -sb
if ! git show-ref --verify --quiet refs/remotes/origin/codex/audit-fixes; then
  printf '%s\n' 'STOP: use Publish Branch in VS Code, then rerun this check.' >&2
  exit 1
fi

pending=$(git log --oneline origin/codex/audit-fixes..HEAD)
printf '%s\n' 'git log origin/codex/audit-fixes..HEAD:'
if [[ -n "$pending" ]]; then
  printf '%s\n' "$pending"
  printf '%s\n' 'STOP: these commits are not recorded as pushed. Use VS Code Push, then rerun.' >&2
  exit 1
fi
printf '%s\n' 'PASS: no unpushed commits relative to the local origin tracking ref.'
printf '%s\n' 'This is not a network verification or a CI result; confirm the commit in GitHub.'
