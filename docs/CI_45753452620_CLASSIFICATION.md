# CI classification and Decimal inventory

CI run `45753452620`: 11,898 passed, 4 failed, 15 setup errors, 8 skips. This run predates local commits `8e72393`, `fdd48fe` and phase33. No production service was contacted.

## Four failures

| Test ID | Exception and safe first message line | Root cause and disposition |
| --- | --- | --- |
| `tests.integration.test_exact_decimal_postgresql_acceptance::test_raw_postgres_decimal_boundary_and_catalogue_contract` | ProgrammingError / UndefinedFunctionError: operator does not exist: boolean = integer | Genuine ORM DDL defect fixed by 8e72393, not fdd48fe. The later 28-column numeric contract discrepancy remains. |
| `tests.integration.test_migration_postgresql_acceptance::test_migration_security_and_downgrade_contracts_on_disposable_postgres` | RuntimeError: Database migration failed without exposing server diagnostics (SQLSTATE P0001) | Fixture environment: runtime role absent in CI. Phase33 commit creates it before migrations. fdd48fe fixes a later ACL query bug, not this CI failure. Phase33 fixes the exposed PUBLIC grant; exact head inventory is updated without changing the zero-grant assertion. |
| `tests.integration.test_migration_postgresql_acceptance::test_phase32_foreign_key_actions_upgrade_and_downgrade_on_disposable_postgres` | RuntimeError: Database migration failed without exposing server diagnostics (SQLSTATE P0001) | Fixture environment: runtime role absent in CI. Phase33 commit creates it before migrations. fdd48fe fixes a later ACL query bug, not this CI failure. Phase33 fixes the exposed PUBLIC grant; exact head inventory is updated without changing the zero-grant assertion. |
| `tests.unit.test_ci_migration_ca_scope::test_test_image_copies_required_files_and_excludes_operator_configuration` | FileNotFoundError: [Errno 2] No such file or directory: /workspace/.dockerignore | Test-image environment omission; not fixed by fdd48fe. Copy into the test image in the lint/tooling commit. |

## Fifteen setup errors: one root-cause group

All 15 are fixture **setup**, not collection or teardown. Exception: `sqlalchemy.exc.ProgrammingError`, caused by `asyncpg.exceptions.UndefinedFunctionError`. Safe first-line cause: `operator does not exist: boolean = integer`. The three invalid audit CHECKs prevent `metadata.create_all`; all are fixed by `8e72393`. None is directly fixed by `fdd48fe`.

- `tests.integration.test_repository_postgresql_acceptance::test_postgres_two_connection_lease_winner`
- `tests.integration.test_repository_postgresql_acceptance::test_postgres_disabling_policy_is_observed_by_waiting_signal_claim`
- `tests.integration.test_repository_postgresql_acceptance::test_postgres_expiry_during_lease_lock_wait[renew]`
- `tests.integration.test_repository_postgresql_acceptance::test_postgres_expiry_during_lease_lock_wait[release]`
- `tests.integration.test_repository_postgresql_acceptance::test_postgres_skip_locked_disjoint_order_and_post_lock_time`
- `tests.integration.test_repository_postgresql_acceptance::test_postgres_competing_attempts_have_one_version_winner`
- `tests.integration.test_repository_postgresql_acceptance::test_postgres_cross_command_event_key_wait_before_sample`
- `tests.integration.test_repository_postgresql_acceptance::test_postgres_cancellation_while_waiting_releases_transaction`
- `tests.integration.test_repository_postgresql_acceptance::test_postgres_concurrent_attempt_and_event_64_boundary`
- `tests.integration.test_repository_postgresql_acceptance::test_postgres_reconciliation_wait_precedes_command_lock`
- `tests.integration.test_repository_postgresql_acceptance::test_postgres_claim_expiry_during_command_lock_wait`
- `tests.integration.test_repository_postgresql_acceptance::test_postgres_concurrent_enqueue_has_one_created_and_one_exact_replay`
- `tests.integration.test_repository_postgresql_acceptance::test_postgres_stale_terminal_claim_cannot_mutate`
- `tests.integration.test_repository_postgresql_acceptance::test_postgres_live_fk_wait_precedes_sample_and_expiry[account]`
- `tests.integration.test_repository_postgresql_acceptance::test_postgres_live_fk_wait_precedes_sample_and_expiry[credential]`

## Decimal inventory (report only)

The following 28 columns use plain Numeric rather than ExactDecimal in `platform_models.py`. No numeric schema is changed. Recommended replacements retain the existing business precision/scale but reject loss before binding and through typmod-free storage plus exact CHECKs. Qualify existing data and add an additive migration separately; do not merely exclude these columns from acceptance.

