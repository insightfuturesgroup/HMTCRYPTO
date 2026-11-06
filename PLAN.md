# HMT-Crypto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new, independent Python 3.11+ crypto trading system for Delta Exchange, preserving HMT's proven strategy and safety behavior while removing all Zerodha/India-market coupling.

**Architecture:** A dedicated async worker owns exchange connectivity, rate limiting, market state, risk enforcement, OMS submission and reconciliation. FastAPI exposes health/control/read endpoints. SQLAlchemy/Alembic persists durable intent and exchange observations in SQLite for development and Postgres for production. A React dashboard adapted from HMT is added only in Phase 5.

**Tech Stack:** Python 3.11+, FastAPI/Uvicorn, `httpx`, `websockets`, Pydantic v2 + `pydantic-settings`, SQLAlchemy 2 async + Alembic, `aiosqlite`/`asyncpg`, `structlog`, Typer, Prometheus client, pytest/pytest-asyncio, `respx`, Hypothesis where useful, Ruff and mypy. Phase 5 adds Argon2-backed operator sessions and a React 18 + TypeScript + Vite UI adapted from HMT.

**Spec:** [`AUDIT.md`](./AUDIT.md), the user's phased requirements, [Delta India API docs](https://docs.delta.exchange/), and [Delta Global API docs](https://docs-global.delta.exchange/), revalidated at the start of every implementation phase.

## Global Constraints

- The original HMT project and supplied archive are read-only references. No runtime import, database migration, shared secret, symlink, or deployment dependency may couple HMT-Crypto back to HMT.
- Stop after each phase and obtain explicit user approval before starting the next phase.
- Never place a production order during development or tests. CI must reject production base URLs and production credentials.
- Through Phases 1–4 the transport rejects every write method when `DELTA_ENV=prod`, regardless of flags or credentials. Phase 5 may implement a production-capable path, but it remains read-only until a separately reviewed persisted activation/qualification gate is approved; this project task never activates it or sends a production order.
- Default configuration is `PAPER`, `india`, `testnet`.
- All money, prices, quantities, fees and rates use `Decimal`; binary `float` is forbidden at exchange/domain boundaries.
- All persisted timestamps are timezone-aware UTC. Display-time conversion belongs only in the UI.
- Exchange state is authoritative. Local state represents durable intent, observations and reconciled projections.
- Every risk-increasing order must pass a fresh, synchronous risk decision and obtain an atomic reservation immediately before OMS submission.
- Order placement is never blindly retried. A timeout/disconnect becomes `UNCERTAIN` and must be resolved by client order ID, exchange order ID, history and fills.
- Secrets, signatures, signed prehashes, authorization headers and complete credential fingerprints must never be logged, returned by APIs, or stored in the audit log.
- Public interfaces and public functions receive Python type hints and docstrings. Modules remain small and cohesive.
- Every behavior depending on Delta documentation gets a source comment/link and a contract test/fixture where practical.
- Fail closed for stale price/account/catalogue data, unsupported product models, sequence gaps, incomplete reconciliation, clock uncertainty in LIVE mode, or ambiguous exchange state.
- SQLite is limited to single-process BACKTEST/PAPER. Testnet-LIVE and any future production-LIVE require Postgres and its real concurrency/fencing acceptance suite.
- Order transitions and API/audit evidence are append-only with database-enforced restricted grants/triggers, monotonic sequence/version and idempotency keys; an application convention alone is insufficient.

## Review Focus

- Safety under timeout, crash and reconnect; no double submission.
- Exact region/environment/credential binding.
- Product-specific contract conversion and `Decimal` correctness.
- Atomic risk reservations across positions and open orders.
- Exchange-truth reconciliation before accepting new risk.
- Explicit LIVE gating and production-order test prevention.
- Current Delta endpoint/schema/channel limits rather than historical HMT assumptions.
- Redaction, authorization, operator audit trail and kill-switch semantics.

---

## 1. Target architecture

```text
                       +-----------------------------+
                       | React dashboard / CLI       |
                       | commands, status, kill      |
                       +--------------+--------------+
                                      |
                              FastAPI control/read API
                                      |
                 +--------------------+--------------------+
                 |                                         |
          command repository                         query repositories
                 |                                         |
                 +--------------------+--------------------+
                                      |
                     dedicated async execution worker
                                      |
       +------------------------------+------------------------------+
       |                              |                              |
 ProductCatalog +              MarketDataService               Reconciler
 REST/WS adapters              quotes/books/funding       startup/periodic/reconnect
       |                              |                              |
       +----------------------- StrategyEngine ----------------------+
                                      |
                                RiskManager
                         checks + atomic reservation
                                      |
                                     OMS
                   intent -> submit -> observe -> reconcile
                                      |
                   exchange-neutral ExchangeClient protocol
                                      |
                    Delta REST + public/private WebSocket
                                      |
                             Delta Exchange

 Persistence: products, signals, orders/attempts/transitions, fills,
 positions/balances/PnL/funding snapshots, risk state, reconciliation,
 kill events, and redacted API audit events.
```

### Process boundaries

1. `hmt-crypto-api`: FastAPI health, read models, authenticated operator commands. It never submits exchange orders directly.
2. `hmt-crypto-worker`: single active leader per configured Delta user/account profile. Owns REST client, user-scoped rate limiter, sockets, OMS, risk and reconciliation.
3. `hmt-crypto-cli`: setup diagnostics, catalogue sync, paper runner, reconciliation, kill switch and controlled maintenance.
4. `hmt-crypto-ui` in Phase 5: HMT-style browser dashboard calling only the API.

Keeping exchange writes in the worker makes one limiter central and avoids duplicate schedulers under multiple Uvicorn workers/reload. V1 allows one active trading account per worker/deployment profile. A Postgres lease plus account-scoped leadership generation/fencing token is checked immediately before signing/sending; the worker terminates on lease/DB-session loss. Advisory locks alone are not treated as an HTTP-write fence. Two workers using the same external Delta user are prohibited unless they share one durable limiter/leader because Delta's authenticated quota is user-scoped. SQLite permits one local PAPER worker only.

## 2. Proposed repository layout

This is a planned layout; Phase 1 creates only the files it needs.

