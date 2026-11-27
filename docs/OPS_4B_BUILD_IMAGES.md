# Ops-4b: pinned native ARM64 image build

This is the approved one-time first-UI build path. It changes only the EC2 host:
it creates and later removes a 4 GiB swapfile, creates or updates `/opt/hmt-crypto`,
creates local Docker images, and prunes build cache. It does not create AWS resources,
read application secrets, start services, or open gates.
Git commands use a command-scoped safe-directory setting for the pinned checkout,
and Compose receives only the non-secret build placeholder through `sudo`.

## Step 1: record the reviewed script digest

RUN ON: Mac VS Code terminal.

FOLDER: `cd ~/HMT_Hitashi/HMTCRYPTO-fixes`

PROMPTS: none.

COMMAND: `shasum -a 256 ops/aws/20_build_images.sh`

EXPECTED OUTPUT: one 64-character digest followed by the script path. Record the
digest locally; it is not a secret. Also record the full SHA from the CI-green run.

FAILURE FORMAT: share only `shasum unavailable` or the script path; no environment dump.

## Step 2: transfer and verify the bootstrap script

RUN ON: EC2 interactive Session Manager shell, never `send-command`.

FOLDER: `cd /tmp`

PROMPTS: reviewed full commit SHA and the digest recorded on the Mac. Neither is secret.

COMMAND:

```bash
read -r -p 'Reviewed full commit SHA: ' HMT_SHA
read -r -p 'Mac script SHA256: ' HMT_SCRIPT_SHA
curl -fsS --proto '=https' --tlsv1.2 \
  "https://raw.githubusercontent.com/insightfuturesgroup/HMTCRYPTO/${HMT_SHA}/ops/aws/20_build_images.sh" \
  -o /tmp/hmt-ops-4b.sh
printf '%s  %s\n' "$HMT_SCRIPT_SHA" /tmp/hmt-ops-4b.sh | sha256sum -c -
bash /tmp/hmt-ops-4b.sh
unset HMT_SHA HMT_SCRIPT_SHA
```

Do not continue on a mismatch. The script repeats its own SHA check before any host change.

EXPECTED OUTPUT: PASS for disk, pinned checkout and swap; four sequential
BUILD lines; image names and byte sizes only; final PASS confirming swap removal.

FAILURE FORMAT: share only the last fixed `STOP`, `BUILD`, or `PASS` line and the
service name. Do not share instance IDs, AWS output, image history or environment.

## OOM or stall procedure

In a second Session Manager shell, use `free -h`, `swapon --show`, `df -h /`,
`ps -eo pid,stat,etime,%cpu,%mem,comm --sort=-%mem | head`, and
`sudo dmesg --ctime | tail -n 40`. Report only: service name, elapsed time, free
memory/swap, disk percentage, whether `oom-kill` appears, and the last non-secret
Docker build stage. Never send complete logs before checking them for secrets.

If CPU and output do not change for 15 minutes, interrupt once with Ctrl-C. Do not
loop. Rerun ops-4b only after investigating; Docker cache resumes completed layers.
The script removes its temporary swap on exit and builds services serially. If the
Session Manager shell dies, reconnect, confirm no build remains with `ps`, remove
the exact `/swapfile-hmt-build` only after `swapoff`, and then rerun once.

Option C—CI-built ARM64 images in private ECR, deployed by digest through GitHub
OIDC and the instance role—is a post-UI task and is not implemented here.
