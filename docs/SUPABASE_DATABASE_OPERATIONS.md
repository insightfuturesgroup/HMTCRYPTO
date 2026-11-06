# Supabase/PostgreSQL migration and runtime policy

HMT-Crypto owns its schema through the Alembic history in `migrations/`. No
migration runs during application startup, no Supabase migration timestamp has
been invented, and the repository is not linked to a hosted Supabase project.
This history is PostgreSQL-only. Development SQLite uses its existing explicit
schema-test path; Alembic will not translate the production history to SQLite.
The application data lives in the private `hmt_crypto` schema. `PUBLIC`, `anon`,
and `authenticated` receive no schema, table, sequence, or function access. Do
not expose these tables through the browser/Data API and never put a Supabase
service-role key or database password in a frontend or Vercel public variable.
Alembic keeps only its non-application `public.hmt_crypto_alembic_version`
bookkeeping table in `public` so the private schema can be created and removed by
the baseline; the hardening revision revokes that table from the same browser
roles and `PUBLIC`.

## Connections to provide later

Provide these values through the deployment secret manager, never in Git or a
support message containing screenshots:

- `DATABASE_MIGRATION_URL`: a direct PostgreSQL owner connection using
  `postgresql+asyncpg://...`. It must not contain query parameters. Alembic
  rejects SQLite, missing credentials, Supabase pooler hosts, port `6543`, and
  the same database role used by `DATABASE_URL`.
- `DATABASE_URL`: a required, separately credentialed least-privilege
  `postgresql+asyncpg` application URL used only to prove that the migration
  role is distinct. SQLAlchemy's asyncpg dialect resolves its effective user,
  host, port, and database before comparison; URL query keys that can override
  any of those values or the password are rejected. Non-identity driver options
  cannot replace the authority identity. Its database name must match the
  migration target. Generic deployments must use the same host/port; a
  Supabase direct host may pair with a session-pooler URL only when the project
  reference in the pooler username matches the direct host. The persistent
  trading worker should use a direct connection or Supabase session pooler. Do
  not use transaction pooling for the persistent worker because it relies on
  transaction/session semantics and database locks.
- `DATABASE_MIGRATION_CA_FILE` (optional): the exact path to Supabase's reviewed
  CA bundle when the host trust store is insufficient. The asyncpg connection
  always receives a real `SSLContext` with certificate validation and hostname
  checking enabled. `ssl`, `sslmode`, CA paths, and other driver options in the
  URL are rejected instead of being forwarded as ambiguous asyncpg kwargs.
  Record the CA source and rotation procedure before a hosted migration.
- A separate backup/restore owner connection. Backups and restores use a direct
  connection, never a transaction pooler.

A future read-only serverless dashboard may use transaction pooling only with a
separate read-only role. With asyncpg that path must set
`statement_cache_size=0`; it must not run migrations, trading workers, locks, or
write-side reconciliation. Vercel credentials are not needed to build or test
this migration history.

## Explicit commands

From the repository root, after exporting the direct owner URL, the distinct
runtime URL, and (when needed) the CA file:

```bash
alembic upgrade head
alembic current
alembic history --verbose
alembic upgrade head --sql > /tmp/hmt_crypto_upgrade.sql
```

The offline file is review evidence only; treat it as sensitive operational
material even though the DSN is deliberately absent. The frozen baseline is
checked with:

```bash
python tools/generate_migration_baseline.py
```

Despite its historical filename, that command is now a check-only verifier. It
does not import current ORM metadata and has no rewrite mode. The manifest pins
the baseline upgrade and downgrade statement sets plus the exact baseline and
hardening revision sources. Add a new revision for every later schema change.

Only a database stopped at the baseline revision can be downgraded in
development. It requires
`HMT_CRYPTO_ALLOW_EMPTY_DOWNGRADE=I_ACKNOWLEDGE_EMPTY_DEV_DATABASE`, and the
database refuses if any application table has a row. The hardening revision is
intentionally irreversible: reversing its triggers while ACL/default-privilege
revokes remain in force would not be a truthful inverse, and per-revision
transactions could leave a partially unhardened database. Once `head` has been
applied, recovery is forward-fix plus restore, never down-migration.

Wheel builds include the operator assets under `hmt_crypto_migrations/` so a
deployment artifact contains the reviewed history. Source-checkout commands
still use the root `alembic.ini`; deployment automation must select the packaged
config explicitly after extracting or locating those resources.

The opt-in disposable PostgreSQL acceptance requires
`HMT_CRYPTO_MIGRATION_ACCEPTANCE_URL` and
`HMT_CRYPTO_MIGRATION_ACCEPTANCE_DISPOSABLE=true`. It proves the baseline's
empty/data-bearing downgrade gates, head upgrade, private ACL/default-privilege
state, append-only trigger, retention purge/decoder, and irreversible hardening
downgrade. It skips when no acknowledged URL is supplied; no live PostgreSQL
evidence is claimed by an ordinary offline test run.
Its destructive target guard applies the same driver-resolved comparison as
migration admission. Supabase direct and pooler URLs for one project collapse
to one protected target even when their host, port, or database names differ.
Set `HMT_CRYPTO_REQUIRE_MIGRATION_POSTGRES=true` in the qualification job so a
missing disposable URL fails instead of silently skipping this gate.

## Runtime and retention roles

The hardening revision intentionally grants no application role. Once role names
are supplied, review and grant only `USAGE` on `hmt_crypto`, the minimum table
operations each backend component needs, and the minimum sequence/function
operations. Re-run privilege evidence after every grant. `PUBLIC`, `anon`, and
`authenticated` must remain empty of privileges.

Immutable evidence tables reject `UPDATE` and `DELETE` in the database. Audit
payload copies are the sole deletion exception. Their controlled operation is:

```sql
SELECT *
FROM hmt_crypto.purge_expired_audit_payload(
  :owning_event_id::uuid,
  :payload_slot::text
);
```

It is `SECURITY INVOKER`, accepts only the exact role name
`hmt_audit_retention`, locks one expired payload, samples database time after the
lock, appends immutable `MAINTENANCE_ROOT` and `PAYLOAD_PURGED` evidence, deletes
that one payload, and returns the safe receipt shape already defined by the
Python contract. It has no caller-supplied actor, time, payload, or event IDs.

Because `SECURITY DEFINER` is intentionally forbidden, the dedicated retention
role necessarily needs the underlying `SELECT`, `INSERT`, and `DELETE`
privileges used by the function. That role must therefore be a trusted,
non-browser, non-runtime operator role with no login except through the isolated
retention job. Do not grant payload `DELETE` or audit-event `INSERT` to the main
worker. Owner access remains an operational trust boundary and must be audited.

## Supabase CLI gate

No Supabase CLI is installed or project-linked now. Before any hosted change:

1. confirm the current Supabase CLI v3 release and breaking changes against the
   official changelog;
2. pin the reviewed v3 version in CI and local operations rather than using an
   unbounded latest install;
3. link the intended project explicitly and verify project reference, region,
   environment, database host, and backup state;
4. run a dry-run/diff and compare it with Alembic's manifest and offline SQL;
5. test upgrade, restore, and runtime privileges on a disposable project first.

Alembic remains the owner-controlled migration authority for this Python
backend. Do not create a parallel Supabase-native migration history without a
separate reviewed ownership decision.