```text
HMT-Crypto/
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
├── AUDIT.md
├── PLAN.md
├── alembic.ini
├── migrations/
│   ├── env.py
│   └── versions/
├── src/hmt_crypto/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── errors.py
│   ├── logging.py
│   ├── clock.py
│   ├── domain/
│   │   ├── enums.py
│   │   ├── models.py
│   │   ├── money.py
│   │   └── events.py
│   ├── exchange/
│   │   ├── base.py
│   │   ├── credentials.py
│   │   └── delta/
│   │       ├── endpoints.py
│   │       ├── signer.py
│   │       ├── schemas.py
│   │       ├── paginator.py
│   │       ├── rate_limiter.py
│   │       ├── client.py
│   │       ├── catalog.py
│   │       ├── sizing.py
│   │       └── websocket.py
│   ├── market_data/
│   │   ├── service.py
│   │   └── state.py
│   ├── strategy/
│   │   ├── base.py
│   │   ├── engine.py
│   │   ├── indicators.py
│   │   └── funding.py
│   ├── risk/
│   │   ├── models.py
│   │   ├── manager.py
│   │   └── reservations.py
│   ├── oms/
│   │   ├── models.py
│   │   ├── state_machine.py
│   │   ├── service.py
│   │   ├── paper.py
│   │   ├── reconciliation.py
│   │   └── kill_switch.py
│   ├── persistence/
│   │   ├── db.py
│   │   ├── models.py
│   │   ├── repositories.py
│   │   └── redaction.py
│   ├── observability/
│   │   ├── health.py
│   │   ├── metrics.py
│   │   └── alerts.py
│   ├── production/
│   │   └── qualification.py
│   ├── api/
│   │   ├── app.py
│   │   ├── auth.py
│   │   └── routes/
│   └── worker/
│       ├── runtime.py
│       ├── leadership.py
│       └── scheduler.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── fixtures/delta/
├── ui/                         # Phase 5 only
├── Dockerfile                  # Phase 5
└── docker-compose.yml          # Phase 5
```

## 3. Core contracts

### 3.1 Exchange-neutral client

`src/hmt_crypto/exchange/base.py` will define a `Protocol`/abstract interface. Proposed surface:

```python
class ExchangeClient(Protocol):
    async def get_products(self) -> list[Product]: ...
    async def get_ticker(self, product: ProductRef) -> Ticker: ...
    async def get_candles(self, request: CandleRequest) -> list[Candle]: ...
    async def get_balance(self) -> list[Balance]: ...
    async def get_positions(self, query: PositionQuery) -> list[Position]: ...
    async def place_order(self, request: PlaceOrderRequest) -> Order: ...
    async def cancel_order(self, request: CancelOrderRequest) -> Order | None: ...
    async def modify_order(self, request: ModifyOrderRequest) -> Order: ...
    async def get_open_orders(self, query: OrderQuery | None = None) -> list[Order]: ...
    async def get_order_history(self, query: OrderHistoryQuery) -> list[Order]: ...
    async def get_fills(self, query: FillQuery) -> list[Fill]: ...
    async def set_leverage(self, product: ProductRef, leverage: Decimal) -> Leverage: ...
    async def set_margin_mode(self, mode: MarginMode, account: AccountRef | None = None) -> MarginModeResult: ...
    async def change_position_margin(self, product: ProductRef, delta: Decimal) -> Position: ...
    def subscribe_market_data(self, request: MarketSubscription) -> AsyncIterator[MarketEvent]: ...
    def subscribe_private(self, request: PrivateSubscription) -> AsyncIterator[PrivateEvent]: ...
```

The protocol uses domain objects only; Delta response schemas remain inside `exchange/delta`. Public API callers cannot bypass risk/OMS by obtaining the concrete client.

### 3.2 Error hierarchy

`src/hmt_crypto/errors.py`:

```text
HmtCryptoError
├── ConfigurationError
├── ExchangeError
│   ├── AuthError
│   │   ├── SignatureExpiredError
│   │   └── UnauthorizedApiAccessError
│   ├── RateLimitError(retry_after)
│   ├── ExchangeDownError
│   ├── ExchangeTimeoutError
│   ├── AmbiguousExchangeResult
│   ├── ExchangeProtocolError
│   └── OrderRejected
├── UnsupportedProductError
├── RiskRejected
├── ReconciliationRequired
└── KillSwitchActive
```

Errors expose safe codes/context, never secrets or unredacted exchange bodies.

### 3.3 Execution modes and gates

| Mode | Market data | Exchange private reads | Exchange order writes |
|---|---|---|---|
| `BACKTEST` | recorded/local | no | no |
| `PAPER` | live public or recorded | optional read-only diagnostics | no; simulator only |
| `LIVE` | live | yes | yes, only after all gates |

Defaults: `EXECUTION_MODE=PAPER`, `DELTA_REGION=india`, `DELTA_ENV=testnet`.

LIVE startup requires all of:

- `DELTA_LIVE_TRADING_ENABLED=true`.
- `DELTA_LIVE_CONFIRMATION=I_UNDERSTAND_THIS_CAN_PLACE_REAL_ORDERS`.
- A credential pair in the exact selected region/environment slot.
- Clock/private signed-read check passed.
- Catalogue fresh and selected products supported.
- Startup reconciliation completed with no unacknowledged discrepancies.
- Kill switch not latched.
- Risk policy loaded and valid.
- For production: the Phase 5 persisted 24-hour qualification is valid and bound to account, region/environment, credential version, egress identity, build/config/schema versions and current reconciliation state; operator acknowledgement of those identities; and a loud structured/console banner.

Tests set a hard `PRODUCTION_WRITES_FORBIDDEN` guard. The production transport itself is write-disabled through Phase 4, not merely guarded in tests. Integration tests may use LIVE semantics only on testnet, behind an explicit pytest marker and environment switch.

### 3.4 Credential isolation

V1 is explicitly one active Delta trading account per worker/deployment profile. The database remains account-scoped so later profiles do not collide, but a worker receives only one selected credential set. No generic `DELTA_API_KEY` variable is accepted. Planned slots within one deployment profile:

```text
DELTA_INDIA_TESTNET_API_KEY / _API_SECRET
DELTA_INDIA_PROD_API_KEY    / _API_SECRET
DELTA_GLOBAL_TESTNET_API_KEY / _API_SECRET
DELTA_GLOBAL_PROD_API_KEY    / _API_SECRET
```

The selected `(DELTA_REGION, DELTA_ENV)` resolves exactly one slot and exact allowlisted REST/WS endpoints. Supplying credentials in the wrong slot or multiple generic aliases is a configuration error. A `CredentialProvider` boundary supports environment/secret-file injection now and a KMS/vault adapter later; the database stores only account identity, fingerprint and credential version by default. Logs may include an irreversible short SHA-256 fingerprint, never key characters.

### 3.5 Product catalogue and conversion

`ProductCatalog` is scoped by `(region, environment)` and stores immutable snapshots keyed by both numeric `product_id` and exact symbol. Duplicate IDs/symbols, missing assets, invalid decimals, non-positive tick/contract values, or ambiguous settlement fail publication.

An adapter registry handles contract models. The initial `LinearContractSizer` supports only verified linear products and exposes:

```python
contracts_to_base_quantity(product, contracts) -> Decimal
contracts_to_notional(product, contracts, price) -> Decimal
quantity_to_contracts(product, quantity, rounding) -> int
notional_to_contracts(product, notional, price, rounding) -> int
```

Conversion never silently rounds up risk. A non-integral request must declare a rounding policy; risk-increasing conversions default to toward-zero and reject zero. Unsupported product/notional types fail closed.

### 3.6 OMS state machine

