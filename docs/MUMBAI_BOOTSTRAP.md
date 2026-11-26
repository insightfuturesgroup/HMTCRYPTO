# Mumbai project bootstrap and direct migrations

Operator runbook only; these commands have not been executed against Supabase. Target:
`<project-ref>`, Mumbai (`ap-south-1`), PostgreSQL 17. Keep entry gates closed.
Use the reviewed checkout and Python 3.13.10. Package installation may use registries;
it is preparation, separate from offline test execution. Never paste passwords into SQL,
command arguments, shell history, tickets, or this repository.

Replace template placeholders through the operator workflow. The helper reads
the project reference through a hidden prompt, or from an already-populated
private `HMT_SUPABASE_PROJECT_REF` environment variable. It has no project default.
Obtain the reference from the dashboard Connect hostname; do not set it inline in
a shell command. See [the identifier guard](PUBLIC_IDENTIFIER_GUARD.md) for checks.

## Connection admission

Runtime uses the dashboard's **session pooler**, port 5432, qualified username
`hmt_crypto_app.<project-ref>`. Alembic currently requires the direct owner
endpoint, `db.<project-ref>.supabase.co:5432`, username `postgres`.
Do not guess the pooler hostname from a region: copy it from Connect in this project.

The migration guard rejects every `.pooler.supabase.com` endpoint, port 6543, and
all URL query parameters. Transaction pooling does not preserve backend session
state. The blanket session-pooler rejection is a conservative admission rule;
session pooling is not inherently incompatible with DDL. Supporting it would need
explicit endpoint classification, same-project/distinct-role checks, verified TLS,
and qualification of transactions, session settings, advisory locks, DDL and
connection loss. This patch does not change admission. Query parameters are
rejected to prevent driver identity overrides and inconsistent SSL handling;
CA configuration uses `DATABASE_MIGRATION_CA_FILE` instead.

The Free direct endpoint can require IPv6. An IPv4-only EC2 host cannot reach an
IPv6-only endpoint merely by selecting Docker host networking. Run owner migrations
from a Mac with a working IPv6 route, or enable the project's direct IPv4 add-on
and verify the dashboard endpoint has an IPv4 address. Preserve its DNS hostname
for certificate verification; do not replace it with an IP or bypass TLS. Pro alone
must not be assumed to supply direct IPv4. Runtime session pooling supports IPv4.
Confirm actual endpoint availability in the new project's dashboard.

## 1. Create the runtime role before Alembic

Download the project's database CA certificate from the dashboard to a local path.
The CA is public certificate material, not the database password. Use `psql` with
an interactive password prompt (requires a working direct route):

```bash
PSQL_HISTORY=/dev/null psql -X -W "host=db.<project-ref>.supabase.co port=5432 dbname=postgres user=postgres sslmode=verify-full sslrootcert=/absolute/path/supabase-ca.crt"
```

In that **psql session**, on a new project:

```sql
CREATE ROLE hmt_crypto_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS;
GRANT CONNECT ON DATABASE postgres TO hmt_crypto_app;
\password hmt_crypto_app
\q
```

`\password` is a psql command, not SQL-editor SQL. It prompts without embedding
the password in a saved SQL file. Store the runtime password in the deployment
secret store/protected EC2 environment through your secure provisioning process.
Do not grant this role owner membership or blanket schema privileges. Alembic's
runtime-grants revision requires this role to exist and applies the reviewed
table/function-specific privileges; subsequent migrations maintain them.

## 2. Laptop migration script (passwords only in process memory)

Stop API and both workers on the target before running migrations. FK replacement
locks last for the entire migration transaction. Verify the reviewed full SHA and
the target independently; the prompt below is an operator acknowledgement, not a
remote check. Start from the reviewed repository directory with its installed
editable project/dev dependencies. Save the following password-free script as
`/tmp/hmt-migrate.py`, then run `python /tmp/hmt-migrate.py` from that directory.
Do not enable shell tracing or environment/process diagnostic logging.
The identical executable helper is `tools/migrate_mumbai.py`; run it from the
repository root with `python tools/migrate_mumbai.py` if preferred.

