# PG17 CHECK investigation

Investigated at revision `24fd84e`, before changing any CHECK, migration or
application code. All database evidence below is from a disposable local
PostgreSQL 17.11 service on an internal-only Docker network. No hosted service
was contacted. This report is not clearance to run Mumbai migrations: the
remaining acceptance failures and phase32 downgrade verification are still open.

## Complete boolean/integer CHECK inventory

The inventory enumerated application ORM metadata, identified Boolean columns,
compiled every CHECK with the PostgreSQL dialect, and checked whether each
candidate is present in PostgreSQL CREATE TABLE output. There are six candidates:

| Table | CHECK suffix | Source | Integer comparison | Emitted for PG |
| --- | --- | --- | --- | --- |
| `api_audit_events` | `descriptor_slot` | `src/hmt_crypto/persistence/_api_audit_schema_checks.py:167` (comparison at 173) | `entry_possible = 1` | Yes; invalid |
| `api_audit_events` | `terminal_truth_shape` | `src/hmt_crypto/persistence/_api_audit_schema_checks.py:257`; helper `src/hmt_crypto/persistence/_api_audit_records.py:51` | `entry_possible = 0/1`, `reconciliation_required = 0` | Yes; invalid |
| `api_audit_events` | `transfer_prior_bundle` | `src/hmt_crypto/persistence/_api_audit_schema_checks.py:285` (comparison at 289) | `prior_bytes_sent_possible = 1` | Yes; invalid |
| `account_reconciliation_checkpoints` | `external_exposure_boolean` | `src/hmt_crypto/reconciliation/checkpoint_models.py:125` | `external_exposure IN (0, 1)` | No; SQLite-generated CHECK |
| `exit_intents` | `reduce_only_boolean` | `src/hmt_crypto/persistence/protection_models.py:104` | `reduce_only IN (0, 1)` | No; SQLite-generated CHECK |
| `orders` | `requested_reduce_only_boolean` | `src/hmt_crypto/persistence/order_models.py:146` | `requested_reduce_only IN (0, 1)` | No; SQLite-generated CHECK |

The three invalid ORM expressions, in full:

```sql
-- ck_api_audit_events_descriptor_slot
(retention_mode = 'SUMMARY_ONLY')
OR (event_kind IN ('ATTEMPT_STARTED','VENUE_PRE_DISPATCH','SIMULATOR_PRE_DISPATCH')
    AND payload_slot = 'REQUEST')
OR (event_kind IN ('ATTEMPT_FINISHED','VENUE_ENTERED') AND payload_slot = 'RESPONSE')
OR (event_kind = 'SIMULATOR_OUTCOME' AND entry_possible = 1 AND payload_slot = 'RESPONSE')
```

```sql
-- ck_api_audit_events_terminal_truth_shape
(event_kind <> 'VENUE_NO_ENTRY' OR
 (entry_possible = 0 AND reconciliation_required = 0 AND outcome_kind = 'LOCAL_REJECTED' AND
  ((last_phase = 'ISSUED' AND wire_request_evidence_sha256 IS NULL
    AND pre_dispatch_audit_receipt_id IS NULL)
   OR (last_phase = 'CLAIMED_FOR_UNSIGNED_PREPARATION' AND pre_dispatch_audit_receipt_id IS NULL)
   OR (last_phase = 'AUDIT_RECORDED' AND wire_request_evidence_sha256 IS NOT NULL
       AND pre_dispatch_audit_receipt_id IS NOT NULL))))
AND (event_kind <> 'VENUE_ENTERED' OR (entry_possible = 1 AND last_phase IS NULL))
AND (event_kind <> 'SIMULATOR_OUTCOME' OR
 (last_phase IS NULL AND
  ((entry_possible = 1 AND outcome_kind IN ('ACCEPTED','REJECTED','AMBIGUOUS')
    AND pre_dispatch_audit_receipt_id IS NOT NULL)
   OR (entry_possible = 0 AND outcome_kind = 'NOT_SUBMITTED'))))
```