```text
INTENT_CREATED
  -> RISK_APPROVED
  -> SUBMITTING
     -> ACKNOWLEDGED -> PARTIALLY_FILLED -> FILLED
     -> REJECTED
     -> UNCERTAIN -> ACKNOWLEDGED / REJECTED / OPERATOR_REVIEW
ACKNOWLEDGED/PARTIALLY_FILLED -> CANCEL_PENDING -> CANCELLED / UNCERTAIN
ACKNOWLEDGED/PARTIALLY_FILLED -> MODIFY_PENDING -> ACKNOWLEDGED / UNCERTAIN
```

Every transition is append-only and validated. The order projection is derived transactionally. Scoped unique constraints prevent duplicate intent and double adoption.

Exchange identities are never globally unique in the local database. Order/fill constraints and adoption lookups are scoped by `(account_id, region, environment, exchange_order_id)` and `(account_id, region, environment, exchange_fill_id)`; client order IDs are similarly scoped to the account/venue environment.

Every non-flat position also has a persisted protection lifecycle:

```text
PROTECTION_REQUIRED -> PROTECTION_SUBMITTING -> PROTECTED
         |                    |                    |
         +--------------------+--------------------+-> ATTENTION
```

`PROTECTED` means exchange-truth reconciliation proves sufficient reduce-only stop/worst-case coverage for the current filled size. Partial fills resize protection; missing, undersized, ambiguous or cancelled protection becomes `ATTENTION`, latches new risk off and triggers re-establishment or controlled flattening.

### 3.7 Risk decision

`RiskManager.evaluate(order, context)` returns an immutable decision containing individual check results, observed state versions, approved quantity and reservation amount. Mandatory checks:

- Product enabled/supported and catalogue fresh.
- Market/product state tradeable.
- Quote/mark fresh and price collar respected.
- Max position size per product.
- Max total gross/net exposure, including risk-increasing open orders.
- Max configured/effective leverage.
- Max loss per trade using stop or configured worst-case rule.
- Max daily loss and latched automatic halt.
- Max open orders.
- Max order rate.
- Duplicate signal/order key.
- Verified reduce-only protection for every existing non-flat position; an under-protected position blocks all new risk.
- Available collateral/margin buffer.
- Reconciliation/private-stream health.
- Kill switch.

Reduce-only risk-reducing actions remain possible during most halts, subject to position truth, price/product state and operator policy.

## 4. Phase 0 — audit and plan

- [x] Audit the supplied archive without changing HMT.
- [x] Classify every logical module/file family.
- [x] Verify current Delta India/Global documentation and record ambiguities.
- [x] Run read-only build/test/typecheck verification in a temporary copy.
- [x] Produce `AUDIT.md` and this implementation plan.
- [ ] Obtain explicit user approval before Phase 1.

**Phase 0 changed files:** `AUDIT.md`, `PLAN.md` only.  
**Verified:** source structure, current tests/build, documented Delta contracts listed in the audit.  
**Assumed/open:** documented in `AUDIT.md` section 19.

## 5. Phase 1 — scaffold, configuration, logging, public REST and catalogue

### Task 1.1: Create the Python package and quality gates

**Planned files:** `pyproject.toml`, `.gitignore`, `.env.example`, `src/hmt_crypto/__init__.py`, `tests/conftest.py`, CI workflow if the repository uses GitHub.

- [ ] Write a smoke test that imports `hmt_crypto` and asserts Python >=3.11.
- [ ] Add the minimal package/dependency/tool configuration.
- [ ] Configure Ruff, mypy strict-enough settings, pytest markers (`unit`, `contract`, `integration`, `testnet`).
- [ ] Add a CI safety fixture that fails if a test attempts a write to a production Delta host.
- [ ] Run `pytest -q`, `ruff check .`, `ruff format --check .`, and `mypy src`.

### Task 1.2: Typed configuration and LIVE gates

**Planned files:** `src/hmt_crypto/config.py`, `tests/unit/test_config.py`, `.env.example`.

- [ ] First write tests for defaults, exact endpoint matrix, separate credential slots, secret representation, unknown/generic key rejection and invalid region/environment combinations.
- [ ] Write tests proving PAPER/testnet is the default and order-write capability is false.
- [ ] Write tests for all LIVE gates and the exact confirmation phrase.
- [ ] Implement Pydantic settings and `ExecutionMode`, `DeltaRegion`, `DeltaEnvironment` enums.
- [ ] Implement safe diagnostic output containing only selected slot name and irreversible key fingerprint.
- [ ] Run focused tests, then the full quality suite.

### Task 1.3: Structured logging and redaction

**Planned files:** `src/hmt_crypto/logging.py`, `src/hmt_crypto/persistence/redaction.py`, `tests/unit/test_logging_redaction.py`.

- [ ] Write parameterized tests for API key, secret, signature, authorization, signed prehash, nested payload and query-string redaction.
- [ ] Test that exception rendering cannot leak an `httpx.Request` authorization/signature header.
- [ ] Implement `structlog` JSON/console configuration, correlation IDs and component levels.
- [ ] Add safe request/response summary helpers with body size/hash and allowlisted fields.
- [ ] Run tests and scan captured logs for fixture secrets.

### Task 1.4: Domain types and error hierarchy

**Planned files:** `src/hmt_crypto/errors.py`, `src/hmt_crypto/domain/{enums,models,money}.py`, `tests/unit/test_domain_models.py`.

- [ ] Write tests for Decimal-only parsing, UTC-aware timestamps, immutable IDs and enum validation.
- [ ] Write safe error serialization tests.
- [ ] Implement product, asset, ticker, candle and request/reference models needed in Phase 1.
- [ ] Reject float input at exchange/domain boundaries unless it came from a deliberately normalized test fixture.

### Task 1.5: Endpoint registry and clock readiness

**Planned files:** `src/hmt_crypto/exchange/delta/endpoints.py`, `src/hmt_crypto/clock.py`, `tests/unit/test_endpoints.py`, `tests/unit/test_clock.py`.

- [ ] Test the four REST endpoints and India WebSocket endpoints exactly against current docs.
- [ ] Record the Global public/private hosts exactly as officially documented, but mark streaming capability `disabled_pending_testnet_contract` until Phase 3 verifies host/auth/channel behavior.
- [ ] Test that arbitrary override hosts are rejected unless an explicit development-only override is enabled and LIVE is impossible.
- [ ] Implement a clock-readiness result using local synchronization diagnostics, optional HTTP `Date` observation, and signed-probe outcome.
- [ ] Require a successful signed private read before enabling order writes; absence of a documented `/time` endpoint is preserved as an explicit constraint.

### Task 1.6: Signature generation

**Planned files:** `src/hmt_crypto/exchange/delta/signer.py`, `tests/unit/test_delta_signer.py`, `tests/fixtures/delta/signing_vectors.json`.