Alembic `env.py` reads `DATABASE_MIGRATION_URL` and `DATABASE_URL`; its optional
TLS CA setting is `DATABASE_MIGRATION_CA_FILE`. It does not load app settings.
The helper additionally sets and validates these runtime-loader settings using
`ControlRuntimeSettings(_env_file=None)` before Alembic, without opening an engine:

| Setting | Helper value / purpose |
| --- | --- |
| `DATABASE_URL` | Distinct runtime session-pooler URL, built in memory |
| `DATABASE_MIGRATION_URL` | Direct owner URL, built in memory |
| `DATABASE_MIGRATION_CA_FILE` | Absolute project CA path for owner TLS |
| `DATABASE_CA_FILE` | Same CA path for runtime configuration |
| `DATABASE_SINGLE_PROCESS` | `false`; PostgreSQL multi-process deployment |
| `EXECUTION_MODE` | `LIVE`; records target scope, does not submit orders |
| `DELTA_REGION` | `india` |
| `DELTA_ENV` | `prod` |
| `PRODUCTION_WRITES_FORBIDDEN` | `true` |
| `HMT_LIVE_ORDER_DISPATCH_ENABLED` | `false` |
| `HMT_PRIVATE_READ_ONLY` | `true` |

The final three settings explicitly preserve closed write/dispatch behavior. No
Auth/Delta keys or account IDs are required for migrations. Runtime-loader fields
otherwise have defaults; explicitly setting them prevents accidental SQLite or
PAPER defaults. `HMT_CRYPTO_ALLOW_EMPTY_DOWNGRADE` is only a destructive baseline
downgrade acknowledgement, not an upgrade requirement; this helper does not set it.
No process settings are written to disk, and previous values are restored on exit.

