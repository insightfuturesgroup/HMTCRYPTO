# Owner-controlled PostgreSQL migrations

This directory is the only migration history for the Python backend. It is not a
Supabase CLI migration directory, and revisions must never call the live ORM
metadata. Run Alembic explicitly with a reviewed direct owner connection; the
application does not run migrations during startup.

Migration admission resolves the runtime URL through SQLAlchemy's asyncpg
dialect before comparing role and target identity. Query parameters may not
override user, password, host, port, or database. Disposable acceptance treats
Supabase direct and pooler aliases for one project as the same protected target.

Alembic records only its revision identifier in
`public.hmt_crypto_alembic_version`; all application data remains in the private
`hmt_crypto` schema. The hardening revision is intentionally irreversible.
Baseline-only downgrade is restricted to an explicitly acknowledged, empty
development database. Built wheels carry this directory under
`hmt_crypto_migrations/migrations/` together with the matching Alembic config.