- [ ] Create at least one fixed known vector with method, timestamp, path, canonical query, byte-exact JSON payload, secret, expected prehash and expected HMAC hex.
- [ ] Test empty query/body, Unicode/JSON bytes, percent-encoded query values and case-normalized method.
- [ ] Test that timestamp is acquired and signature generated immediately before send, not when a request object is queued.
- [ ] Implement a pure signer with injected clock and canonical-byte input.
- [ ] Ensure signer objects redact their secret and cannot be serialized accidentally.

### Task 1.7: Weighted rate limiter and safe retry policy

**Planned files:** `src/hmt_crypto/exchange/delta/rate_limiter.py`, `tests/unit/test_rate_limiter.py`.

- [ ] Use a fake monotonic clock to test region-specific fixed five-minute quotas (India 20,000; Global 10,000) and versioned endpoint weights.
- [ ] Test `X-RATE-LIMIT-RESET`, `/v2/rate_limits/quota`, cancellation safety, waiter fairness, cancellation of waiting tasks and per-product operation limits.
- [ ] Key authenticated budget state by external Delta user/account; reject a second independent writer for the same user unless it shares the durable limiter/leader.
- [ ] Test exponential backoff with bounded jitter for safe reads on 429/5xx.
- [ ] Test that create-order POST is never automatically retried and that ambiguous write results raise `AmbiguousExchangeResult`.
- [ ] Implement one limiter owned by the worker/client and a retry classifier that defaults to no retry.

### Task 1.8: Generic cursor paginator

**Planned files:** `src/hmt_crypto/exchange/delta/paginator.py`, `tests/unit/test_paginator.py`.

- [ ] Test normal multi-page iteration, empty result, `meta.after`, page-size cap, max pages/items, repeated cursor and missing cursor.
- [ ] Test cancellation and propagation of typed exchange errors.
- [ ] Implement an async generic paginator that never mutates caller filters.

### Task 1.9: Delta public REST client

**Planned files:** `src/hmt_crypto/exchange/base.py`, `src/hmt_crypto/exchange/delta/{schemas,client}.py`, `tests/contract/test_delta_public_client.py`, recorded sanitized fixtures.

- [ ] Write `respx` contract tests for success/error envelopes, decimal strings, mixed timestamp fields, timeouts, 429, 5xx, malformed JSON, redirects and response-size limits.
- [ ] Test public products, ticker(s), L2 orderbook and OHLC candles, including the 2,000-candle cap and supported resolution validation.
- [ ] Implement one configured `httpx.AsyncClient`, explicit timeouts, connection limits and mandatory `User-Agent`.
- [ ] Add a strict endpoint registry; through Phase 4 it rejects every production write method before network I/O and always rejects any withdrawal, transfer, deposit-address or other fund-movement path.
- [ ] Add negative tests proving unknown and fund-movement paths cannot be requested through the client.
- [ ] Map Delta error codes to the hierarchy; retain safe response correlation metadata.
- [ ] Implement only documented endpoint shapes. Unknown fields may be retained in a safe extras map, but required-field drift fails contract tests.

### Task 1.10: Product catalogue

**Planned files:** `src/hmt_crypto/exchange/delta/catalog.py`, `tests/unit/test_product_catalog.py`, `tests/fixtures/delta/products*.json`.

- [ ] Test cursor-based full load, unique ID/symbol mapping, exact assets, tick/contract decimals, state, contract type, settlement and leverage limits.
- [ ] Test duplicate/malformed products and atomic last-good publication.
- [ ] Test startup load and periodic refresh policy without replacing a good catalogue on a bad fetch.
- [ ] Implement immutable catalogue snapshots with version, fetched-at, freshness and source region/environment.
- [ ] Persisting catalogue snapshots is deferred to Phase 2; Phase 1 keeps an in-memory last-good snapshot.

### Task 1.11: Contract/quantity/notional conversion

**Planned files:** `src/hmt_crypto/exchange/delta/sizing.py`, `tests/unit/test_contract_sizing.py`.

- [ ] Write linear known-vector/property tests and Decimal boundary cases.
- [ ] Test explicit rounding modes, no silent risk-increasing round-up, zero/remainder rejection and tick-size quantization.
- [ ] Test fail-closed behavior for options, inverse, quanto/nonlinear or missing contract metadata.
- [ ] Implement the adapter registry and `LinearContractSizer` only.

### Task 1.12: Phase 1 CLI and verification

**Planned files:** `src/hmt_crypto/cli.py`, `tests/unit/test_cli.py`, initial `README.md` section.

- [ ] Add read-only commands: `config-check`, `clock-check`, `products sync`, `products show`, `ticker`, `candles`.
- [ ] Test that commands redact secrets and cannot perform order writes.
- [ ] Run the full unit/contract suite, static checks and build/package installation.
- [ ] If testnet public internet is available, run read-only smoke calls against India and Global testnet; save no secrets/raw personal data.
- [ ] Recheck official docs and record any drift in the phase report.
- [ ] Stop and report files changed, commands, verified/assumed behavior and open questions; wait for approval.

**Phase 1 acceptance criteria:** package installs on Python 3.11+, default startup is PAPER/testnet, four REST environments are exact, signer known vectors pass, rate/pagination/catalogue/sizing tests pass, public endpoints work from sanitized fixtures and optional read-only testnet smoke, and no private write path exists.

## 6. Phase 2 — private REST, persistence, OMS, risk, reconciliation and paper trading

### Task 2.1: Clean persistence baseline

**Planned files:** `persistence/{db,models,repositories}.py`, Alembic config/initial migration, `tests/integration/test_database_schema.py`.

- [ ] Write SQLite tests for single-process PAPER behavior and real Postgres tests for reservations, uniqueness races, claims, leadership fencing, transaction isolation and monetary constraints.
- [ ] Create accounts/credential metadata, catalogue snapshots, signals, durable command operations, orders, order attempts/transitions, fills, position protections, position/balance/PnL/funding snapshots, risk reservations/state, reconciliation runs, kill events, scheduler/heartbeat state, production qualification evidence and redacted API audit events.
- [ ] Scope unique client/exchange order and fill constraints by account, region and environment so testnet/production/regions/accounts cannot cross-adopt coincident IDs.
- [ ] Store SQLite decimals as canonical text/scaled integers through a tested adapter; use explicit Postgres `NUMERIC` precision/scale where product data permits.
- [ ] Enforce append-only event/audit tables with restricted grants/triggers, monotonic sequence/version and idempotency keys.
- [ ] Add UTC/Decimal checks, empty-development up/down tests and transaction helpers.
- [ ] Define production migration policy: migration lock/preflight, API-worker-schema compatibility, expand/migrate/contract releases, backup/restore test, and forward-fix/data-preserving rollback. No production down-migration is allowed while positions, orders or unresolved operations exist; disable new entries while preserving monitors/recovery/obligations.
- [ ] Implement retention settings for verbose API audit bodies while retaining hashes/status/correlation metadata.

### Task 2.2: Private Delta REST surface

