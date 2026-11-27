# PG17 acceptance comparison and remaining blockers

Recorded on 26 November 2026. No hosted services, operator credentials, gates,
production migrations, or deployment configuration were changed. Nothing was
pushed. All SQL probes used the existing disposable PostgreSQL 17.11 fixture.

## Baseline and verification limits

The detached baseline worktree is `/private/tmp/hmt-c3b36fd-baseline`, at
`c3b36fd`. The new Boolean acceptance module does not exist at that revision;
only its unchanged test file from `8e72393` was overlaid for the requested
four-module comparison. Parent application and migration source was unchanged.
The runtime role existed before both fresh baseline databases were created.

The baseline Docker container stayed in `Created`, with a zero start timestamp,
zero CPU/memory usage, and no OOM indication. It never executed pytest. That
specific never-started container was removed without force to cancel the attempt.
A subsequent attempt to copy the existing test image's interpreter for an
exec-only run also stalled, copying zero bytes, and was terminated. No further
container starts or full-suite attempts were made. Therefore neither baseline
nor fixed full-acceptance counts are available for this checkpoint.

The earlier completed `8e72393` run remains the latest local full acceptance
result: **48 passed, 4 failed, 0 errors, 0 skipped, 47.42 seconds**, across the four
acceptance modules (not the entire repository suite).

Bounded baseline SQL probes independently confirmed all three Boolean/integer
CHECK expressions are rejected on PG17 and the original ACL query raises
`invalid_parameter_value`. This does not establish baseline pytest counts.
In particular, parent Decimal setup fails before its catalogue assertion, so the
later catalogue mismatch must not be described as an observed parent assertion
failure. The two new comparison tests are absent from the parent; with the test
overlay, invalid parent ORM CHECK SQL prevents reaching their equality assertions.

## CHECK comparison correction

Both scratch tables now provide PostgreSQL's own canonical constraint output.
The migration scratch table uses `LIKE ... INCLUDING CONSTRAINTS` to copy the
parsed migration-created CHECK; unrelated copied constraints are removed from
that temporary probe only. The ORM scratch CHECK is installed from compiled ORM
SQL. Re-parsing `pg_get_constraintdef` text is avoided: it can distribute casts
between an array and its elements, changing text without changing truth.

Every original assertion remains. The equality to the installed migration
definition and SQLSTATE `23514` rejection assertions remain, and explicit equality
between both scratch-table canonical definitions is added. Valid-row and
violating-row probes remain for both sources.

PG17 SQL probes passed for all three scratch comparisons and all six behavioral
cases, inside a rolled-back transaction. These are targeted SQL results, not a
pytest acceptance-suite result. Offline Python 3.13.10 regression execution:
**16 passed, 2 real-PG tests deselected**, no failures or skips. New acceptance
cases still require the full PG17 execution requested above.

## Decimal failure: genuine schema-contract discrepancy, unchanged

Failing assertions in `test_exact_decimal_postgresql_acceptance.py`:

```python
assert {(row[0], row[1]) for row in numeric_rows} == expected_numeric
assert all(row[2] == -1 and row[3] == "numeric" for row in numeric_rows)
```

The query selects every numeric column in the disposable schema; expectations
come from `ExactDecimal` metadata plus the three probe columns. There are 28
additional plain `Numeric(p,s)` columns in `platform_models.py`, spanning candle,
protection, GTT, TradingView, contract-setting, risk-policy, preview, and action
tables. These have typmods and do not use the ExactDecimal boundary checks.
Changing the test to ignore them would hide the discrepancy. No assertion was
changed. Proposed separate work: review each field's numeric contract, qualify
existing values, and align ORM plus additive migrations with the agreed exact
storage/rejection semantics; preserve raw-SQL NaN, infinity and scale probes.

## ACL query: test bug corrected; genuine grant remains

The original query called:

```sql
pg_catalog.aclexplode(COALESCE(namespace.nspacl, ARRAY[]::aclitem[]))
pg_catalog.aclexplode(COALESCE(relation.relacl, ARRAY[]::aclitem[]))
```

The empty array has zero dimensions; PG17 raises SQLSTATE `22023`:
`ACL arrays must be one-dimensional`. This is a test-query bug, not a missing
Supabase extension. NULL ACLs mean object defaults, not necessarily no grants.
The correction uses `acldefault` for schemas, relations/sequences and functions,
and converts explicit empty ACL arrays to SQL NULL before `aclexplode`. Default
ACL arrays are handled the same way. No grants or production SQL were modified.

The corrected read-only query returns **1** forbidden privilege on the migrated
fixture: PUBLIC EXECUTE on
`hmt_crypto.guard_account_reconciliation_checkpoints_v1`. The function is created
in `phase5_reconcile_checkpoint_v1.py`; later runtime-role revocation alone does
not revoke PUBLIC's inherited grant. Classify this separately as a genuine
migration-hardening defect. Propose an additive, reversible migration after
review of trigger/function requirements; do not change the zero-forbidden-grants
specification to make the test pass.

The same test also contains a stale phase5 snapshot expectation of 43 tables,
29 triggers and revision `phase5_reconcile_checkpoint_v1`, although it upgrades
to head. The disposable head actually has 76 tables, 30 triggers and revision
`phase32_restrict_foreign_keys_v1`. This is another test-maintenance discrepancy,
left unchanged here rather than replacing an exact inventory with observed
counts. A follow-up should derive the expected head inventory independently
from the migration chain and retain explicit privilege assertions.

## Completed CI run

Run `45753452620` completed with conclusion **failure**. It tested the earlier
pushed revision, not these local commits. Its `full-suite-pg17` artifact was
downloaded to `~/ci-artifacts` and read with the redacting summarizer.

| Job / artifact | Result | Counts / failed step |
| --- | --- | --- |
| event-policy | Success | Event selection, no pytest job |
| backend | Failure | `ruff check .`: 76 findings; mypy skipped; no pytest count |
| ui | Success | 7 test files, 52 tests passed; typecheck/lint/build succeeded |
| deployment-shape | Success | Compose validation; no pytest count |
| postgres-acceptance | Failure | Full-suite execution step failed; artifact upload succeeded |
| full-suite-pg17 artifact | Recorded | 11,898 passed; 4 failed; 15 errors; 8 skipped; 2,052.547 seconds |

The artifact is not a separate CI job. The former 5,342 setup errors dropped to
15, all setup errors in repository PG fixtures. Their common root is the invalid
ORM Boolean/integer CHECK, corrected by local `8e72393`. The CI run predates that
fix. The four first-message groups are: 15 ProgrammingError setup errors; two
RuntimeError migration failures; one ProgrammingError Decimal failure; one
FileNotFoundError image-content failure.

The two migration failures report SQLSTATE `P0001`. The workflow creates no
`hmt_crypto_app` role, while the runtime-grants migration explicitly requires it;
this is the matching fixture-environment cause, rather than evidence that the
role-first local phase32 upgrade/downgrade failed. The image-content failure is
the missing `.dockerignore` inside the test image. Neither issue was changed in
this narrowly scoped test correction. The eight skips comprise six explicit
testnet opt-ins and the two recorded scoped-FK expected failures.

## Rollout and rollback

This commit changes tests and this report only. Reverting it restores the old
comparison/query bugs; no database downgrade is needed. Do not run Mumbai
migrations or consider acceptance green until the full role-first PG17 rerun,
numeric-contract review, ACL finding and inventory expectation are resolved.