```python
"""Interactive owner migration helper; never prints exception messages or URLs."""

import getpass
import os
import re
import ssl
import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL

from hmt_crypto.api.settings import ControlRuntimeSettings
from hmt_crypto.persistence.migration_configuration import MigrationConfigurationError


def build_environment(
    ca: Path, pooler: str, owner_password: str, runtime_password: str, *, project_ref: str
) -> dict[str, str]:
    if not re.fullmatch(r"[a-z]{20}", project_ref):
        raise ValueError("Invalid project reference")
    project = project_ref
    return {
        "DATABASE_MIGRATION_URL": URL.create(
            "postgresql+asyncpg",
            username="postgres",
            password=owner_password,
            host=f"db.{project}.supabase.co",
            port=5432,
            database="postgres",
        ).render_as_string(hide_password=False),
        "DATABASE_URL": URL.create(
            "postgresql+asyncpg",
            username=f"hmt_crypto_app.{project}",
            password=runtime_password,
            host=pooler,
            port=5432,
            database="postgres",
        ).render_as_string(hide_password=False),
        "DATABASE_MIGRATION_CA_FILE": str(ca),
        "DATABASE_CA_FILE": str(ca),
        "DATABASE_SINGLE_PROCESS": "false",
        "EXECUTION_MODE": "LIVE",
        "DELTA_REGION": "india",
        "DELTA_ENV": "prod",
        "PRODUCTION_WRITES_FORBIDDEN": "true",
        "HMT_LIVE_ORDER_DISPATCH_ENABLED": "false",
        "HMT_PRIVATE_READ_ONLY": "true",
    }


def read_project_reference() -> str:
    project = os.environ.get("HMT_SUPABASE_PROJECT_REF")
    if project is None:
        project = getpass.getpass("Supabase project reference (hidden): ").strip()
    if not re.fullmatch(r"[a-z]{20}", project):
        raise ValueError("Invalid project reference")
    return project


def failure_diagnostic(error: Exception) -> str:
    # Class names are bounded; exception messages are never included in output.
    name = type(error).__name__
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
        name = "Exception"
    if isinstance(error, MigrationConfigurationError):
        hint = (
            "guard admission: verify direct owner, distinct runtime role, "
            "target and CA configuration"
        )
    elif isinstance(error, ssl.SSLError):
        hint = "TLS: verify project CA, certificate hostname and clock; do not bypass verification"
    elif isinstance(error, OSError):
        hint = "connection: verify DNS, IPv6/direct IPv4 route and port 5432"
    elif re.fullmatch(
        r"Database migration failed without exposing server diagnostics \(SQLSTATE 28[A-Z0-9]{3}\)",
        str(error),
    ):
        hint = "authentication: verify owner credentials and login permission"
    else:
        # env.py intentionally suppresses driver details; do not undo that boundary.
        hint = (
            "details redacted: check connection/TLS/auth, role grants and migration prerequisites"
        )
    return f"Migration failed [{name}]: {hint}."


def run() -> None:
    if sys.version_info[:3] != (3, 13, 10) or not sys.stdin.isatty():
        raise RuntimeError("Use Python 3.13.10 in an interactive terminal")
    root = Path.cwd()
    expected = input("Reviewed full commit SHA: ").strip()
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], text=True)
    if len(expected) != 40 or expected != actual or dirty.strip():
        raise RuntimeError("Require clean reviewed checkout")
    if input("Type APP STOPPED to acknowledge API/workers stopped: ") != "APP STOPPED":
        raise RuntimeError("Stop application before migration")
    project_ref = read_project_reference()
    ca = Path(input("Absolute project CA certificate path: ").strip())
    if not ca.is_absolute() or not ca.is_file():
        raise RuntimeError("CA file missing")
    pooler = input("Exact session-pooler hostname from project dashboard: ").strip()
    if not re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", pooler):
        raise RuntimeError("Invalid session-pooler hostname")
    owner_password = getpass.getpass("Database owner password: ")
    runtime_password = getpass.getpass("hmt_crypto_app password: ")
    if not owner_password or not runtime_password:
        raise RuntimeError("Passwords required")
    values = build_environment(
        ca, pooler, owner_password, runtime_password, project_ref=project_ref
    )
    previous = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        ControlRuntimeSettings(_env_file=None).database_runtime_config()
        config = Config(str(root / "alembic.ini"))
        command.upgrade(config, "head")
        command.current(config)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        print(failure_diagnostic(error), file=sys.stderr)
        sys.exit(1)
```

The script passes passwords to Alembic in the same process, not command arguments
or a child process. Passwords still exist in process memory; run only on a trusted
laptop. No password is written by this script. The existing migration guard still
validates connection admission and the CA. This recipe needs a real PG17 rehearsal
before use; it is not evidence that migrations were run.
`URL.create(...).render_as_string(hide_password=False)` percent-encodes reserved
password characters; enter the original password, not a pre-encoded version.
Passwords do not need to be alphanumeric. This document contains real public
project/domain identifiers, but no real passwords, API keys, tokens or CA contents.
On failure, the helper prints only a bounded exception class name and a fixed
hint. Driver messages, URL/password values and tracebacks are not printed. When
Alembic has already redacted driver diagnostics, the hint remains generic; it
cannot reliably distinguish TLS from a network failure after that redaction.

## 3. Auth, platform admin, and locked account

In the new project's Supabase Auth dashboard, create the initial user with a
secure password and confirmed email; record its Auth UUID. Creating an Auth user
does not create the platform administrator. Configure the protected API environment
with this project's publishable/secret Auth keys and URL, runtime session-pooler URL,
runtime CA, and the unchanged production-write/entry defaults. Then, on EC2:

```bash
cd /opt/hmt-crypto
sudo docker compose -f docker-compose.ec2.yml run --rm --no-deps api hmt-crypto platform bootstrap-admin --auth-user-id <auth-user-uuid> --email <confirmed-email> --name '<full name>'
sudo docker compose -f docker-compose.ec2.yml run --rm --no-deps api hmt-crypto account bootstrap --profile delta-india-primary --confirm-locked-live
```

