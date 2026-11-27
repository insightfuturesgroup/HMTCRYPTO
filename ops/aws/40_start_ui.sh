#!/usr/bin/env bash
set -euo pipefail
cd /opt/hmt-crypto
compose=(sudo docker compose -f docker-compose.ec2.yml)

if [[ "${1:-}" == '--stop' ]]; then
  "${compose[@]}" stop caddy ui private-worker worker api redis
  echo 'PASS: application services stopped; volumes and images retained'
  exit 0
fi

[[ -t 0 ]] || { echo 'STOP: interactive Session Manager required'; exit 1; }
read -r -p 'Reviewed full commit SHA: ' reviewed_sha
read -r -s -p 'Elastic IP for DNS comparison (hidden): ' elastic_ip
printf '\n'
[[ "$reviewed_sha" =~ ^[0-9a-f]{40}$ ]] || { echo 'STOP: invalid reviewed SHA'; exit 1; }
[[ "$elastic_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo 'STOP: invalid IP'; exit 1; }

sudo python3 tools/ec2_release_preflight.py --config /run/hmt --expected-commit "$reviewed_sha" \
  >/tmp/hmt-preflight-safe.json || { cat /tmp/hmt-preflight-safe.json; exit 1; }
grep -q '"status": "PASS"' /tmp/hmt-preflight-safe.json || { echo 'STOP: preflight failed'; exit 1; }
rm -f /tmp/hmt-preflight-safe.json

resolved=$(dig +short A crypto.helpmetrade.club | sort -u)
[[ "$resolved" == "$elastic_ip" ]] || { echo 'STOP: DNS does not match Elastic IP'; exit 1; }
unset elastic_ip resolved
disk=$(df -P / | awk 'NR==2 {gsub(/%/,"",$5); print $5}')
(( disk <= 70 )) || { echo 'STOP: disk use above 70 percent'; exit 1; }
memory=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
(( memory >= 3500000 )) || { echo 'STOP: less than 3.5 GiB memory'; exit 1; }
if ss -ltnH | awk '{print $4}' | grep -Eq '(:80|:443)$'; then
  echo 'STOP: host TCP 80 or 443 is already occupied'
  exit 1
fi
for file in /run/hmt/{compose,api,worker,migration}.env; do
  sudo test -f "$file" || { echo 'STOP: tmpfs environment missing; rerun ops-5'; exit 1; }
done
for image in hmt-crypto-ec2-api hmt-crypto-ec2-worker hmt-crypto-ec2-private-worker hmt-crypto-ec2-ui; do
  sudo docker image inspect "$image" >/dev/null 2>&1 || { echo "STOP: image missing: $image"; exit 1; }
done
echo 'PASS: preflight, closed gates, DNS, disk, memory, env and images verified'

"${compose[@]}" up -d --no-deps --no-build redis
for _ in {1..20}; do
  [[ "$("${compose[@]}" ps --format json redis | grep -c healthy || true)" -gt 0 ]] && break
  sleep 2
done
"${compose[@]}" ps --format json redis | grep -q healthy || { echo 'STOP: redis not healthy'; exit 1; }
for service in api worker private-worker; do
  "${compose[@]}" up -d --no-deps --no-build "$service"
done
for _ in {1..30}; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8000/api/v1/health/live || true)
  [[ "$code" == '200' ]] && break
  sleep 2
done
[[ "$code" == '200' ]] || { echo 'STOP: API health did not reach 200'; exit 1; }
"${compose[@]}" up -d --no-deps --no-build ui
"${compose[@]}" up -d --no-deps --no-build caddy
echo 'PASS: redis; api/worker/private-worker; ui/caddy started in order'
echo 'PASS: API liveness returned 200'
echo 'STOP COMMAND: bash ops/aws/40_start_ui.sh --stop'
