# HMT-Crypto phase reports

Status date: **2026-11-07**

These reports group files by responsibility. Git history and `git diff --name-status` remain the
exact machine-readable file lists. Environment-dependent checks are separated from offline
verification.

The comprehensive offline release campaign completed on 2026-11-07: 11,566 backend tests passed
with 22 credential/infrastructure skips, 50 UI tests passed, and the documented backend/frontend
lint, type-check, and production-build gates passed. Environment-dependent checks below remain
open and are not implied by that offline evidence.

## Phase 0 report

### Files

- `AUDIT.md`
- `PLAN.md`

### Verification

- Enumerated and read the supplied archive from a temporary extraction.
- Traced HMT startup, Zerodha login/token/REST/ticker/order/account flows, strategies, scheduling,
  risk, persistence, configuration, dashboard, logging, tests, and deployment evidence.
- Classified first-party modules by reuse disposition.
- Did not modify the archive or original HMT worktree.

### Verified, assumed, and open

- **Verified:** repository contents and recorded integration behavior.
- **Assumed:** externally deployed migrations/functions/config may differ from the archive.
- **Open:** final deployment host, Supabase project, Vercel project/domain, and alert provider.

## Phase 1 report

### Main files

- Root/config: `.env.example`, `.gitignore`, `pyproject.toml`, `README.md`
- Core: `src/hmt_crypto/{config,clock,errors,logging,cli,cli_runtime}.py`
- Exchange-neutral contracts: `src/hmt_crypto/exchange/{base,credentials}.py`
- Delta public integration: `src/hmt_crypto/exchange/delta/{endpoints,signer,transport,response,
  routes,client,paginator,rate_limiter,weighted_limiter,retry_policy,catalog,catalog_models,
  sizing,sizing_linear,sizing_math,sizing_models,sizing_validation,wire_json,wire_schemas}.py`
- Market data: `src/hmt_crypto/market_data/{contracts,orderbook,primitives,readiness,state,
  state_models}.py`
- Tests/fixtures: public REST, signing, pagination, catalogue, sizing, limiter, retry, and Delta
  fixture tests under `tests/unit`, `tests/contract`, and `tests/fixtures/delta`.

### Run

```bash
.venv/bin/python -m pytest -q tests/unit tests/contract
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/python -m build
```

### Verified, assumed, and open

- **Verified:** exact signing vectors, host isolation, bounded public REST, rate/retry policy,
  cursor pagination, catalogue publication, Decimal sizing, read-only CLI, and redaction.
- **Assumed pending network:** current public testnet reachability from the deployment host.
- **Open:** whether Global is required; Global streaming remains a separate qualification.

## Phase 2 report

### Main files

- Private contracts/runtime: `src/hmt_crypto/domain/private_*.py`,
  `src/hmt_crypto/exchange/private*.py`, and
  `src/hmt_crypto/exchange/delta/private_*.py`
- Persistence/migrations: `src/hmt_crypto/persistence/**`, `migrations/**`, `alembic.ini`
- OMS and PAPER: `src/hmt_crypto/oms/{models,state_machine,client_order_id,paper_*,kill_switch}.py`
- Risk: `src/hmt_crypto/risk/**`
- Leadership/clock: `src/hmt_crypto/worker/{leadership,clock}.py`
- Tests: private Delta, schema, repository, risk, reconciliation, leadership, PAPER, and
  PostgreSQL acceptance tests.

### Run

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_delta_private*.py \
  tests/contract/test_delta_private*.py \
  tests/unit/test_oms*.py \
  tests/unit/test_risk*.py \
  tests/unit/test_paper*.py \
  tests/integration