The first command uses the runtime database role. It only bootstraps the first
platform user (or the matching existing admin); do not use ad-hoc user-table SQL.
Put the **account UUID returned by the second command** into `HMT_API_ACCOUNT_ID`
in `/etc/hmt/api.env`. It is not the Auth UUID or platform-user UUID. Configure
account assignments/credentials through the reviewed platform flows. Account
bootstrap does not open entry gates. Preflight the final locked configuration
against the reviewed SHA before starting services. Do not copy old-project users,
keys, UUIDs or credential ARNs into this project by assumption.

Deployment values to replace: `HMT_AWS_REGION=ap-south-1` (API and worker),
`HMT_SUPABASE_URL=https://<project-ref>.supabase.co`, both Auth keys,
each runtime `DATABASE_URL`, owner `DATABASE_MIGRATION_URL`, both database CA
file settings/certificate mounts, `HMT_API_ACCOUNT_ID`, and region/account-specific
Secrets Manager ARNs and IAM permissions. Verify `HMT_API_ALLOWED_ORIGIN` matches
the actual public HTTPS origin; retain `https://crypto.helpmetrade.club` if DNS is
being moved without a domain change. Recreate regional networking/IP whitelist
and verify DNS/TLS separately; this commit does not provision them.

## 4. Read-only verification in the owner psql session

```sql
SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication,
       rolbypassrls FROM pg_roles WHERE rolname IN ('postgres', 'hmt_crypto_app');
SELECT parent.rolname AS granted_role, member.rolname AS member
FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid
JOIN pg_roles member ON member.oid=m.member
WHERE member.rolname='hmt_crypto_app';
SELECT has_database_privilege('hmt_crypto_app','postgres','CONNECT') AS can_connect,
       has_schema_privilege('hmt_crypto_app','hmt_crypto','USAGE') AS schema_usage,
       has_schema_privilege('hmt_crypto_app','hmt_crypto','CREATE') AS must_be_false;
SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants
WHERE table_schema='hmt_crypto' AND grantee='hmt_crypto_app'
ORDER BY table_name, privilege_type;
SELECT r.rolname, has_schema_privilege(r.oid,n.oid,'USAGE') AS must_be_false,
       has_schema_privilege(r.oid,n.oid,'CREATE') AS must_also_be_false
FROM pg_roles r CROSS JOIN pg_namespace n
WHERE r.rolname IN ('anon','authenticated') AND n.nspname='hmt_crypto';
SELECT a.grantee, a.privilege_type FROM pg_namespace n,
LATERAL aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a
WHERE n.nspname='hmt_crypto' AND a.grantee=0;
SELECT c.relname, a.grantee, a.privilege_type
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
WHERE n.nspname='hmt_crypto' AND c.relkind IN ('r','p','v','m')
AND (a.grantee=0 OR a.grantee IN
     (SELECT oid FROM pg_roles WHERE rolname IN ('anon','authenticated')));
SELECT version_num FROM public.hmt_crypto_alembic_version;
```

The PUBLIC query must return no rows. Inspect table grants against the migration
definitions (some narrowly scoped DELETE grants are deliberate). Confirm the
private schema is absent from the Data API exposed-schema list in the dashboard;
SQL ACL queries cannot prove that dashboard setting. `command.current` above
must match `alembic heads` from the reviewed checkout. Verify actual PG major via
`SHOW server_version;`. No verification query here reads password hashes or secrets.

## Uncertain-order operations

Missing-evidence UNCERTAIN blocks entries and appears as CONFIRMING with
"do not retry" in Orders. See [UNCERTAIN_ORDER_RESOLUTION.md](UNCERTAIN_ORDER_RESOLUTION.md)
for the age thresholds and evidence-preserving manual-resolution procedure.
