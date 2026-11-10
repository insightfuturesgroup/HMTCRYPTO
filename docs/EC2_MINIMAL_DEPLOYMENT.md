# Minimal EC2 deployment

This profile deploys HMT-Crypto on one EC2 instance with one Elastic IP, GoDaddy DNS,
Caddy-managed HTTPS, and a separate Supabase PostgreSQL project. It intentionally has no
CloudFront, NAT Gateway, load balancer, Route 53, or Vercel dependency.

The checked-in worker remains PAPER/testnet-only. Completing this deployment does not authorize
Delta private reads, testnet mutations, or production trading.

## AWS baseline

Create a separate AWS account and an EC2 instance in `ap-south-1`:

- Ubuntu 24.04 LTS or Amazon Linux 2023;
- `t3.small` initially;
- 30 GiB encrypted gp3 EBS;
- one Elastic IP associated with the instance;
- IMDSv2 required;
- an IAM instance profile with Systems Manager core permissions;
- automatic EBS snapshots and EC2 status-check recovery;
- no SSH ingress.

Security-group ingress must contain only TCP 80, TCP 443, and UDP 443. The application, API,
worker, Docker daemon, and database must not have public listener rules. Administration uses
Systems Manager Session Manager.

The Elastic IP is the only address to whitelist with Delta. Before any private testnet probe,
verify the instance's observed egress address matches the recorded Elastic IP.

## GoDaddy DNS

Create this record after the Elastic IP is allocated:

| Type | Name | Value | TTL |
| --- | --- | --- | --- |
| A | `crypto` | the EC2 Elastic IP | 600 seconds |

Remove conflicting `crypto` A/AAAA/CNAME records. Wait until
`crypto.helpmetrade.club` resolves to the Elastic IP before starting Caddy; certificate issuance
requires public TCP 80/443 reachability.

## Host preparation

Install Docker Engine and the Compose plugin from Docker's official repository. Clone only this
private repository into `/opt/hmt-crypto`. Do not copy the original F&O checkout or its secrets.

Create protected configuration from the templates:

```bash
cd /opt/hmt-crypto
cp deploy/ec2/compose.env.example .env
cp deploy/ec2/api.env.example deploy/ec2/api.env
cp deploy/ec2/worker.env.example deploy/ec2/worker.env
cp deploy/ec2/migration.env.example deploy/ec2/migration.env
chmod 600 .env deploy/ec2/api.env deploy/ec2/worker.env deploy/ec2/migration.env
```

Populate the exact Supabase URLs supplied by its dashboard. Use a persistent session-pooler or
direct runtime URL for `DATABASE_URL`, and the direct owner URL for `DATABASE_MIGRATION_URL`. Do not
use the transaction pooler for worker locks. Keep the API and migration roles distinct.

Generate the operator password hash interactively; never store the plaintext password:

```bash
docker run --rm -it python:3.13-slim sh -lc \
  "pip install --quiet argon2-cffi && python -c 'from argon2 import PasswordHasher; print(PasswordHasher().hash(input()))'"
```

## First start

Validate rendered Compose configuration without printing the populated environment files:

```bash
docker compose -f docker-compose.ec2.yml config --quiet
docker compose -f docker-compose.ec2.yml build
docker compose -f docker-compose.ec2.yml run --rm migrate
```

Bootstrap the PAPER account through a one-off container, put the returned account UUID into both
protected API/worker files, and only then enable database-backed API control:

```bash
docker compose -f docker-compose.ec2.yml run --rm api \
  hmt-crypto account bootstrap --profile paper-primary
```

Start the services:

```bash
docker compose -f docker-compose.ec2.yml up -d --no-build
docker compose -f docker-compose.ec2.yml ps
```

Verify `https://crypto.helpmetrade.club`, the liveness endpoint, secure cookies, same-origin API
routing, and that no container other than Caddy publishes host ports.

## Updates and rollback

Pin deployments to a reviewed Git commit. Before updating, record the running commit, image IDs,
database migration revision, and Supabase backup/PITR status. Then build, run migrations once, and
replace services:

```bash
git fetch --prune
git checkout <reviewed-commit>
docker compose -f docker-compose.ec2.yml build
docker compose -f docker-compose.ec2.yml run --rm migrate
docker compose -f docker-compose.ec2.yml up -d --no-build
```

Application rollback means restoring the previous reviewed commit/images. Database downgrade is
allowed only when the migration runbook explicitly proves it safe; otherwise restore a verified
Supabase backup into an isolated project and investigate.

## Required monitoring before qualification

Create alarms for EC2 status checks, CPU, memory, disk, container restarts, API readiness,
certificate expiry, worker leadership, reconciliation age, public/private stream freshness,
uncertain operations, risk halt, and alert-delivery failure. Keep `PRODUCTION_WRITES_FORBIDDEN=true`
through deployment and read-only qualification.