```sql
-- ck_api_audit_events_transfer_prior_bundle
(prior_dispatch_event_id IS NULL AND prior_consumption_id IS NULL
 AND prior_bytes_sent_possible IS NULL)
OR (prior_dispatch_event_id IS NOT NULL AND prior_consumption_id IS NOT NULL
    AND prior_bytes_sent_possible = 1)
```

## What Alembic actually installs

The frozen baseline already uses native PostgreSQL Boolean literals:

| CHECK | Migration source | Actual Boolean comparisons |
| --- | --- | --- |
| `descriptor_slot` | `migrations/versions/phase2_baseline_v1.py:3177` | `entry_possible = TRUE` |
| `terminal_truth_shape` | `migrations/versions/phase2_baseline_v1.py:3240` | `entry_possible = FALSE/TRUE`, `reconciliation_required = FALSE` |
| `transfer_prior_bundle` | `migrations/versions/phase2_baseline_v1.py:3297` | `prior_bytes_sent_possible = TRUE` |

The later migration chain does not replace these with integer comparisons.
Fresh migration and inspection of the installed constraints confirm the
distinction: this is a genuine ORM/PostgreSQL DDL portability defect, not a
failure of the corresponding Alembic CHECKs. Tests using `metadata.create_all`
expose it. No missing Supabase extension explains `operator does not exist:
boolean = integer` (SQLSTATE `42883`). Do not install an operator or extension
to hide that incompatibility.

The three generated `IN (0, 1)` constraints are omitted for PostgreSQL native
Boolean columns. They are not additional PG failures.

## Fresh role-first migration evidence

The probe ensured the runtime role existed before migrating a fresh disposable
database. It performed:

1. Create `hmt_crypto_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
   NOREPLICATION NOBYPASSRLS`.
2. Create a fresh disposable database and grant that role CONNECT.
3. Supply distinct fake owner/runtime URLs and a disposable, generated TLS CA.
4. Run `alembic upgrade head` through the unchanged owner-connection guard.
5. Inspect the revision, role flags, grants and foreign keys; SET LOCAL ROLE to
   the runtime role and execute an account SELECT.

Results:

- Upgrade succeeded: `phase32_restrict_foreign_keys_v1`.
- Role: LOGIN true; SUPERUSER, CREATEDB, CREATEROLE, REPLICATION and BYPASSRLS false.
- CONNECT and schema USAGE true; schema CREATE false.
- Account SELECT, INSERT and UPDATE true; account DELETE false.
- Runtime-role SELECT succeeded.
- All 189 application FKs have `confupdtype = 'r'` and `confdeltype = 'r'`.
- Installed application CHECK count: 925.

This does not establish that the whole acceptance suite or phase32 downgrade is
green. Those are separate checks. No existing migration was edited, no constraint
was weakened and no gate was changed.

## Why fixture errors hid the original failure

The repository fixture creates its random schema and ORM tables in one
transaction. A table-creation failure rolls back CREATE SCHEMA. Its unconditional
DROP SCHEMA then failed with `InvalidSchemaNameError`, replacing the original
`ProgrammingError` / `UndefinedFunctionError` during fixture setup (not a pytest
teardown-phase error).

The corrective fixture uses DROP SCHEMA IF EXISTS and disposes the engine in a
nested finally. It does not suppress setup failures, cancellation or genuine
cleanup failures, and does not change any schema assertion. Regression tests use
offline fakes for setup failure, cancellation, normal cleanup and cleanup failure.

Verification of the cleanup change:

- Four new safety-marked regressions failed against the unchanged fixture.
- After the fix, the affected offline regression set passed: 41 passed, no skips.
- A real repository-fixture case on disposable PG17 still fails during setup,
  as it should until the ORM defect is fixed, but now exposes `ProgrammingError`
  caused by `UndefinedFunctionError: operator does not exist: boolean = integer`.
  It no longer reports `InvalidSchemaNameError` instead.
- This is targeted verification, not a new full-suite result or CI result.
