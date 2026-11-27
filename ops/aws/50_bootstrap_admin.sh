#!/usr/bin/env bash
set -euo pipefail
cd /opt/hmt-crypto
exec sudo python3 ops/aws/bootstrap_admin.py