```

PostgreSQL acceptance tests require an explicitly acknowledged disposable database. Delta private
integration tests require an India-testnet key and skip otherwise.

### Verified, assumed, and open

- **Verified offline:** strict private models/allowlist, fund-movement exclusion, durable schema,
  state/risk kernels, reservations/evidence, credential isolation, PAPER simulation, and crash-safe
  workflow contracts.
- **Assumed pending supplied evidence:** account binding, signed-read behavior, IP allowlist,
  leverage/mutation wire behavior, and real PostgreSQL race/fencing behavior.
- **Open:** exact Delta external user ID, testnet key permissions, approved products/risk limits,
  Supabase roles/URLs, and a static worker egress IP.
- **Disabled:** production writes and unqualified testnet mutation dispatch.

## Phase 3 report

### Main files

- Delta streams: `src/hmt_crypto/exchange/delta/websocket_*.py`
- Private stream: `src/hmt_crypto/private_stream/**`
- Supervision/state: `src/hmt_crypto/market_data/{public_stream*,connection_attempts,
  reconnect_policy,stream_control_*,stream_liveness*}.py`
- Tests: WebSocket, market-stream, public parser, and public supervisor/failure tests.
- Contract record: `docs/DELTA_CONTRACTS.md`

### Run

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_websocket*.py \
  tests/unit/test_market_*stream*.py \
  tests/contract/test_delta_public_ws_parsers.py \
  tests/integration/test_public_stream*.py
```

### Verified, assumed, and open

- **Verified offline:** bounded codec/parsers, endpoint/channel policy, subscriptions, liveness,
  attempt budgets, reconnect/backoff, resubscription, active-authority revocation, bounded control
  sends, initial snapshots, and per-resource REST reconciliation barriers under focused fake-server
  failure tests.
- **Assumed pending testnet:** private auth payloads/account events and Global host/channel behavior.
- **Open:** sanitized private India snapshots/incrementals and whether Global support is desired.
- **Disabled:** Global streams, dead-man cancellation, and private readiness without REST
  reconciliation evidence.

## Phase 4 report

### Main files

- Strategy: `src/hmt_crypto/strategy/**`
- Backtest: `src/hmt_crypto/backtest/**`
- PAPER execution/accounting: `src/hmt_crypto/oms/paper_*.py`
- Fixtures/tests: strategy golden fixtures and indicator, strategy, backtest, and PAPER tests.

### Run

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_indicator*.py \
  tests/unit/test_strategy*.py \
  tests/unit/test_backtest*.py \
  tests/unit/test_paper*.py
```

### Verified, assumed, and open

- **Verified:** frozen HMT parity fixtures, deterministic exact-Decimal replay, no look-ahead event
  ordering, order/bracket/portfolio behavior, fees/slippage/latency assumptions, and funding events.
- **Assumed:** future historical datasets carry trustworthy product/fee/funding provenance.
- **Open:** approved production strategy configuration and data vendor; no profitability is assumed.

## Phase 5 report

### Main files

- API/control: `src/hmt_crypto/api/**`
- Observability: `src/hmt_crypto/observability/**`
- Durable control/reconciliation: command and checkpoint repositories/models, dev schema,
  `phase5_control_v1.py`, and `phase5_reconcile_checkpoint_v1.py`
- Kill workflow: `src/hmt_crypto/oms/{kill_switch,_kill_switch_types,
  _kill_switch_values}.py`
- UI: `ui/**`
- Deployment: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `deploy/**`
- Documentation: `README.md`, `PHASE_STATUS.md`, and `docs/**`
- Tests: API/session/database-control/kill/deployment tests and `ui/src/**/*.test.ts(x)`.

### Run

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/python -m build

cd ui
npm ci
npm run typecheck
npm run lint
npm test
npm run build
npm audit
```

When Docker is available:

```bash
docker compose config
docker compose build
```

### Verified, assumed, and open

- **Verified offline:** authenticated control API, database-backed conservative dashboard,
  durable/idempotent kill admission, exact-decimal UI, cross-tab guarded controls, migrations,
  static/type/unit/build gates, and production-write prohibition.
- **Assumed pending infrastructure:** container runtime, HTTPS reverse proxy, Supabase migrations and
  grants, Vercel same-origin routing, backup/restore, and alert delivery.
- **Open:** Supabase/Vercel/host details listed in `docs/GO_LIVE_CHECKLIST.md`.
- **Disabled:** claiming exchange-side kill completion without the qualified worker, and any
  production activation.

## Documentation sources

Delta endpoints, authentication, rate limits, private/public channels, payloads, and changelog were
rechecked against the official India documentation at <https://docs.delta.exchange/>. Global
contracts use <https://docs-global.delta.exchange/> and remain disabled where current testnet proof
is absent.
