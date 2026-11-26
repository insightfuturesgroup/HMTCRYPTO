# Phase33 private-function ACL qualification

No production service was contacted and no gate was changed. This is an additive
forward migration from phase32, with no edits to existing migrations.

## Complete migrated function inventory before phase33

| Function signature in hmt_crypto | Trigger helper | PUBLIC / anon / authenticated EXECUTE |
| --- | --- | --- |
| `guard_account_reconciliation_checkpoints_v1()` | Yes | PUBLIC EXECUTE |
| `guard_api_audit_payload_mutation()` | Yes | None |
| `guard_credential_identity_binding()` | Yes | None |
| `purge_expired_audit_payload(uuid,text)` | No | None |
| `reject_append_only_mutation()` | Yes | None |
| `reject_platform_audit_mutation()` | Yes | None |

The catalogue audit included every pg_proc object in this schema and the effective
default ACL when proacl is NULL. Empty explicit ACL arrays are treated as no
grants. After upgrade all six routines have zero grants to the three browser
principals. Other explicit owner/application/retention grants are preserved.

## Defaults and scope

The migration revokes existing function EXECUTE from PUBLIC and, conditionally,
anon/authenticated. It removes both global and schema-local default EXECUTE grants
for the migration role and each current private-function owner. PostgreSQL's
schema defaults are additive: a schema-local REVOKE cannot subtract the built-in
global PUBLIC EXECUTE grant. This behavior was verified in the
[PG17 documentation](https://www.postgresql.org/docs/17/sql-alterdefaultprivileges.html).

The global default change affects **future functions in every schema created by
those roles**, not only hmt_crypto. It does not change existing functions outside
hmt_crypto or defaults of unrelated creator roles. Review this scope before a
hosted migration. Any new schema-creator role must receive the same reviewed
default-privilege policy before it may create private functions.

## Downgrade

Downgrade restores the repository-defined phase32 state: PUBLIC EXECUTE on the
checkpoint trigger helper and the owners' built-in global PUBLIC function
default. It does not broadly grant EXECUTE on all existing private functions.
It does not reconstruct arbitrary grants introduced outside migration history.
If the project has custom default ACLs, review that difference before upgrading
or downgrading; the fresh-baseline qualification is not a claim of exact rollback
for an arbitrary hosted ACL configuration.

Downgrade reopens the known permission gap. An application rollback can retain
phase33; do not downgrade security solely to roll back application images.

## Local results

The original new safety tests failed before the revision existed. After the
change, offline Python 3.13.10 tests passed: **42 passed, 2 real-PG cases
deselected**. Existing assertions were preserved except strict head expectations
advanced for the additive revision. The migration-history test still enumerates
the complete chain and exactly one head.

Fresh PostgreSQL **17.11**, runtime role created first:

| Scenario | Upgrade | Future private default | Trigger enforcement | Downgrade ACL equality | Re-upgrade |
| --- | --- | --- | --- | --- | --- |
| anon/authenticated absent | Pass | Pass | Pass | Pass | Pass |
| anon/authenticated present | Pass | Pass | Pass | Pass | Pass |

These database qualifications executed offline-rendered Alembic SQL through psql
inside the existing internal-only disposable PG container. They are not reported
as online Alembic/pytest acceptance runs. The container-start problem remains a
limitation for the full suite. An automated safety-marked PG acceptance test is
included for the same lifecycle, future defaults and existing trigger behavior.

Successful measured upgrade blocks took **6.95 ms** and **6.62 ms**, including
local probe overhead, without concurrent application load. The sampled catalogue
relation lock was AccessShareLock on pg_proc. No business table is rewritten.
Catalogue/object ACL changes remain transactional and locks can be held through
commit. Run owner migrations with API/workers stopped, a reviewed lock timeout,
and an independently checked target. These measurements are not production
latency or lock-wait guarantees.

The phase32-specific acceptance test is pinned to phase32 for its upgrade and
downgrade assertions. The full-head security test now strictly expects the
reviewed head inventory (76 tables, 30 triggers, phase33), and still expects
**zero forbidden grants**. CI now creates the restricted runtime role before
migrations; its former absence explains the earlier P0001 failures.

The 28-column Decimal discrepancy is report-only and still open; see
[the CI classification and complete inventory](CI_45753452620_CLASSIFICATION.md).
