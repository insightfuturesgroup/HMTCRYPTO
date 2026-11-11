# Minimal EC2 deployment

This profile deploys HMT-Crypto on one EC2 instance with one Elastic IP, GoDaddy DNS,
Caddy-managed HTTPS, and a separate Supabase PostgreSQL project. It intentionally has no
CloudFront, NAT Gateway, load balancer, Route 53, or Vercel dependency.

The checked-in worker contains a fail-closed Live path. Completing this deployment does not
authorize production trading: global and per-account entry gates must remain disabled through
deployment and qualification.

## AWS baseline

Use the dedicated AWS account and EC2 instance in `ap-southeast-2`:

- Ubuntu 24.04 LTS or Amazon Linux 2023;
- a 2 GiB ARM64 instance initially (the current host is ARM64); monitor memory and resize when
  sustained headroom is inadequate;
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

Create root-owned protected configuration from the templates. The production host uses `/etc/hmt`
and symlinks so a Git checkout cannot overwrite secrets:

```bash
cd /opt/hmt-crypto
sudo install -d -o root -g root -m 700 /etc/hmt
sudo install -o root -g root -m 600 deploy/ec2/compose.env.example /etc/hmt/compose.env
sudo install -o root -g root -m 600 deploy/ec2/api.env.example /etc/hmt/api.env
sudo install -o root -g root -m 600 deploy/ec2/worker.env.example /etc/hmt/worker.env
sudo install -o root -g root -m 600 deploy/ec2/migration.env.example /etc/hmt/migration.env
sudo ln -sfn /etc/hmt/compose.env .env
sudo ln -sfn /etc/hmt/api.env deploy/ec2/api.env
sudo ln -sfn /etc/hmt/worker.env deploy/ec2/worker.env
sudo ln -sfn /etc/hmt/migration.env deploy/ec2/migration.env
```

Populate the exact Supabase URLs supplied by its dashboard. Use the session pooler on port 5432 for
the `hmt_crypto_app.<project-ref>` runtime role and the direct `postgres` owner URL for migrations.
Do not use the transaction pooler for worker locks or migrations. Install the verified Supabase CA
at `/etc/hmt/supabase-prod-ca.crt` and expose it read-only inside containers at
`/run/secrets/supabase-prod-ca.crt`. Keep API and migration roles distinct.

Generate the operator password hash interactively; never store the plaintext password:

```bash
docker run --rm -it python:3.13-slim sh -lc \
  "pip install --quiet argon2-cffi && python -c 'from argon2 import PasswordHasher; print(PasswordHasher().hash(input()))'"
```

## First start

Run the secret-safe locked-deployment preflight, then validate and build. The preflight prints no
configuration values and refuses an enabled dispatcher, a preloaded qualification digest, an
unreviewed checkout, unsafe file modes, or the wrong Supabase endpoint class:

```bash
sudo python3 tools/ec2_release_preflight.py --expected-commit <reviewed-full-git-sha>
docker compose -f docker-compose.ec2.yml config --quiet
docker compose -f docker-compose.ec2.yml build
docker compose -f docker-compose.ec2.yml run --rm migrate
```

Expected preflight output includes `"status": "PASS"`, `"dispatchEnabled": false`, and
`"productionWritesForbidden": true`. Do not continue on any failure.

The migration service uses the EC2 host network so Supabase direct endpoints that resolve only
to IPv6 can use the host's verified IPv6 route. API and worker services remain isolated on the
application bridge and use the persistent session pooler.

Bootstrap the first locked production account record through a one-off container, put the returned
account UUID into both protected API/worker files, and only then enable database-backed API
control. Bootstrap does not enable entries:

```bash
docker compose -f docker-compose.ec2.yml run --rm api \
  hmt-crypto account bootstrap --profile delta-india-primary
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
sudo python3 tools/ec2_release_preflight.py --expected-commit <reviewed-full-git-sha>
docker compose -f docker-compose.ec2.yml build
docker compose -f docker-compose.ec2.yml run --rm migrate
docker compose -f docker-compose.ec2.yml up -d --no-build
```

Application rollback means restoring the previous reviewed commit/images after first disabling
global entries. Migrations are forward-fix only unless a specific migration proves otherwise;
never run Alembic downgrade on production. If schema rollback is necessary, restore a verified
Supabase backup into an isolated project, validate it, and switch only under an approved incident
procedure.

## Emergency entry disable

Use the authenticated Admin Health page to disable global entries. If the UI or API is unavailable,
stop the worker while preserving the database and evidence, then investigate before restart:

```bash
cd /opt/hmt-crypto
sudo docker compose -f docker-compose.ec2.yml stop worker
sudo docker compose -f docker-compose.ec2.yml ps
```

Stopping the worker is a last-resort dispatch stop; it does not cancel exchange orders or flatten
positions. An operator must inspect Delta directly, preserve or cancel protective orders according
to the approved incident procedure, and reconcile before restoring service.

## Required monitoring before qualification

Create alarms for EC2 status checks, CPU, memory, disk, container restarts, API readiness,
certificate expiry, worker leadership, reconciliation age, public/private stream freshness,
uncertain operations, risk halt, and alert-delivery failure. Keep both database-backed global and
per-account entry gates disabled through deployment and read-only qualification. Environment flags
alone are not an activation control.
