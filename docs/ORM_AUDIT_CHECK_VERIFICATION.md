# ORM audit Boolean CHECK correction

This correction changes only PostgreSQL compilation of `descriptor_slot`,
`terminal_truth_shape`, and `transfer_prior_bundle` on `api_audit_events`.
The baseline Alembic definitions already compare Boolean columns with Boolean
literals. They are unchanged. SQLite compilation retains the original integer
literals. The separate SQLite Boolean-domain `IN (0,1)` checks are unchanged.

## Verification evidence

Interpreter: Python 3.13.10, editable development install in
`~/hmt-venv-3.13.10`. Package installation used public package registries;
the recorded pytest, lint, and type-check execution denied all network access
with macOS `sandbox-exec`.

Before the fix, the new baseline-comparison regressions produced three failures
and three passes. Each failure was the expected Boolean/integer difference.

Additional local PostgreSQL probes used only the previously created disposable
PostgreSQL 17.11 database, with `hmt_crypto_app` created before migration.
`pg_get_constraintdef` matched all three compiled ORM definitions exactly to
their migration-created definitions. Six behavioral probes (ORM and migration
for each CHECK) accepted a valid row and rejected a violating row. Each probe
used temporary tables and rolled back. Other table constraints were deliberately
excluded from these probes so they could not mask the predicate under test.

These probes are **not** a full PostgreSQL acceptance-suite result. New
safety-marked acceptance tests repeat canonical-definition comparison and
behavioral checks through SQLAlchemy against an explicitly acknowledged
disposable migration database.

## Unverified and release checks

The full role-first PostgreSQL acceptance suite has not been rerun for this
change: Docker test-container startup was stalled at `Created`. Docker remained
responsive to inspection and `exec` in the existing disposable PG container;
the VM reported approximately 4 GB RAM and 14 running containers. No Docker
restart or additional test-container startup retries were attempted.

Before considering migration qualification green, run the full acceptance
suite on the pinned test image and internal-only PG17 network, with the runtime
role created first. Include the new audit Boolean acceptance module. Preserve
JUnit and report all failures and skips; targeted probes do not substitute for
that result. No hosted database or exchange verification was performed.

## Rollout and rollback

No migration is required. Existing migrated PostgreSQL databases already use
the correct predicates. Check the three compiled definitions and run the
acceptance tests before deployment. Keep all dispatch gates closed.

Rollback is a revert of this commit; it restores the previous ORM compilation
bug, so metadata-based PostgreSQL test setup would fail again. No database
downgrade or data change is needed.