| Table | Column | Precision / scale | Semantic type | Recommended action |
| --- | --- | --- | --- | --- |
| `market_candles` | `open` | 38 / 18 | price | ExactDecimal retaining price precision/scale; qualify finite price/ATR/trailing-anchor bounds. |
| `market_candles` | `high` | 38 / 18 | price | ExactDecimal retaining price precision/scale; qualify finite price/ATR/trailing-anchor bounds. |
| `market_candles` | `low` | 38 / 18 | price | ExactDecimal retaining price precision/scale; qualify finite price/ATR/trailing-anchor bounds. |
| `market_candles` | `close` | 38 / 18 | price | ExactDecimal retaining price precision/scale; qualify finite price/ATR/trailing-anchor bounds. |
| `market_candles` | `volume` | 38 / 18 | quantity | ExactDecimal with finite nonnegative volume checks; preserve nullability. |
| `protection_groups` | `average_fill_price` | 38 / 18 | price | ExactDecimal retaining price precision/scale; qualify finite price/ATR/trailing-anchor bounds. |
| `gtt_orders` | `trail_anchor` | 38 / 18 | price | ExactDecimal retaining price precision/scale; qualify finite price/ATR/trailing-anchor bounds. |
| `tradingview_strategies` | `stop_loss_pct` | 9 / 6 | ratio | ExactDecimal retaining ratio precision/scale and documented bounds. |
| `tradingview_strategies` | `target_pct` | 9 / 6 | ratio | ExactDecimal retaining ratio precision/scale and documented bounds. |
| `tradingview_strategies` | `trailing_offset_pct` | 9 / 6 | ratio | ExactDecimal retaining ratio precision/scale and documented bounds. |
| `crypto_contract_settings` | `atr_multiplier` | 18 / 8 | ratio | ExactDecimal retaining ratio precision/scale and documented bounds. |
| `crypto_contract_settings` | `stop_loss_multiplier` | 18 / 8 | ratio | ExactDecimal retaining ratio precision/scale and documented bounds. |
| `crypto_contract_settings` | `target_multiplier` | 18 / 8 | ratio | ExactDecimal retaining ratio precision/scale and documented bounds. |
| `tradingview_signals` | `atr_value` | 38 / 18 | price | ExactDecimal retaining price precision/scale; qualify finite price/ATR/trailing-anchor bounds. |
| `tradingview_signals` | `atr_multiplier` | 18 / 8 | ratio | ExactDecimal retaining ratio precision/scale and documented bounds. |
| `tradingview_signals` | `stop_loss_multiplier` | 18 / 8 | ratio | ExactDecimal retaining ratio precision/scale and documented bounds. |
| `tradingview_signals` | `target_multiplier` | 18 / 8 | ratio | ExactDecimal retaining ratio precision/scale and documented bounds. |
| `account_risk_policies` | `max_order_notional` | 38 / 18 | money | ExactDecimal with strictly positive finite monetary limits/notional and existing policy validation. |
| `account_risk_policies` | `max_position_notional` | 38 / 18 | money | ExactDecimal with strictly positive finite monetary limits/notional and existing policy validation. |
| `account_risk_policies` | `max_daily_loss` | 38 / 18 | money | ExactDecimal with strictly positive finite monetary limits/notional and existing policy validation. |
| `account_risk_policies` | `usd_inr_rate` | 18 / 6 | ratio | Retain the pinned policy conversion constant; ExactDecimal bounds, never label it an FX quote. |
| `account_risk_policies` | `max_order_limit_inr` | 38 / 18 | money | ExactDecimal with strictly positive finite monetary limits/notional and existing policy validation. |
| `account_risk_policies` | `max_position_limit_inr` | 38 / 18 | money | ExactDecimal with strictly positive finite monetary limits/notional and existing policy validation. |
| `account_risk_policies` | `max_daily_loss_limit_inr` | 38 / 18 | money | ExactDecimal with strictly positive finite monetary limits/notional and existing policy validation. |
| `live_order_previews` | `limit_price` | 38 / 18 | price | ExactDecimal retaining price precision/scale; qualify finite price/ATR/trailing-anchor bounds. |
| `live_order_previews` | `price` | 38 / 18 | price | ExactDecimal retaining price precision/scale; qualify finite price/ATR/trailing-anchor bounds. |
| `live_order_previews` | `notional` | 38 / 18 | money | ExactDecimal with strictly positive finite monetary limits/notional and existing policy validation. |
| `live_order_actions` | `target_limit_price` | 38 / 18 | price | ExactDecimal retaining price precision/scale; qualify finite price/ATR/trailing-anchor bounds. |