**Planned files:** extend Delta schemas/client; `tests/contract/test_delta_private_client.py`, `tests/integration/test_delta_testnet_private.py`.

- [ ] Fixture-test balances, wallet transactions, latest/margined positions, open/history orders, fills, create/modify/cancel/cancel-all, leverage, margin-mode change, position-margin change, bracket fields and client-order lookup.
- [ ] Accept only the documented `isolated|portfolio` margin-mode request values; preserve but do not select any additional response value until testnet behavior is verified.
- [ ] Test `SignatureExpired`, `UnauthorizedApiAccess`, `invalid_api_key`, IP whitelist errors and clock context.
- [ ] Enforce documented page/filter limits and real-time vs margined position semantics.
- [ ] Maintain a positive allowlist containing only the named read/trading resources; reject unknown, withdrawal, transfer, deposit-address and other fund-movement paths before network I/O, with negative contract tests.
- [ ] Reject every production write method structurally; Phase 2 LIVE integration means testnet only.
- [ ] Implement testnet integration tests skipped when exact testnet keys are absent.
- [ ] Never include a production-write integration marker.

### Task 2.3: Durable API audit

- [ ] Write tests that every client call creates a redacted audit event for success, rejection, timeout and ambiguity.
- [ ] Store sanitized request/response, hashes, latency, status, endpoint ID, weight, correlation and attempt number.
- [ ] Prove through tests that keys, signatures and auth headers cannot appear in the database.
- [ ] Prove update/delete of append-only audit evidence is denied to normal API/worker roles.

### Task 2.4: Durable command queue and OMS state machine

**Planned files:** `oms/{models,state_machine,service}.py`, `tests/unit/test_order_state_machine.py`, `tests/integration/test_oms_transactions.py`.

- [ ] Write transition-table tests, illegal-transition tests and crash checkpoints.
- [ ] Implement durable command receipts with unique request ID/payload hash, `QUEUED -> CLAIMED -> SUBMITTING -> UNCERTAIN|COMPLETED|FAILED`, claim lease/generation, safe result/error and idempotent replay.
- [ ] Route API/CLI order, reconciliation and kill actions through the durable queue; never acknowledge an in-memory-only dispatch as durable.
- [ ] Persist intent before exchange I/O and commit `SUBMITTING` immediately before send.
- [ ] Attach account-scoped leadership generation to claims/transitions and revalidate it immediately before signing/sending; terminate the worker on lease or DB-session loss.
- [ ] Generate deterministic <=32-character client order IDs.
- [ ] On timeout/disconnect, mark `UNCERTAIN`; query by client ID/order ID/history/fills before any retry decision.
- [ ] Treat create, modify and cancel ambiguities explicitly.
- [ ] Make REST observations idempotently update projections and append transitions.
- [ ] Implement the persisted protection lifecycle. Every partial/full entry fill creates or resizes verified reduce-only worst-case/stop coverage; missing, undersized, cancelled or ambiguous protection moves to `ATTENTION` and blocks new risk.
- [ ] Prefer exchange-native bracket/protection semantics where current docs/testnet prove them; software-triggered protection never substitutes for a verified exchange order without an explicit risk decision.

### Task 2.5: Mandatory risk manager

**Planned files:** `risk/{models,manager,reservations}.py`, `tests/unit/test_risk_manager.py`, `tests/integration/test_risk_reservations.py`.

- [ ] Write one focused test group per mandatory check and boundary.
- [ ] Include positions plus risk-increasing open orders in total exposure.
- [ ] Define side/reduce-only-aware exposure math and reject ambiguous position truth.
- [ ] Use fresh mark price by default for collars/liquidation risk; configurable last-price fallback must be explicit.
- [ ] Reserve exposure/order-rate/open-order capacity atomically before OMS submission and release/adjust on outcome/fill.
- [ ] Build daily accounting from deduplicated fills and wallet transactions plus equity/position snapshots. Prove with sanitized testnet fixtures which Delta aggregates already include fees, funding or cashflows; never sum overlapping `realized_pnl`, `realized_funding`, `realized_cashflow`, commission or settlement fields by assumption.
- [ ] At the configured UTC reset, persist baseline equity and the exact ledger cursor/window. Compare the limit against the more conservative of reconciled realised loss and account-equity drawdown including unrealised mark-to-market, then add a worst-case reserve for unresolved fills/operations.
- [ ] Latch an automatic halt when the conservative daily-loss measure breaches the limit; missing/ambiguous accounting fails closed.
- [ ] Permit controlled reduce-only risk reduction during halt without allowing position reversal.

### Task 2.6: Reconciliation service

**Planned files:** `oms/reconciliation.py`, `tests/unit/test_reconciliation.py`, `tests/integration/test_reconciliation_recovery.py`.

- [ ] Test startup barrier, periodic reconciliation, reconnect-triggered reconciliation and single-run leadership.
- [ ] Compare open orders, order history/fills, positions and balances with local projections.
- [ ] Adopt exchange observations without silently adopting unmanaged risk; surface external orders/positions for acknowledgement.
- [ ] Resolve uncertain submissions and partial fills idempotently.
- [ ] Block new risk until critical discrepancies are resolved or explicitly acknowledged with audit evidence.

### Task 2.7: Credential provider and flat-only rotation

**Planned files:** `exchange/{credentials,credential_provider}.py`, credential metadata repository, CLI diagnostics and tests.

- [ ] Implement environment/secret-file `CredentialProvider`; persist only fingerprint/version/account metadata unless a separately reviewed KMS/envelope provider is used.
- [ ] Own the sealed exact `Task27EnrollmentCapabilityIssuer` in `credential_provider.py`; the B5 `Task27EnrollmentCapabilityIssuerPort` is typing-only. Production composition must reject structural, subclassed, copied, deserialized and tests-only adapters before method lookup, and remain disabled until the exact provider/factory is accepted.
- [ ] On a changed fingerprint/version, disable entries and require a read-only identity/permission probe plus reconciliation proving no positions, open orders or unresolved operations.
- [ ] Commit the new metadata version only after flat verification; reset all soak/qualification evidence and require monitoring requalification.
- [ ] Never expose credential material to the API/browser. Document external secret update/restart steps.

### Task 2.8: Durable kill latch and cancel workflow

**Planned files:** `oms/kill_switch.py`, CLI command, repositories and crash/restart tests.

- [ ] Persist an idempotent entry-disable latch before any external action; every risk-increasing path checks it.
- [ ] Add `hmt-crypto kill engage` and status/release commands with operator audit and explicit release preconditions.
- [ ] Cancel every pre-existing open order. Cancel risk-increasing orders first; before cancelling protection on a retained position, atomically modify it or establish/reconcile replacement reduce-only coverage, then cancel the old order. If that cannot be done safely, flatten instead. The final state may contain only newly verified protective orders for intentionally retained positions.
- [ ] Implement REST reconciliation and resumable step state for PAPER/testnet. Do not claim kill completion while orders/state are unknown or any retained position is under-protected.
- [ ] Test restart at every step, repeated invocation and unmanaged/external orders.
- [ ] Optional position flattening and the two-step dashboard control remain Phase 5 work.

