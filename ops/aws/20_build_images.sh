#!/usr/bin/env bash
set -euo pipefail

REPO_URL='https://github.com/insightfuturesgroup/HMTCRYPTO.git'
REPO_DIR='/opt/hmt-crypto'
SWAP_FILE='/swapfile-hmt-build'
COMPOSE=(sudo env HMT_TLS_EMAIL=build-only@example.invalid docker compose -f docker-compose.ec2.yml)

fail() { printf 'STOP: %s\n' "$1" >&2; exit 1; }
cleanup_swap() {
  if sudo swapon --show=NAME --noheadings | grep -Fxq "$SWAP_FILE"; then
    sudo swapoff "$SWAP_FILE" || true
  fi
  if sudo test -f "$SWAP_FILE"; then sudo rm -f "$SWAP_FILE"; fi
}
trap cleanup_swap EXIT INT TERM

[[ -t 0 ]] || fail 'run interactively in Session Manager'
read -r -p 'Reviewed full commit SHA: ' reviewed_sha
read -r -p 'Expected SHA256 of this script from Mac: ' expected_script_sha
[[ "$reviewed_sha" =~ ^[0-9a-f]{40}$ ]] || fail 'invalid reviewed SHA'
[[ "$expected_script_sha" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid script SHA256'
actual_script_sha=$(sha256sum "${BASH_SOURCE[0]}" | awk '{print $1}')
[[ "$actual_script_sha" == "$expected_script_sha" ]] || fail 'script SHA256 mismatch'

root_use=$(df -P / | awk 'NR==2 {gsub(/%/,"",$5); print $5}')
[[ "$root_use" =~ ^[0-9]+$ ]] || fail 'cannot determine root disk use'
(( root_use <= 70 )) || fail 'root disk use is above 70 percent'

printf 'PASS: disk at or below 70 percent.\n'

if [[ ! -d "$REPO_DIR/.git" ]]; then
  sudo git -c safe.directory="$REPO_DIR" clone --no-checkout "$REPO_URL" "$REPO_DIR" >/dev/null
fi
sudo git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" remote set-url origin "$REPO_URL"
sudo git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" fetch --depth=1 origin "$reviewed_sha" \
  >/dev/null
sudo git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" checkout --detach --force \
  "$reviewed_sha" >/dev/null
[[ -z "$(sudo git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" status --porcelain)" ]] \
  || fail 'checkout is not clean'
[[ "$(sudo git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" rev-parse HEAD)" == "$reviewed_sha" ]] \
  || fail 'checkout does not match reviewed SHA'
printf 'PASS: clean HTTPS checkout pinned to reviewed SHA.\n'

cleanup_swap
sudo fallocate -l 4G "$SWAP_FILE"
sudo chmod 600 "$SWAP_FILE"
sudo mkswap "$SWAP_FILE" >/dev/null
sudo swapon "$SWAP_FILE"
printf 'PASS: temporary 4 GiB swap enabled.\n'

cd "$REPO_DIR"
export COMPOSE_PARALLEL_LIMIT=1
export HMT_TLS_EMAIL=build-only@example.invalid
for service in api worker private-worker ui; do
  printf 'BUILD: %s\n' "$service"
  "${COMPOSE[@]}" build "$service" || fail "image build failed for $service"
done
unset HMT_TLS_EMAIL

for image in hmt-crypto-ec2-api hmt-crypto-ec2-worker hmt-crypto-ec2-private-worker hmt-crypto-ec2-ui; do
  sudo docker image inspect "$image" --format '{{.RepoTags}} {{.Size}}' \
    || fail "expected image missing: $image"
done
sudo docker builder prune --force >/dev/null
cleanup_swap
trap - EXIT INT TERM
printf 'PASS: images built serially; build cache pruned; temporary swap removed.\n'
