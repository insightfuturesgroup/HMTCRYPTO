# First locked UI check

This procedure makes the UI reachable while every trading and dispatch gate stays
closed. Stop on the first unexpected line. It does not authorize Delta credentials,
TradingView configuration, qualification evidence, or gate opening.

## 1. Ops-1: runtime role

RUN ON: Mac VS Code terminal.

FOLDER: `cd ~/HMT_Hitashi/HMTCRYPTO-fixes`

PROMPTS: pooler host, project reference, CA path, owner password and new runtime
password, all from the reviewed dashboard/password manager and hidden prompts.

COMMAND: `bash ops/mac/01_create_runtime_role.sh`

EXPECTED OUTPUT: restricted runtime role and CONNECT verified.

FAILURE FORMAT: fixed STOP/PASS lines only; no credentials or server diagnostics.

## 2. Ops-2: owner migration

RUN ON: Mac VS Code terminal.

FOLDER: `cd ~/HMT_Hitashi/HMTCRYPTO-fixes`

PROMPTS: see `docs/OPS_2_MIGRATE_MUMBAI.md`; use the same CI-green deploy SHA.

COMMAND: `bash ops/mac/02_migrate_mumbai.sh`

EXPECTED OUTPUT: phase34 head, 189 restrictive FKs, phase33 ACL PASS, retention
EXECUTE PASS, and GET-only confirmation that `hmt_crypto` is not Data API exposed.

FAILURE FORMAT: fixed category/STOP/PASS lines only.

## 3. Ops-4b: pinned one-time ARM64 build

RUN ON: EC2 interactive Session Manager shell.

FOLDER: `/tmp`, then `/opt/hmt-crypto` as directed by `docs/OPS_4B_BUILD_IMAGES.md`.

PROMPTS: CI-green deploy SHA and Mac-computed script SHA256.

COMMAND: execute the verified `ops/aws/20_build_images.sh` bootstrap.

EXPECTED OUTPUT: disk PASS, pinned checkout, temporary swap, four serial image
builds, image names/sizes, cache prune and swap removal.

FAILURE FORMAT: last fixed line, service, safe resource totals only.

## 4. Ops-5: protected configuration

RUN ON: EC2 interactive Session Manager shell.

FOLDER: `cd /opt/hmt-crypto`

PROMPTS: see `docs/OPS_5_INSTALL_CONFIG.md`; all sensitive values are hidden.

COMMAND: `bash ops/aws/30_install_config.sh`

EXPECTED OUTPUT: CA and environment files verified root-only under `/etc/hmt`, gates closed.

FAILURE FORMAT: fixed STOP/PASS lines only.

## 5. Ops-6: ordered start

RUN ON: EC2 interactive Session Manager shell.

FOLDER: `cd /opt/hmt-crypto`

PROMPTS: reviewed SHA and hidden Elastic IP.

COMMAND: `bash ops/aws/40_start_ui.sh`

EXPECTED OUTPUT: preflight PASS, ordered containers running, API status 200.

FAILURE FORMAT: STOP/PASS, service name and status code only.

## 6. Ops-7: sole admin and locked account

RUN ON: EC2 interactive Session Manager shell.

FOLDER: `cd /opt/hmt-crypto`

PROMPTS: hidden Auth UUID, confirmed email and admin name.

COMMAND: `bash ops/aws/50_bootstrap_admin.sh`

EXPECTED OUTPUT: sole admin bound, locked account created, API binding recreated.

FAILURE FORMAT: fixed STOP/PASS lines only.

## Basic UI check

Open `https://crypto.helpmetrade.club`. Expect a valid HTTPS login page, then the
locked administrative UI for the single user. The UI must show production writes,
trading entry and live dispatch disabled. An unauthenticated webhook request must
be rejected; it must never enqueue or acknowledge a signal. No anonymous signup,
database endpoint, API port, worker port, Docker port or gate-open state may be
reachable. On EC2, only api, worker, private-worker, ui, caddy and redis should run;
disk must remain at or below 70 percent.

Stop command: `bash ops/aws/40_start_ui.sh --stop`.