### Task 2.9: Paper execution simulator

**Planned files:** `oms/paper.py`, `tests/unit/test_paper_fills.py`.

- [ ] Model deterministic market/limit/stop/trailing/bracket behavior supported by the domain contract.
- [ ] Use top-of-book/candle inputs with explicit slippage and latency assumptions. Phase 2 uses a conspicuously labeled configurable synthetic/zero-fee model; authoritative sourced fees and funding arrive in Phase 4.
- [ ] Guarantee PAPER cannot invoke private order-write methods even when credentials exist.
- [ ] Persist paper orders/fills under a separate account/mode namespace.

### Task 2.10: Worker runtime, scheduler, core observability and local API

**Planned files:** `worker/{runtime,leadership,scheduler}.py`, `api/app.py`, health routes.

- [ ] Start with reconciliation and catalogue barriers.
- [ ] Implement one active testnet writer per external account using lease plus leadership generation; graceful shutdown cancels tasks and relinquishes leadership. Production writes remain structurally disabled.
- [ ] Implement scheduler loops with monotonic timers, single-flight jobs, bounded jitter/backoff, persisted run/heartbeat state, graceful cancellation, restart catch-up policy and readiness failure for stale critical jobs.
- [ ] Persist/expose core counters needed before Phase 3 soak: rate budget, leader changes, uncertain operations, reconciliation discrepancies, critical job freshness and command backlog.
- [ ] Add `/health/live` and `/health/ready` with component-level safe reasons.
- [ ] Bind the Phase 2 API to loopback by default. Generate/store a high-entropy local operator token with restrictive file permissions, constant-time verification, rotation/revocation-on-restart support, request rate limiting and operator audit; disable CORS.
- [ ] Any non-loopback exposure already requires TLS/reverse-proxy allowlisting, replay protection and the Phase 5-grade auth controls; sensitive read endpoints are never anonymous.
- [ ] Add authenticated read-only order/position/balance/risk/command status endpoints. Mutations remain CLI/durable-queue controlled until Phase 5.

### Task 2.11: Phase 2 verification

- [ ] Run unit, contract, real-Postgres concurrency/fencing, database and manual-intent paper pipeline tests.
- [ ] Run private read-only testnet tests when keys exist.
- [ ] If explicitly enabled, place only tightly bounded testnet orders with automatic cleanup/reconciliation; never production.
- [ ] Exercise crash-after-submit and restart recovery with mocked/recorded exchange outcomes.
- [ ] Stop and report files changed, tests, verified vs assumed behavior and open questions; wait for approval.

**Phase 2 acceptance criteria:** the named private read/trading resources are typed/tested and all other/fund-movement paths are rejected; OMS never blind-retries; durable commands, fencing, kill latch, mandatory risk checks and atomic reservations precede every risk-increasing testnet write; verified protection guards every non-flat position; startup reconciliation blocks risk until ready; the manual-intent -> risk -> OMS -> simulated-fill PAPER pipeline is complete and incapable of real writes; Postgres proves safety concurrency; and testnet tests auto-skip without credentials. Strategy-driven PAPER acceptance is deferred to Phase 4.

## 7. Phase 3 — WebSocket market/private streams and resilience

### Task 3.1: Reverify WebSocket contracts

- [ ] Recheck current India and Global docs/changelog.
- [ ] Testnet-handshake all intended hosts and current `key-auth` behavior.
- [ ] Use the Global public/private hosts exactly as officially documented, but verify current host, private-auth (`auth` vs `key-auth`) and channel behavior on testnet; keep Global streams disabled if that evidence is incomplete.
- [ ] Capture sanitized current channel fixtures.

### Task 3.2: Public stream

**Planned files:** `exchange/delta/websocket.py`, `market_data/{service,state}.py`, tests with scripted fake server.

- [ ] Test ticker, L1/L2/update book, trades, mark, candles, funding, product updates and system status parsing.
- [ ] Test subscription acknowledgement, ref-counted resubscription and channel/symbol limits.
- [ ] Maintain last/mark/orderbook/funding freshness independently.
- [ ] Rebuild orderbook from snapshot+deltas with sequence validation where supplied.

### Task 3.3: Private stream

- [ ] Authenticate with fresh `key-auth` signature after each connect.
- [ ] Test initial snapshots and sequence handling for orders, positions, margins and `v2/user_trades`.
- [ ] Treat commission-incomplete trade events as provisional until REST fill reconciliation.
- [ ] Route observations through the OMS/repositories, never mutate projections ad hoc.

### Task 3.4: Connection supervision

- [ ] Test heartbeat enablement, 35-second heartbeat deadline, ping/pong deadline and 60-second inactivity behavior.
- [ ] Implement exponential reconnect backoff with full jitter and connection-attempt budget below Delta's 150/5-minute cap.
- [ ] Resubscribe after connect and force REST reconciliation before clearing the reconnect barrier.
- [ ] On sequence gap, parse error or stale private stream, fail readiness closed and resnapshot.
- [ ] Add graceful task cancellation and socket cleanup tests.

### Task 3.5: Deadman switch integration

- [ ] Verify heartbeat endpoints and current schema on testnet before implementation because examples use unversioned paths.
- [ ] Verify whether Deadman cancellation can preserve reduce-only protective orders. If it cannot, arm it only while flat or keep it disabled; never knowingly let a liveness feature strip protection from retained positions.
- [ ] Create/acknowledge a cancel-orders heartbeat only in testnet-LIVE until production activation is separately approved.
- [ ] Treat Deadman as defense in depth, not the application kill switch.
- [ ] Alert, latch risk and reconcile protection when heartbeat health/cancellation outcome cannot be proven.

### Task 3.6: Chaos/resilience verification

- [ ] Persist and expose soak counters before starting: reconnects, sequence gaps, public/private freshness, uncertain operations, reconciliation discrepancies, leader changes, critical scheduler gaps and REST rate-budget state.
- [ ] Bind each soak run to account, region/environment, credential/egress identity, build/config/schema versions and start/end timestamps; a critical gap invalidates the run.
- [ ] Fake-server tests for dropped frames, half-open socket, invalid JSON, duplicate/out-of-order events, auth expiry, reconnect storm and REST outage.
- [ ] Restart worker during partial fill and during `UNCERTAIN` submit.
- [ ] Verify no duplicate orders and exchange-truth recovery.
- [ ] Run a sustained testnet PAPER soak; if testnet LIVE is explicitly enabled, keep quantities minimal and reconcile cleanup.
- [ ] Stop and report files changed, tests, verified vs assumed behavior and open questions; wait for approval.

