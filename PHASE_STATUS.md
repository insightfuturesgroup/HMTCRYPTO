# HMT-Crypto phase status

Status date: **2026-11-07**

This file distinguishes code that is verified offline from behavior that requires credentials,
testnet observations, PostgreSQL/Supabase, or deployment infrastructure. “Implemented” never means
“authorized for production.”

> Release-candidate note: the comprehensive offline campaign completed on 2026-11-07. It passed
> 11,566 backend tests with 22 credential/infrastructure skips and 50 UI tests, plus backend/frontend
> lint, type-check, and production-build gates. External Delta testnet, Supabase/PostgreSQL, Vercel,
> and operational qualification gates remain open, so this revision is not live-ready.

## Phase 0 — audit and architecture

**Delivered**

- `AUDIT.md`: full supplied-HMT archive audit, startup/runtime map, Zerodha and Delta findings,
  strategy/storage/UI/logging/test review, and per-module reuse classification.
- `PLAN.md`: exchange-neutral target architecture, phased implementation plan, safety invariants,
  assumptions, and evidence gates.

**Verified:** the source archive was treated as read-only; HMT-Crypto has no runtime dependency on
HMT. **Assumed:** the archive may not match an externally deployed HMT instance.

## Phase 1 — safe foundation and public REST

**Implemented and offline-verified**

- Typed settings with region/environment credential slots and fail-fast LIVE gates.
- Official host allowlists, exact signing, typed errors, bounded HTTP transport, retry policy,
  weighted limiter, cursor paginator, public products/tickers/orderbook/candles, immutable product
  catalogue, and exact contract sizing.
- Read-only CLI commands, structured redaction, package/build boundaries, and contract fixtures.

**External verification still required:** current read-only public testnet smoke and the selected
deployment host's clock/network behavior.

## Phase 2 — private REST, persistence, OMS, risk, reconciliation, PAPER

**Implemented and offline-verified**

- Strict private request/response models and positive route allowlist; fund-movement APIs are not
  represented.
- Delta India signed private-read runtime for balances, positions, orders, history, fills, and
  leverage observations behind sealed identity/clock/limiter admission.
- Mutation codecs/parsers and durable pre-dispatch evidence exist for offline contract testing;
  production venue dispatch is structurally disabled.
- SQLAlchemy/Alembic schema for scoped accounts, catalogue, commands, orders/attempts/transitions,
  fills, balances/positions/PnL/funding, protection, reconciliation, leadership, risk reservations,
  halts, qualification, and redacted append-only API audit evidence.
- Strict OMS/order transition kernels, deterministic client order IDs, mandatory risk evaluator,
  atomic evidence models, reconciliation evidence/query boundaries, sealed credential lifecycle,
  PAPER order/bracket/portfolio simulation, and durable global-kill admission.

**Not externally verified or activated:** signed reads against the user's account, exact account
binding, refreshable trusted-clock composition, PostgreSQL/Supabase race acceptance, testnet
mutations, and the exchange-writing worker. No production order path exists.

## Phase 3 — WebSockets and resilience

**Implemented and offline-verified**

- Exact endpoint policy, bounded frame codec, public channel parsers, subscription registry,
  connection-attempt governor, heartbeat/ping/inactivity policy, reconnection backoff, and
  reconciliation barrier contracts.
- India-testnet private-stream runtime with bounded auth/subscription/ping sends, periodic active
  authority revalidation, initial snapshot deadline, per-resource REST cutoffs, provisional fill
  handling, reset-only baseline publication, and fail-closed reconnect behavior.
- Failure tests cover malformed/oversized frames, sequence issues, backpressure, disconnects,
  cancellation, reconnect storms, and REST-reconciliation requirements.
- Private channel names/auth/signing/subscription contracts are represented.

**Activation blocker:** current private India testnet `key-auth` and account payloads must be
captured with the user's key before the private stream parser/runtime can be qualified. Global
streams remain disabled pending an equivalent Global testnet contract capture. Dead-man
cancellation remains disabled pending protection-preservation proof.

## Phase 4 — strategies, PAPER, and backtesting

**Implemented and offline-verified**

- Broker-independent HMT indicator and Strategy-v2 golden fixtures with explicit India-only
  exclusions.
- Deterministic strategy evaluator with UTC eligibility windows and diagnostic trace.
- Event-driven, exact-Decimal backtester; deterministic replay; latency/liquidity/slippage/fee
  models; order/stop/trailing/bracket behavior; portfolio accounting; and explicit funding events.
- Missing required funding evidence makes a report incomplete rather than assuming zero.

**Assumed:** historical datasets and product-specific fee/funding provenance will be supplied for
real research runs; no profitability claim is made.

## Phase 5 — control API, dashboard, kill control, deployment, documentation

**Implemented and offline-verified**

- Same-origin FastAPI control plane with Argon2id login, bounded server-side sessions, strict
  cookies, CSRF/origin checks, body/rate bounds, secure headers, authenticated health, metrics,
  OpenAPI, reconciled dashboard view, kill preview/admission, active-command lookup, and durable
  status.
- React/TypeScript/Vite dashboard with exact decimal display, 24×7 UTC/venue/mode status,
  reconciliation/risk/account views, positions/orders/funding/leverage/liquidation fields, and a
  guarded idempotent kill workflow with cross-tab coordination.
- SQLite PAPER bootstrap, PostgreSQL/Supabase migration policy, Docker multi-stage assets,
  Compose baseline, a credential-free durable PAPER worker, Nginx static proxy, systemd
  API/worker/migration units, Vercel static configuration, and operator documentation.

**Environment-dependent work:** deploy Supabase roles/migrations, configure the HTTPS API host and
Vercel same-origin route, install secrets, run container smoke, connect alerts, and execute the
testnet/read-only production qualification in `docs/GO_LIVE_CHECKLIST.md`.

**Important boundary:** the API queues a kill command; it cannot truthfully report exchange
cancellation/flattening complete until the separately testnet-qualified execution worker processes
and reconciles it.

## Verification commands

These commands are designed to make no real Delta write:

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

Credential/infrastructure tests skip unless their explicit opt-in variables are supplied. Never
point destructive PostgreSQL migration acceptance at a real Supabase project.

## Open questions for deployment

1. India only, or India plus Global after separate testnet qualification?
2. Which products and maximum risk limits are approved for the first PAPER/testnet profile?
3. Which Linux/container host will run the persistent Python API/worker with static egress?
4. Which alert channel and recipients should be used?
5. Which Vercel domain and same-origin API proxy will be used?
6. What backup/PITR retention and restore-time objective should Supabase enforce?