**Phase 3 acceptance criteria:** public/private streams reconnect safely, heartbeat/staleness/sequence gaps fail closed, all reconnects force REST reconciliation, soak evidence is durable/version-bound, and Global WebSocket support is either evidenced on the officially documented hosts or explicitly disabled.

## 8. Phase 4 — strategy port, backtester, funding and fees

### Task 4.1: Freeze HMT golden behavior

- [ ] Export broker-independent Strategy v2 and indicator input/output fixtures from the audited HMT source without importing HMT at runtime.
- [ ] Cover rule groups, accelerators, direction, ATR/stop/target multipliers, overrides and diagnostic trace.
- [ ] Record excluded India-only behaviors explicitly.

### Task 4.2: Python indicators and strategy engine

**Planned files:** `strategy/{base,engine,indicators}.py`, golden/property tests.

- [ ] Implement indicator math to match golden tolerances.
- [ ] Implement deterministic strategy evaluation and trace.
- [ ] Replace broker/stock vocabulary with exchange-neutral product/account concepts.
- [ ] Add optional UTC entry/risk windows; never encode venue closure.
- [ ] Version every strategy configuration and signal.

### Task 4.3: Port selected strategies

- [ ] Port only strategies approved after reviewing golden behavior and Pine inconsistencies.
- [ ] Replace Binance symbols with catalogue product references.
- [ ] Make weekend/weekday entry filters explicit strategy settings, defaulting to 24x7 eligibility.
- [ ] Require a defined stop/worst-case loss for risk sizing.

### Task 4.4: Backtester

- [ ] Build an event-driven engine using the same domain orders, strategy, sizing and risk components as PAPER/LIVE where possible.
- [ ] Model candle/orderbook fill assumptions explicitly and prevent look-ahead.
- [ ] Store run configuration, data provenance, trades, fills, PnL and metrics.
- [ ] Test gaps, partial fills, stop/trailing behavior, liquidation guard and deterministic replay.

### Task 4.5: Fees and funding

**Planned files:** `strategy/funding.py`, fee/funding models and fixtures.

- [ ] Reverify Delta fee/funding documentation for each supported region/product before encoding rates.
- [ ] Persist funding rate/payment events and include them in realised PnL.
- [ ] Model funding timing/sign, maker/taker fees and configurable slippage in backtest/PAPER.
- [ ] Fail closed or label results incomplete when rate history is missing.

### Task 4.6: Phase 4 verification

- [ ] Run golden parity, deterministic backtest and PAPER replay tests.
- [ ] Compare selected HMT strategy fixtures and explain intentional divergence.
- [ ] Produce sample backtest/PAPER reports with assumptions clearly displayed.
- [ ] Stop and report files changed, tests, verified vs assumed behavior and open questions; wait for approval.

**Phase 4 acceptance criteria:** broker-independent HMT strategy behavior is covered by golden tests, all India session/VIX/result-calendar assumptions are removed or explicit opt-ins, backtests are deterministic/no-lookahead, and funding/fees are modeled with sourced assumptions.

## 9. Phase 5 — dashboard/API, kill switch, deployment and go-live material

### Task 5.1: Secure control API

- [ ] Default to a same-origin, single-operator deployment behind TLS: Argon2-hashed operator credential, short-lived server-side sessions, `HttpOnly`/`Secure`/`SameSite=Strict` cookies, CSRF tokens, idle/absolute expiry, revocation and audit. If multi-user is approved, add explicit roles rather than weakening this boundary.
- [ ] Enforce an exact CORS/origin allowlist, trusted-proxy configuration, request/body rate limits and browser credential-storage policy.
- [ ] Finalize authenticated/authorized routes for products, quotes, orders, fills, positions, balances, PnL, funding, risk, reconciliation and audit views.
- [ ] Add authenticated SSE (or one-way WebSocket if measurements require it) for sequenced quote/order/position/reconciliation/health updates, with initial snapshot, heartbeat, last-event resume, bounded client backoff and authorization expiry. REST remains the command path.
- [ ] Add idempotent queued command endpoints for previewed entry, reduce/exit, protect, modify, cancel, reconcile and kill. Return durable command IDs/status; represent `UNCERTAIN` explicitly and never report in-memory dispatch as success.
- [ ] Add CSRF-safe operator actions and role separation if multi-user.
- [ ] Ensure secrets and unrestricted raw exchange payloads are never returned.
- [ ] Add OpenAPI, auth/session/CORS/CSRF, state-stream resume and command contract tests.

### Task 5.2: Extend the global kill switch with flattening and API/UI control

**Planned files:** `oms/kill_switch.py`, API route, CLI command, tests.

- [ ] Reuse the Phase 2 durable latch/cancel workflow; expose it only through authorized, audited API/UI actions.
- [ ] Stop strategies/new risk and drain or reject queued risk-increasing commands.
- [ ] Cancel all open orders, then reconcile.
- [ ] If explicitly requested, flatten positions with product-aware reduce-only actions or verified close-all semantics.
- [ ] If positions are retained, re-establish and verify reduce-only protection after broad cancellation. If flattening is requested, flatten first where needed to avoid a protection gap, then cancel leftovers.
- [ ] Reconcile again and report skipped halted/auction products, under-protected positions and unresolved exposure.
- [ ] Require deliberate operator confirmation for flatten; provide command and guarded endpoint.
- [ ] Test crash/restart at every kill step and idempotent resume.

### Task 5.3: React dashboard adaptation

**Planned files:** `ui/**`, copied selectively from HMT rather than wholesale.

- [ ] Preserve HMT visual language and generic layout/components.
- [ ] Create the UI package explicitly: Vite/React/TypeScript/Tailwind config, fresh lockfile, lint/typecheck/build/Vitest scripts and generated-or-contract-validated typed API client.
- [ ] Replace Supabase Edge imports/direct table reads with the typed API client and authenticated state stream.
- [ ] Add region/environment/mode banner and 24x7 UTC clock.
- [ ] Show symbol/product ID, last/mark, funding, leverage, liquidation, margin use, realised/unrealised PnL, stream/reconciliation health and external exposure.
- [ ] Add account/readiness/configuration screens without credential fields; account identity/fingerprint/version are display-only. Credential verification/flat-only rotation remains a server CLI/external secret workflow so keys never enter the browser.
- [ ] Add saved watchlists, catalogue, orders/fills/positions, performance/export, funding/PnL, notifications/preferences, indicator settings, strategy library/editor/import/export/dry-run/assignment/start-stop, risk/reconciliation and redacted audit screens.
- [ ] Add preview/confirm flows for entry, exit/reduce, protection, modify and cancel; show durable command progress and operator guidance for uncertain operations.
- [ ] If multi-user access is approved, add profile/settings/admin/user-access screens with real persisted controls; otherwise omit them rather than copying inert UI.
- [ ] Add a guarded, two-step kill-switch UI.
- [ ] Add frontend unit/component coverage for market store, formatting, indicators, API/state-stream clients, strategy editor and credential/redaction guards, plus Playwright E2E/accessibility tests.
- [ ] Run UI lint, typecheck, unit tests and production build in CI.
- [ ] Decide mobile behavior explicitly; do not inherit user-agent suppression silently.

### Task 5.4: Observability and alerts

- [ ] Export the core Phase 2/3 evidence as Prometheus metrics and add request latency/errors/weights, OMS/risk/kill/session/auth metrics.
- [ ] Add `/health/live` and `/health/ready` component detail safe for operators.
- [ ] Add pluggable webhook/Telegram/email alert adapters with delivery isolation and dedupe.
- [ ] Document log/audit retention, backup and recovery.

### Task 5.5: Deployment

**Planned files:** `Dockerfile`, `docker-compose.yml`, optional `deploy/systemd/*.service`, production env template.

- [ ] Use non-root, pinned, minimal images and health checks.
- [ ] Compose API, worker and Postgres; UI can be static/Vercel or served separately.
- [ ] Ensure exactly one active exchange worker per account through leadership fencing.
- [ ] Add graceful shutdown, migration job, restart policy and secrets-mount guidance.
- [ ] Add systemd units only if the target host uses systemd; do not add PM2 for Python unless specifically required.

### Task 5.6: Machine-enforced production qualification

- [ ] Persist a qualification record bound to Delta user/account, region/environment, credential version/fingerprint, proxy/egress identity, build/config/schema versions, product allowlist and risk-policy version.
- [ ] Accumulate at least 24 continuous hours of production **read-only** evidence: healthy REST/WS, current catalogue/quotes/private state, reconciliations, scheduler/leader health, rate budget, no unresolved operations and no unacknowledged external exposure.
- [ ] Invalidate qualification on critical gaps, credential/egress/build/config/schema/risk changes, leadership ambiguity, unresolved operations, unmanaged exposure or failed reconciliation. A restart may resume only from persisted continuous evidence whose identities still match.
- [ ] Check valid qualification in both risk and transport immediately before every future production risk-increasing order.
- [ ] Keep the production activation latch false. Implementing/passing qualification does not itself authorize an order; activation requires a separate reviewed operator action after Phase 5.

### Task 5.7: Final README and go-live checklist

- [ ] Document setup, config, database migration, CLI/API/UI, BACKTEST/PAPER/LIVE and troubleshooting.
- [ ] Document Delta key creation: Read Data and Trading only; IP whitelist/static egress; never enable transfer/withdraw capability if offered; HMT-Crypto implements no withdrawal/transfer endpoints.
- [ ] Document region/environment separation and key rotation while flat.
- [ ] Document the server-only credential provider/rotation CLI and state clearly that browser onboarding never receives keys.
- [ ] Add a safe go-live checklist: clean account reconciliation, no unmanaged exposure, clock/NTP, IP allowlist, fresh catalogue, risk values, kill drill, restart drill, WS/REST soak, alert test, backup restore, low-size testnet proof, production read-only soak, explicit human approval.
- [ ] Document the machine-enforced 24-hour production qualification and every invalidation condition.
- [ ] Document a separately approved staged activation beginning at minimum supported size/product allowlist; do not perform that activation in this task.

### Task 5.8: Final verification

- [ ] Run all unit, contract, integration, database, E2E, static and container smoke tests.
- [ ] Run security/dependency scans and resolve high/critical findings or document approved exceptions.
- [ ] Run a production-host **read-only** diagnostic; no production orders.
- [ ] Demonstrate PAPER end-to-end, testnet cleanup, kill-switch dry run and crash recovery.
- [ ] Verify production remains read-only and activation latch remains false; do not place a production order.
- [ ] Stop and report files changed, exact commands, verified vs assumed behavior and remaining go-live blockers.

**Phase 5 acceptance criteria:** authenticated dashboard/control API work end-to-end, kill switch is durable and crash-safe, deployment has one exchange-writer leader, observability/alerts are actionable, all documentation is complete, and production remains unable to trade until the checklist and explicit gates are satisfied.

## 10. Test strategy

| Layer | Purpose | Network |
|---|---|---|
| Unit | signer, conversions, state machine, risk, limiter, paginator, parsers | none |
| Contract | sanitized Delta envelopes/errors/schema drift via `respx`/fixtures | none |
| Database | SQLite PAPER behavior plus Postgres constraints, races, fencing, reservations, recovery and migrations | local SQLite and real test Postgres |
| Fake WebSocket | heartbeat, reconnect, sequence gaps, resubscribe | local only |
| Testnet integration | current public/private Delta behavior | testnet only; skipped without keys |
| End-to-end PAPER | strategy -> risk -> OMS -> simulated fill -> persistence/UI | public/testnet data or fixtures |
| UI E2E | operator workflows, accessibility, redaction, kill confirmation | local stack |

Production base URLs are allowed only for explicit read-only smoke diagnostics outside normal CI. The transport unconditionally rejects production write methods through Phase 4. Phase 5 adds a qualification/activation gate but leaves activation false; tests continue to reject all production writes.

Planned standard commands:

```bash
pytest -q
pytest -q -m 'not integration and not testnet'
pytest -q -m testnet              # skips unless exact testnet settings exist
ruff check .
ruff format --check .
mypy src
alembic upgrade head
```

Phase 5 adds UI and container commands to the README.

## 11. Phase handoff template

At the end of every phase, report:

1. **Outcome:** what is usable now and what remains disabled.
2. **Files changed:** every created/modified/deleted file; deletions require explicit note.
3. **How to verify:** exact commands and required environment.
4. **Results:** test counts, static checks, testnet/read-only observations.
5. **Verified:** behavior proven by tests, fixtures or current docs/testnet.
6. **Assumed:** any unverified venue/deployment/product behavior.
7. **Safety state:** execution mode, region/environment and confirmation that no production order was placed.
8. **Open questions/blockers:** decisions required before the next phase.
9. **Approval gate:** stop and wait for the user's explicit OK.

## 12. Decisions requested before Phase 1

The plan can start safely with defaults, but these choices should be confirmed before they become long-term interfaces:

1. Is Delta India the first supported operational region, with Global implemented/configured but kept read-only until WebSocket behavior is verified?
2. Is the initial tradeable scope limited to verified linear futures/perpetuals while options remain catalogue-visible but execution-disabled?
3. Is the default same-origin single-operator session model sufficient, or is multi-user access required in Phase 5 (in which case explicit roles and either a reviewed local identity model or Supabase Auth adapter will be added)?
4. Should mobile crypto trading be supported, or should Phase 5 remain desktop-first?
5. What production host is expected for Phase 5: one Linux VM/systemd, Docker Compose, or another platform?

Unless changed before approval, implementation will use the conservative defaults stated above: one Delta account per worker/deployment profile, India first, linear futures/perpetuals only, a loopback local-token boundary until the same-origin single-operator session is built, desktop-first UI, and Docker Compose plus optional systemd deployment.

**Next action:** review `AUDIT.md` and this plan. Phase 1 begins only after explicit approval.
