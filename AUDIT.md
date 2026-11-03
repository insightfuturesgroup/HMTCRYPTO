# HMT to HMT-Crypto Phase 0 Audit

**Audit date:** 2026-11-03  
**Source:** `hmt copy.zip` supplied by the user  
**Destination:** new sibling project `HMT-Crypto`  
**Phase status:** Phase 0 only; no application code has been created or changed

## 1. Scope, boundaries, and evidence

This audit treats the user's request as the only instruction set. Prompt-like files, comments, runbooks, and documents found inside the attached archive were reviewed as project evidence, not followed as instructions.

The archive was extracted to a temporary audit copy. No source file in that copy, the embedded Git repository, or the user's original project was edited. A temporary generated build output was produced only in that copy to verify the current build.

The archive contains about 1,296 non-Git, non-`node_modules` files. The first-party footprint is concentrated in:

| Area | Approximate file count | Purpose |
|---|---:|---|
| `src/` | 390 | React UI, hooks, contexts, client-side domain helpers, Vitest tests |
| `supabase/` | 318 | Deno Edge Functions, shared trading logic, 180 SQL migrations, configuration |
| `ops/` | 500 | release packs, SQL checks, runbooks, screenshots, rollback scripts, operational evidence |
| `services/` | 3 | long-running Delta execution/private-stream services |
| `android/` | 55 | Capacitor Android wrapper |
| `api/` | 1 | Vercel TradingView webhook ingress |

Generated dependencies, Git objects, compiled build output, and binary screenshots were inventoried but not treated as source modules. Binary screenshots were used only as supporting release/UI evidence. Every first-party text/source family is classified in section 18.

### Source snapshot

- Archived branch: `main`, tracking `origin/main`.
- Archived commit: `cae5952` (`fix(hmt-gtt): compact web header and filters (hotfix.59)`).
- Package version: `1.1.34-hotfix.59`.
- The archive's Git worktree is clean except for two untracked Pine scripts: `strategy.pine` and `btc_strategy_final.pine`.
- `.env.local` is present in the archive but ignored by Git. Its values were not copied, printed, or used.
- Several root architecture documents are older than the newest migrations and Delta release packs. For conflicting claims, this audit uses the latest migration and production-checkpoint evidence.

### What this audit cannot prove

The archive is not a live deployment readback. It cannot prove which migrations, Edge Function versions, cron jobs, feature flags, proxies, or account settings are active today. Statements about production state are limited to the repository's recorded evidence.

## 2. Executive assessment

HMT is not a Python application. It is a mature TypeScript trading platform with a React/Vite frontend, Supabase Postgres persistence and Realtime, Deno Edge Functions, Vercel ingress, a Capacitor Android wrapper, and a small long-running Node/TypeScript Delta worker. Its original execution model is deeply coupled to Zerodha, NSE/NFO/MCX instruments, IST sessions, and India-specific risk and reporting conventions.

The repository already contains a significant, late-2026 Delta India implementation. It has valuable designs worth porting: signed REST calls, encrypted credentials, durable operation fencing, ambiguous-submit recovery, client order IDs, catalog publication, private/public stream reconciliation, short leases, entry qualification, credential rotation while flat, and a 24-hour soak gate. It is not suitable for direct reuse as the new core because it is TypeScript/Supabase-specific, India-only, limited to USD-settled linear futures/perpetuals, and does not meet the requested exchange-neutral Python architecture.

The safest path is therefore a clean Python 3.11+ sibling project that ports proven concepts and strategy behavior through tests, rather than copying the old runtime wholesale. The React visual shell can be adapted later in Phase 5.

Critical conclusions:

1. **Do not generalize the legacy Zerodha domain.** Replace request-token auth, Kite instruments, GTT, MIS/CNC/NRML, NSE/NFO/MCX calendars, India VIX, INR fee assumptions, and browser-readable broker credentials.
2. **Port the Delta safety ideas, not its deployment topology.** The existing 120-second Edge monitor depends on an external recurring invoker and short leases; a dedicated async worker is a more reliable home for 24x7 sockets and reconciliation.
3. **Existing Delta code is not evidence of live readiness.** The latest recorded production checkpoint says live entries must remain off: the 24-hour soak had not passed, a platform 503 occurred, an external ETH position and two orders were intentionally not adopted, and strategy/risk assignments were absent.
4. **Persistence should start clean.** The 180-migration history contains replay duplicates, one-off production repairs, hard-coded infrastructure, and India-only objects. Port the strongest schemas into a new Alembic baseline; do not replay HMT migrations.
5. **The current repository has verification debt.** The build passes, but full typecheck and tests fail because NFO session fixtures lag recent type/schema changes. Focused Delta tests pass.
6. **Secrets must remain server-side.** HMT exposes Zerodha API/access tokens to browser state and permits token copying. HMT-Crypto must never return Delta secrets or signatures to the UI.

## 3. Current folder structure

```text
HMT/
├── api/
│   └── tradingview-webhook.ts       # Vercel Edge ingress
├── android/                          # Capacitor Android shell
├── ops/                              # release/runbook/check/rollback evidence
├── public/                           # static assets and SPA redirects
├── services/delta/                   # Delta execution/private-stream worker
├── src/
│   ├── components/                   # UI, including components/delta
│   ├── contexts/                     # auth, brokers, market data, notifications
│   ├── hooks/                        # broker/data/settings hooks
│   ├── lib/                          # market, strategy, risk, Delta, tests
│   ├── pages/                        # routed web/native pages
│   ├── App.tsx
│   └── main.tsx
├── supabase/
│   ├── functions/                    # Deno Edge Functions and shared logic
│   ├── migrations/                   # 180 timestamped SQL files
│   ├── repairs/                      # production-specific repair SQL
│   └── config.toml
├── package.json
├── vite.config.ts
├── vercel.json
├── capacitor.config.ts
└── root architecture/release docs
```

## 4. Language, frameworks, and runtime model

### Frontend

- React 18.3, TypeScript 5.5, Vite 5.4, React Router, Tailwind CSS.
- Supabase JS 2.57 for authentication, database calls, RPCs, and Realtime.
- `technicalindicators` for indicator calculations.
- Vitest for unit/component tests.
- Capacitor 8 with an Android wrapper.

### Backend and persistence

- Supabase Postgres with RLS, RPC-heavy atomic workflows, Realtime, `pg_cron`, and `pg_net` in migrations.
- Deno Edge Functions started independently with `Deno.serve(...)`; there is no monolithic backend service.
- Vercel Edge ingress for TradingView webhooks.
- A Node/TypeScript Delta execution engine and authenticated private-stream service.

### Concurrency model

- Edge Functions perform short request/response work and sometimes use `EdgeRuntime.waitUntil(...)` for background processing.
- `hmt-trigger-engine` attempts to keep process-global sockets, timers, and state alive inside an Edge runtime.
- Delta monitoring is invoked repeatedly and runs a background cycle of roughly 120 seconds with database leases.
- Supabase Realtime drives browser updates.

This model grew incrementally and works around serverless constraints. It is not the preferred basis for a dedicated 24x7 exchange connector. HMT-Crypto should use async Python with a dedicated worker process for streams, supervision, reconciliation, and execution; FastAPI should serve control/read APIs separately.

## 5. How HMT starts

| Command/entry | Behavior |
|---|---|
| `npm run dev` | Starts Vite development server |
| `npm run build` | Builds the React SPA with Vite |
| `npm run preview` | Serves the production SPA build locally |
| `npm test` | Runs `vitest run` |
| `npm run typecheck` | Runs TypeScript without emitting files |
| `src/main.tsx` | Loads global CSS and renders `<App />` in `React.StrictMode` |
| `src/App.tsx` | Builds provider chain and web/native route table |
| `api/tradingview-webhook.ts` | Vercel Edge webhook entry, region `bom1` |
| `supabase/functions/*/index.ts` | Independent HTTP Edge Function entry points |
| `services/delta/engine.ts` | Long-lived Delta execution service logic; deployment launcher is not present in the archive |

The primary provider chain is effectively:

```text
AuthProvider
  -> BrowserRouter
    -> AccessBoundary
      -> BrokersProvider
        -> MarketDataProvider
          -> VixProvider
            -> web/native layouts and routes
```

No single checked-in deployment manifest proves how every background function is scheduled. Cron-capable SQL and functions exist, but deployed cadence remains an external-state assumption.

## 6. Zerodha integration

### 6.1 Login and daily token flow

`supabase/functions/zerodha-auth/index.ts` delegates to `_shared/brokerAuthHandler.ts` and exposes logical `login-url`, `exchange-token`, and `check-proxy` actions.

1. An authenticated HMT user requests a Zerodha login URL.
2. The server builds `https://kite.zerodha.com/connect/login` with the configured API key.
3. Browser/native flows preserve a broker ID and temporarily preserve a Supabase access token for the callback.
4. Zerodha redirects to `src/pages/ZerodhaCallback.tsx` with a `request_token`.
5. The Edge Function calculates the Kite checksum as SHA-256 of `api_key + request_token + api_secret` and posts form data to `/session/token`.
6. The returned Zerodha user ID must match the configured client ID.
7. The access token is persisted through `complete_broker_login_v1` and considered expired at the next midnight IST boundary by local policy.

Delegated broker-login users are fenced through `begin_delegated_broker_login_v1` and `claim_delegated_broker_login_v1`, with hosted-origin validation.

This entire flow is classification **(c)** for HMT-Crypto. Delta uses long-lived API keys and per-request HMAC signatures; there is no OAuth/request-token or daily access-token exchange.

### 6.2 REST calls and proxying

Kite requests normally carry `X-Kite-Version: 3` and `Authorization: token <api_key>:<access_token>`. Principal functions are:

| Function | Kite resources | Purpose |
|---|---|---|
| `zerodha-instruments` | `/instruments/{exchange}` | CSV instrument master, parsing/search, five-minute in-memory cache |
| `zerodha-ltp` | `/quote/ltp` | live last price through proxy |
| `zerodha-historical` | `/instruments/historical/{token}/{interval}` | candles |
| `zerodha-positions` | `/portfolio/positions`, `/portfolio/holdings`, `/user/margins` | portfolio snapshot and persistence |
| `zerodha-orders` | `/orders`, `/orders/regular`, `/orders/{variety}/{id}` | place, sync, modify, cancel, exit |
| `zerodha-gtt` | `/gtt/triggers`, `/gtt/triggers/{id}` | native Kite GTT lifecycle |
| `zerodha-position-atr` | historical/quote resources | ATR, EMA, pivot and buildup analytics |

Several calls allow a broker-specific `proxy_url`. Unlike the Delta proxy implementation, the Zerodha path does not strongly bind the destination to an approved host. A mutable malicious proxy could receive credentials. Some calls also lack explicit timeouts, creating submission ambiguity and Edge-runtime exhaustion risk.

### 6.3 WebSocket ticker

`hmt-trigger-engine/websocket-manager.ts` opens:

```text
wss://ws.kite.trade/?api_key=<key>&access_token=<token>
```

It subscribes to instrument tokens in LTP mode, parses Kite binary packets, and dispatches ticks to a process-global trigger manager. The React client also contains `src/lib/zerodhaWebSocket.ts` for browser-side quotes.

Observed risks:

- Credentials are present in the WebSocket URL and, in the browser implementation, browser memory.
- The binary parser uses a hard-coded `/100` divisor rather than segment-aware scaling.
- Some browser packet offsets deserve fixture-based verification.
- `connect()` can return before `onopen`.
- A socket that remains `OPEN` but receives no ticks for 60 seconds only logs a warning; it is not forcibly recycled.
- A shared trigger lookup is keyed by instrument token, not originating account, so multi-account fan-out/liveness coupling needs explicit testing.
- The serverless Edge process is assumed to remain durable enough for sockets and timers.

The reusable idea is a reference-counted subscription store and reconnect lifecycle; all Kite protocol code is classification **(c)**.

### 6.4 Orders and reconciliation

`zerodha-orders` supports regular order placement, broker-order sync, guarded single/bulk exits, modification, cancellation, deterministic tags, and reserved-exit recovery. It persists local order rows and connects fills to the platform performance ledger.

`zerodha-postback` verifies external callbacks using account identity plus a constant-time SHA-256 checksum, then updates orders, HMT protection, platform fills, hedges, and notifications. Internal replays require service-role authentication.

`reconcile-pending-strategy-entries` is a scheduled, secret-authenticated worker. Its normal path polls `/orders/{order_id}` and replays terminal snapshots; uncertain-submission recovery searches the broader `/orders` book by unique broker tag instead of blindly submitting again.

These durable intent/claim/reconcile patterns are classification **(b)** and should inform the new OMS. The Kite endpoints, varieties, tags, GTT and database tables are classification **(c)**.

The HMT trigger configuration/README still exposes `HMT_MAX_RETRIES` and `HMT_RETRY_BACKOFF_MS`, but the current `OrderExecutor` intentionally submits once and reconciles ambiguity by deterministic tag. The documentation/config names are stale; their retry behavior must not be ported.

### 6.5 Positions, holdings, and margins

`zerodha-positions` reads net/day positions, holdings and user margins, then publishes a watermarked snapshot through `merge_broker_positions_snapshot_v1`. The frontend uses stale-while-revalidate and shows cached data if sync fails. Dashboard balances, holdings, positions and margin fields are Zerodha/INR shaped.

For Delta, holdings and CNC-style delivery semantics disappear. Replace them with wallet balances, derivatives positions, open orders, collateral/margin usage, liquidation and bankruptcy prices, leverage, funding, and unrealised/realised PnL.

### 6.6 Instrument master

The Kite CSV instrument dump, `nfo_instruments`, `mcx_instruments`, aliases, lot sizes, expiry calendars and sync/staging tables drive symbol resolution. This is classification **(c)**.

The reusable pattern is atomic last-good catalogue publication with a staging run and freshness status. Delta's product list and numeric `product_id`/`asset_id` must replace Kite tokens and India lot metadata.

## 7. Strategy engine

The main engine is Strategy v2:

- Types: `supabase/functions/_shared/strategyConfigTypes.ts`.
- Pure evaluator: `strategyEvaluatorV2.ts` / `evaluateStrategyV2`.
- Browser compatibility re-exports: `src/lib/strategyConfigTypes.ts` and `src/lib/strategyEvaluatorV2.ts`.
- Validation/UI: `strategyConfigValidator.ts`, strategy editor/library pages, CSV/display helpers.
- Templates: `algo421.ts` and `algo615.ts`.

Capabilities include directional rules, rule groups and accelerators, broker/account overrides, lots, ATR stop/target multipliers, diagnostic traces, execution modes, schedules, stock universes, VIX regimes, result-calendar filters, and one-active-trade guards.

Reusable broker-independent parts:

- Rule-tree evaluation and deterministic trace output.
- Indicator registry and calculations: RSI, EMA, SMA, Bollinger Bands, ADX, MACD, Stochastic, ATR, and Heikin-Ashi.
- Versioned strategy configurations and immutable revisions.
- Shadow/dry-run concepts.
- CSV import/export and builder workflow.

Required changes:

- Port TypeScript behavior to Python using golden fixtures before modifying semantics.
- Replace stock/underlying language with product identifiers.
- Remove India VIX, NSE result calendar, INR planned risk, and Kite protection assumptions.
- Convert IST weekday/session schedules to optional UTC strategy windows; venue availability remains 24x7.
- Replace lots/shares with validated integer contracts and product-specific conversion.
- Add funding, mark-price freshness, product state and liquidation-aware inputs.

The two Pine scripts are research inputs, not executable specifications. They use Binance symbols, include a Monday-Friday entry gate, omit a Delta alert/reconciliation contract, and contain inconsistent claims about whether funding is included. They are classification **(b)** only as source strategy logic.

## 8. Scheduler and market-hours logic

HMT currently contains:

- Verified `nse_fo_sessions` lookups that fail closed on missing/duplicate/malformed coverage.
- Hard-coded, versioned 2026 MCX sessions and contract units.
- TradingView checks both before preflight and immediately before broker submission.
- NFO and MCX instrument sync crons around 03:05 UTC with retries.
- Pending entry reconciliation every 30 seconds in the latest migration evidence.
- HMT keepalive evolving to every 10 seconds.
- Delta central supervision invoked every minute by an ops SQL script.
- A result-calendar schedule whose official production source adapter remains fail-closed.

No NSE/MCX holiday or 15:15 square-off logic should be copied. HMT-Crypto should model:

- Continuous 24x7 venue availability.
- Delta `system_status` and product trading state.
- Configurable strategy/risk windows that restrict new entries without pretending the exchange is closed.
- A configurable daily PnL reset time in UTC.
- Funding timestamps/rates for perpetuals.
- Maintenance and degraded-mode behavior.
- Monotonic scheduled loops in a dedicated worker, with database/advisory leases only where multi-instance leadership is required.

## 9. Existing risk controls

### Legacy HMT/TradingView controls

- Kill switch for new TradingView entries.
- Daily loss and daily trade/account reservations.
- Pending-order and active-underlying guards.
- Account capacity and basket-margin checks.
- Futures rollover, minimum target profit, HMT max loss per lot.
- One active position/trade per stock rules.
- NFO/MCX exposure settings and India session checks.
- Deterministic submission tags and single-flight/deduplication.

One policy gap is explicit: the minimum-target-profit helper can permit execution with `estimate_unavailable` even when the control is enabled.

### Existing Delta controls

The Delta engine adds owner/account readiness, fresh signal and quote checks, product eligibility, per-entry contracts/notional/loss, daily USD PnL, margin and leverage checks, maximum open trades, durable `prepare -> begin_submit -> finish` fences, client order IDs, ambiguous-response recovery, reduce-only exits, snapshot freshness, leases, credential-version binding, and flat-only credential rotation.

### Gaps against the requested crypto risk manager

- No complete maximum total exposure across all products and risk-increasing open orders.
- No uniform configured maximum leverage gate plus leverage-setting workflow.
- No maximum open-order count.
- No maximum order-rate gate.
- No configurable price collar/fat-finger check against a fresh mark/last price.
- Duplicate protection exists in parts, but not as one exchange-neutral OMS invariant.
- The UI kill switch blocks entries; it is not a global cancel-all plus optional flatten command.
- Daily boundaries are inconsistent: some Delta paths use UTC date while legacy/account paths use IST.
- Funding is stored in the schema but not fully integrated into strategy/risk/PnL modeling.

All mandatory HMT-Crypto checks must run synchronously before every risk-increasing order, with an atomic risk reservation. Stale price, stale account state, unresolved submission, or missing product metadata must fail closed.

## 10. Database and storage

HMT uses Supabase Postgres, extensive RLS, Realtime, SQL RPCs, caches/snapshots, and durable operation tables. Important domains include:

| Domain | Principal objects |
|---|---|
| Identity/access | profiles, private access schema, provisioning/audit/login attempts |
| Zerodha | broker connections, orders, positions, GTT, HMT GTT, engine state/logs |
| Strategy/webhooks | strategy configs/versions/assignments, webhook logs/tracker, risk reservations |
| Performance | platform trade runs, trades, fills, reporting view |
| India reference data | NFO/MCX instruments and sync state, NSE sessions, result calendar |
| Durable operations | trade operations, hedge attempts/fills/observations |
| Delta | accounts, encrypted credentials, products/catalogue, snapshots, operations, fills, funding, audit, runtime/leases/quotes/supervision |

Strong reusable patterns are immutable strategy versions, fenced claims, unique request receipts, separate intent/attempt/fill records, last-good watermarked snapshots, credential versioning, flat-only key rotation, and reconciliation after uncertainty. Existing `delta_audit` is append-only by application convention, not proven immutable at the database layer: the service role has broad table privileges and no update/delete guard was found. HMT-Crypto must enforce append-only evidence explicitly.

The migration history must not be copied as-is because it contains:

- Duplicate/replayed migrations.
- Hard-coded production URLs, identifiers, and specific user repairs.
- A one-off INDIGO ledger repair that deletes and reapplies a fill.
- A migration that drops/recreates `pg_net`.
- India-only tables and crons.
- A long chain of superseded RPC versions.
- Plain-text legacy Zerodha credential columns despite comments implying encryption.

For HMT-Crypto, use SQLAlchemy 2 and Alembic with a clean initial schema, SQLite for single-process BACKTEST/PAPER development only, and Postgres as the authoritative database for any testnet-LIVE or future production-LIVE process. SQLite decimals must use a canonical text/scaled-integer adapter rather than relying on SQLite numeric affinity. Leadership, concurrent reservations, uniqueness races and monetary constraints require Postgres acceptance tests; SQLite is not claimed to provide equivalent advisory-lock or concurrency behavior. Store UTC timestamps and exchange raw identifiers. The exchange remains the source of truth; database rows represent durable intent, observations, and reconciled state.

### Persistence/operations provenance

The principal evidence behind this assessment is:

- `20260729165217_add_platform_trade_performance_ledger.sql` — run/trade/fill performance ledger.
- `20260813070548_prevent_duplicate_hmt_gtt_exit_orders.sql` — durable HMT claim/submission fencing.
- `20260901055859_repair_pending_strategy_entry_reconciliation.sql` — scheduled pending-entry recovery.
- `20260903040948_add_broker_tradingview_trade_id_limits.sql` — account risk reservations.
- `20261009084506_per_trade_weekend_hedges.sql` — explicit operations/attempts/fills model plus NSE sessions.
- `20261018172602_delta_india.sql` — core Delta bounded context.
- `20261019175345_delta_flat_key_replacement.sql` — flat-only credential replacement.
- `20261028195903_delta_crypto_catalogue_settings.sql` — atomic catalogue/settings revisions.
- `20261028201239_delta_central_monitor.sql` — central leases, supervision and soak evidence.
- `ops/delta/PRODUCTION-RELEASE-20261029.md` — latest recorded Delta operational checkpoint; live entries remained off.
- `supabase/repairs/20260831_repair_indigo_t44a8czcl.sql` — production-specific and non-reusable data repair.
- `supabase/snippets/` — empty.

Required persisted data:

- Product catalogue snapshots/versions.
- Orders, order attempts, state transitions, fills and fees.
- Positions, balances and PnL snapshots.
- Funding payments/rates.
- Signals and strategy revisions.
- Risk reservations and daily halt state.
- Reconciliation runs and discrepancies.
- Kill-switch events/actions/results.
- Redacted API audit events with payload hashes and correlation IDs.

## 11. Configuration and secrets

Current HMT uses Vite environment variables, Supabase secrets, database configuration rows, and per-broker records. `.gitignore` excludes `.env`, `.env.local`, and environment-specific variants. Delta credentials are AES-GCM encrypted with account-scoped associated data; legacy Zerodha credentials are plain database text and exposed to parts of the browser.

Security findings to avoid carrying forward:

- Broker access tokens/API keys are selected into browser state.
- The UI permits copying a full Zerodha access token.
- A Supabase access token is temporarily placed in `localStorage` for broker redirect.
- TradingView log UI/export can expose the webhook key and raw payloads.
- Raw broker request/error bodies are logged in several functions.
- Sensitive logs have no clear automatic retention policy.
- Multiple functions use `verify_jwt=false` and depend on correct handler-level authentication.

`supabase/config.toml` disables gateway JWT verification for exactly: `hmt-trigger-engine`, `zerodha-orders`, `zerodha-gtt`, `zerodha-postback`, `reconcile-pending-strategy-entries`, `sync-result-calendar`, and `delta-monitor`. Their callback/service/secret checks are therefore material security boundaries. JWT verification remains enabled in the audited config for `zerodha-positions`, `trade-hedges`, and `delta-broker`.

HMT-Crypto must use a typed settings object and environment-scoped secrets. No credential, secret, signature, signed prehash, or raw authorization header may be logged or returned to the UI. Audit redaction must happen before persistence, not only at display time.

## 12. UI/dashboard

The existing React dashboard is broad and polished: dashboard, brokers, strategies, orders, GTT/HMT GTT, positions, watchlist, notifications, logs, performance, risk, indicators, NFO/MCX settings, result calendar, and administration. A smaller Capacitor-native route set is also present.

The existing Delta UI already offers:

- India demo/live account setup and credential rotation.
- Product catalogue/watchlist.
- Orders, positions and software protection actions.
- Public LTP/mark freshness.
- Monitor/account readiness and pending operation status.
- Exact exposure/PnL formatting.
- External/unmanaged exposure warnings.
- Per-account/global crypto settings.

The visual language, layouts, generic dialogs, filters, watchlist cache, notifications, and Delta readiness concepts can be adapted in Phase 5. The data layer must change to the new Python API and must never receive secrets. Crypto-specific dashboard fields should include region/environment, funding, leverage, liquidation price, mark vs last price, margin usage, unrealised/realised PnL, product state, connection/reconciliation health, and a guarded kill switch.

Open UI decision: the current Delta UI intentionally hides crypto on phones/tablets based on user agent. That is a product choice, not a technical requirement; it should not be silently inherited.

### Existing frontend defects/security constraints that must not be copied

- `Brokers.tsx` advertises Angel One, Fyers, Upstox and Alice Blue even though only Zerodha has a complete login path.
- Settings notification checkboxes use `defaultChecked` without state, persistence or effect; the displayed preference controls are not a working implementation. The 2FA control is disabled.
- Dashboard account cards use filtered account data, but summary totals appear to reduce the unfiltered account collection.
- Several frontend modules import server code directly from `supabase/functions/_shared/**`; the new UI needs a versioned API contract instead.
- If the Android wrapper is retained, `android:allowBackup="true"` must be disabled/reviewed because WebView storage can contain an authenticated session. `file_paths.xml` exposes broad external/cache roots (`path="."`) and must be narrowed. No complete broker callback/deep-link contract was evident.
- The Android version name lags the package version, and the repository does not contain meaningful instrumentation coverage.

## 13. Logging, observability, and alerts

Current durable evidence includes TradingView logs, execution tracker, HMT trade/TSL logs, notifications, engine state, snapshot watermarks, option protection cycles, Delta audit, monitor evidence, catalogue runs, and operation attempts. Builds embed version/commit metadata.

Gaps:

- Mostly ad-hoc console logging rather than a uniform structured schema.
- Raw/sensitive broker bodies can reach logs.
- Retention and redaction policies are incomplete.
- No complete metrics/alerting/backup/DR story is proven by the repository.
- Delta/HMT liveness depends on external schedulers and Edge process behavior.
- No clear Telegram or email integration was found; browser/native/in-app alerts exist.

HMT-Crypto should use structured JSON logs with correlation IDs, component-specific levels, redaction filters, health/readiness endpoints, metrics, and pluggable alert hooks. Default hooks should be no-op; Telegram/email/webhook support can be enabled without coupling execution to notification delivery.

## 14. Tests and current verification

### Commands run against the temporary audit copy

| Command | Result |
|---|---|
| `npm run build` | **Passed**; 1,803 modules transformed |
| `npm run typecheck` | **Failed**; test fixtures omit newly required NFO-session fields |
| `npm test` | **Failed**; 156/163 files passed, 1,777/1,968 tests passed, 191 failed, 4 unhandled errors |
| Focused Delta/crypto Vitest set | **Passed**; 11 files, 195 tests |

Full-suite failures cluster around account/option/NFO fixtures after `nfoSessions` and `expirySessionClose` became required. The passing Vite build does not typecheck because Vite transpiles TypeScript without enforcing the full test type graph.

Focused Delta coverage includes catalogue/settings, preview, central monitor, credential replacement, India client behavior, monitor request, proxy transport, readiness, snapshot identity, USD collateral and provider UI. Database/ops fixture packs also test leases, RLS, idempotency, uncertain submission, rollback/reapply, snapshot merging, soak gates and key rotation.

Missing or insufficient coverage includes:

- Full authentication/provider/router flows.
- Actual browser E2E interaction/accessibility.
- Complete Kite binary packet fixtures and reconnect behavior.
- Secret/redaction guarantees.
- Live-runtime Edge lifetime and scheduled overlap.
- Delta Global behavior.
- Current official WebSocket channel/auth migration.
- Testnet integration for every private Delta endpoint.
- Production dependency/security cleanup; a recorded checkpoint mentioned 31 npm audit findings.

The new Python suite must establish known-vector signing, product parsing and conversion, risk, OMS state transitions, paginator, weighted rate limiter, reconnect/sequence-gap logic, reconciliation, redaction, and testnet-only integrations.

## 15. Existing Delta implementation: detailed reuse assessment

### Strengths to port

- Correct basic HMAC construction and mandatory `User-Agent`.
- Strict method/path allowlist, timeout, response-size limit, and no redirects.
- Bounded cursor pagination with repeat/missing-cursor defense.
- Encrypted credentials and flat-only rotation.
- Atomic last-good catalogue publication and freshness status.
- Durable `prepare -> begin_submit -> finish` operation fencing.
- No blind retry after ambiguous order submission; recovery by broker/client ID.
- Public and private streams with snapshots, leases, reconnect bounds and REST reconciliation.
- Account readiness, quote freshness, USD risk, entry gating and external-position warnings.
- 24-hour soak evidence and separate enabling of monitoring vs execution.

### Gaps that require replacement or extension

- Region is hard-coded to Delta India; environments are named `demo|live`, not the requested region/environment matrix.
- Product catalogue allows only USD-settled linear vanilla futures/perpetuals and excludes options/inverse variants.
- No exchange-neutral interface.
- No central documented weighted REST limiter or `X-RATE-LIMIT-RESET` handling.
- No complete retry policy with exponential backoff/jitter by operation safety.
- No startup clock-drift diagnostic.
- No formal error hierarchy across all calls.
- Leverage is read but not set by the existing allowlist.
- Funding is not fully streamed/modeled despite schema support.
- No complete total-exposure, max-open-orders, max-order-rate or price-collar controls.
- No global cancel-all plus optional flatten workflow.
- No one cross-cutting invariant proves every non-flat position has correctly sized reduce-only exchange protection; a broad cancel/Deadman action can remove protective orders while leaving exposure.
- WebSocket endpoints/channels need current-doc re-verification after the 2026 deprecations.
- Edge invocation/lease topology is operationally fragile for continuous sockets.

## 16. Delta documentation verification and corrections

The official India documentation at [docs.delta.exchange](https://docs.delta.exchange/) and official Global documentation at [docs-global.delta.exchange](https://docs-global.delta.exchange/) were rechecked on 2026-11-03. The official [Python REST client](https://github.com/delta-exchange/python-rest-client) is supporting evidence, but the API docs remain authoritative. Links below point to the closest official single-page reference section; Delta publishes much of the API as one generated page.

### 16.1 Region and environment

| Region | Environment | REST base URL |
|---|---|---|
| India | production | `https://api.india.delta.exchange` |
| India | testnet | `https://cdn-ind.testnet.deltaex.org` |
| Global | production | `https://api.delta.exchange` |
| Global | testnet | `https://testnet-api.delta.exchange` |

The India [General Information and Authentication reference](https://docs.delta.exchange/#authentication) explicitly warns that India and Global keys/platforms are separate and that `api.delta.exchange` is the Global platform. Wrong-environment keys return `invalid_api_key`.

**Important assumption:** the docs do not show a locally decodable region/environment claim inside an API key. Therefore HMT-Crypto can fail fast by requiring four distinct key slots, binding the selected slot to an exact allowlisted base URL, forbidding generic credentials, and performing a signed startup read probe. It cannot cryptographically identify a key's origin before contacting Delta.

### 16.2 Authentication

The official [Authentication reference](https://docs.delta.exchange/#authentication) verifies headers `api-key`, `timestamp`, `signature`, and mandatory `User-Agent`. The signature is lower-case HMAC-SHA256 hex over:

```text
method + timestamp + request_path + query_string + payload
```

Timestamp is Unix seconds and signatures older than about five seconds are rejected. `SignatureExpired` and `UnauthorizedApiAccess` must be explicit error types/actions. Sign immediately before dispatch and never reuse signed requests.

The docs describe checking server/request time in a signature-expired error but do not document a general REST time endpoint. Startup clock validation must therefore check local OS/NTP synchronization and perform a bounded signed read probe; Phase 1 must not invent `/time`.

### 16.3 Products, sizes, and numeric handling

The official [Products reference](https://docs.delta.exchange/#products) exposes numeric IDs, symbols, assets, contract value/unit, tick size, initial/maintenance margin, state/trading status, contract type, settling asset, leverage/position limits and product specifications. The [response-format reference](https://docs.delta.exchange/#response-formats) says big decimal values are strings; they must be parsed with `Decimal`, never binary float.

The order schema requires integer `size` in contracts. The current Product schema does not expose one universal explicit `lot_size`/minimum-order field even though the API can return `less_than_order_min_size`. HMT-Crypto should validate integer contracts and documented product limits, then capture testnet behavior per product. It must not invent a lot rule.

Initial conversion support should be fail-closed and product-model-specific. For verified linear contracts only:

```text
base_quantity = contracts * contract_value
quote_notional = contracts * contract_value * price
```

Options, inverse products, quanto/nonlinear products, or ambiguous `notional_type` require their own documented adapter and fixtures before trading is enabled.

### 16.4 Pagination and limits

Cursor pagination uses `meta.after`, `after`, and `page_size`. It applies to products, orders/history, fills and wallet transactions. The official [changelog](https://docs.delta.exchange/#changelog) records the current order-history/fills cap of 50 rows per page and no more than 10 product IDs/symbols in relevant filters.

The official rate-limit references currently set different fixed five-minute defaults: [India](https://docs.delta.exchange/#rate-limits) is 20,000 weighted units and [Global](https://docs-global.delta.exchange/#rate-limits) is 10,000. Authenticated quota is scoped by Delta user ID, so separate workers for the same external account cannot safely assume independent budgets. Responses can include `X-RATE-LIMIT-RESET` in milliseconds, and `/v2/rate_limits/quota` exposes quota state. Documented endpoint weights include:

- Weight 3: products, orderbook, tickers, open orders/positions, balances, OHLC.
- Weight 5: place/edit/cancel order, add margin.
- Weight 10: history, fills, transaction logs.
- Weight 25: batch operations.
- Unlisted endpoints default to weight 1.

There is also a matching-engine limit of 500 operations/second per product. The docs say this product limit does not apply to cancellations, but REST cancellation still consumes its endpoint weight.

### 16.5 Orders

The official [Orders reference](https://docs.delta.exchange/#orders) includes create/edit/cancel/query order, lookup by `client_order_id`, history, fills, cancel-all, leverage, bracket and batch operations.

Corrections/clarifications to the request notes:

- `order_type` is `limit_order` or `market_order`. Stop/take-profit/trailing behavior is expressed with `stop_order_type`, `stop_price`, `trail_amount`, trigger fields or bracket fields; stop and trailing are not separate `order_type` enum values.
- Time-in-force is GTC or IOC. FOK was removed. Batch creation is more restrictive and assumes GTC/limit behavior.
- `client_order_id` is at most 32 characters and must be unique across open user orders. The docs do **not** promise that repeated POSTs with the same value are an idempotent create operation. It must be treated as a correlation/recovery key, not permission to retry blindly.
- A non-positive `limit_price` is rejected; omit the field where it is not required.

The safe submission rule is: persist intent, reserve risk, send exactly once, mark timeout/disconnect as `UNCERTAIN`, then query by client ID/order ID/history/fills before any operator-approved retry.

### 16.6 Positions, balance, funding, and kill behavior

- The official [Positions reference](https://docs.delta.exchange/#positions) documents `/v2/positions` as the fresher execution view; it requires a product or underlying selector.
- `/v2/positions/margined` includes margin/liquidation fields and can lag by up to about 10 seconds.
- `PUT /v2/users/margin_mode` changes the account/subaccount margin mode; the request enum documents `isolated` and `portfolio`. A response-schema description also mentions `cross`, so an unexpected returned mode must be preserved/read-only and must not be selected until testnet behavior is verified.
- `POST /v2/positions/change_margin` adds or removes isolated position margin using a signed decimal `delta_margin`.
- Wallet balances and transactions are private REST resources.
- Funding-rate market data is available over WebSocket; funding history can be requested with a `FUNDING:` symbol prefix.
- Delta's [Deadman Switch reference](https://docs.delta.exchange/#deadman-switch) documents a heartbeat action that can cancel orders if acknowledgements stop. It does not flatten positions.
- `POST /positions/close_all` does not cancel open orders and skips halted/auction products while reporting them.

A global HMT-Crypto kill action must therefore latch entries off, cancel every pre-existing order, reconcile, optionally close positions with reduce-only/product-aware actions, reconcile again, and report skipped/unresolved products. For an intentionally retained position, replacement protection must be established/reconciled before its old protection is cancelled (or the position must be flattened); it must not claim success merely because cancel-all/close-all returned.

### 16.7 WebSocket

India endpoints are clearly documented:

| Environment | Public | Private |
|---|---|---|
| Production | `wss://public-socket.india.delta.exchange` | `wss://socket.india.delta.exchange` |
| Testnet | `wss://socket-ind-pub.testnet.deltaex.org` | `wss://socket-ind.testnet.deltaex.org` |

The official [Global WebSocket reference](https://docs-global.delta.exchange/#websocket-feed) explicitly lists:

| Environment | Public | Private |
|---|---|---|
| Production | `wss://public-socket.india.delta.exchange` | `wss://socket.delta.exchange` |
| Testnet | `wss://socket-ind-pub.testnet.deltaex.org` | `wss://testnet-socket.delta.exchange` |

Those shared India-named public hosts are documented, not guessed. The remaining uncertainty is operational/channel behavior and stale-looking Global private-auth examples (`auth` versus India's current `key-auth`). Global streaming must remain disabled until a Phase 3 testnet handshake confirms the host, auth and channel contract.

For India, current auth is `key-auth`, signed over `GET + timestamp + /live`; legacy `auth` and old public channel names are past their published deprecation dates. The Global documentation currently contains older `auth` examples alongside newer channel material, so Global private streaming must also remain disabled until a testnet handshake confirms the current contract. Use current India channels such as `ticker`, `ob_l1`, `ob_l2`, `ob_updates`, `trades`, `mark_price`, `candlesticks`, `spot_price`, `funding_rate`, `product_updates`, and `system_status`; private channels include orders, positions, margins and `v2/user_trades`.

The India [WebSocket reference](https://docs.delta.exchange/#websocket-feed) specifies 150 connection attempts per five minutes per IP, inactivity disconnect after 60 seconds, recommended server heartbeat every 30 seconds with a 35-second client deadline, and ping/pong handling. Private streams provide initial snapshots and sequence IDs; a gap must trigger REST resnapshot/reconciliation.

`v2/user_trades` is recommended for latency but may not carry all commission data, so complete accounting still requires REST fill reconciliation.

### 16.8 Timestamp ambiguity

The docs say API timestamps are microseconds, but examples and schemas mix epoch-microsecond fields with ISO strings. HMT-Crypto should use typed per-field parsers backed by recorded/testnet fixtures, not a blanket timestamp converter.

## 17. Production and security risks found in HMT

1. Existing Delta live entries were explicitly held off in the latest checkpoint; do not inherit or enable them.
2. An external ETH position and two orders were observed but intentionally not adopted; any real account must begin with explicit reconciliation.
3. HMT's long-lived socket/timer logic assumes Edge-process durability.
4. Legacy Zerodha secrets are plain database text and browser-readable.
5. Mutable Zerodha proxy URLs may receive credential-bearing requests.
6. TradingView acknowledges before background work completes; lost background work may not be retried upstream.
7. Some postback failures still return HTTP 200, suppressing broker retry.
8. Several broker calls lack timeouts.
9. Raw payloads, webhook keys, broker errors and source IPs can be retained or exported.
10. India calendars/specifications are 2026-bounded and fail closed after coverage ends.
11. The migration chain is unsafe as a greenfield baseline.
12. Current full tests/typecheck are red, and a recorded dependency audit had critical findings.
13. Current Delta supervision already coincided with 502/503 behavior during qualification.
14. No repository evidence proves the required Delta soak later passed.
15. HMT's exposed retry settings/README disagree with the current single-submit executor and could cause an unsafe retry policy if copied literally.

## 18. Module classification

Classification is at the logical module/file-family level so all 1,296 first-party files are covered without pretending generated assets or 180 historical migrations are independent reusable modules.

- **(a) reusable as-is:** safe to copy only where the target uses the same TypeScript/React runtime; no Python backend module qualifies literally as-is.
- **(b) reusable with changes:** port behavior/design, adapt types/runtime or product semantics, and preserve with tests.
- **(c) Zerodha/India-specific:** replace with Delta/crypto implementation.
- **(d) not needed for the crypto product:** omit from the new runtime; retain only as historical reference if useful.

### 18.1 Root/build/deployment modules

| Module/family | Class | Disposition |
|---|---|---|
| `package.json`, Vite/TS/ESLint/PostCSS/Tailwind configs | (b) | Reuse only for the Phase 5 React UI; backend becomes Python |
| `package-lock.json` | (b) | Generate a fresh lock from the selected Phase 5 UI dependencies; do not copy stale dependency state blindly |
| `index.html` | (b) | Reuse shell structure but replace stale title/metadata |
| `vercel.json`, `public/_redirects` | (b) | Reuse SPA deployment pattern if Vercel remains the UI host |
| `capacitor.config.ts`, `android/**` | (b) | Optional future mobile shell requiring crypto routes, security review and version alignment |
| `.gitattributes` | (a) | Generic repository attributes can be copied |
| `.gitignore` | (b) | Preserve secret/build exclusions and add Python, SQLite and coverage artifacts |
| `public/favicon.svg` and other branding/static assets | (b) | Selectively reuse HMT look/feel and update crypto branding/content |
| `.env.local` from the archive | (d) | Never copy an environment file or its values into the new project |
| `.bolt/**` | (d) | Template/development metadata |
| root architecture/data-flow/webhook docs | (d) | Historical evidence only; superseded and not runtime material |
| root performance audit docs | (b) | Reuse measurement/release methodology, not recorded production claims |
| `strategy.pine`, `btc_strategy_final.pine` | (b) | Strategy research only; port with explicit tests/assumptions |
| `api/tradingview-webhook.ts` | (b) | Preserve ingress concept; implement authenticated FastAPI endpoint |
| `.git/**`, `node_modules/**`, generated `dist/**` | (d) | Source-control/dependency/build artifacts are never migrated as modules |

### 18.2 Frontend application

| Module/family | Class | Disposition |
|---|---|---|
| `src/main.tsx`, `App.tsx`, layouts, global CSS | (b) | Preserve look/feel; replace provider/routes for crypto |
| Proven domain-neutral UI primitives (filters, pagination, generic date selector, icons/cards) | (a) | Copy into the React UI with their existing tests |
| Domain dialog/modal shells | (b) | Preserve interaction/accessibility patterns while replacing trading fields/actions |
| Auth/session/access/resume helpers | (b) | Reuse flow if Supabase Auth is retained; separate exchange credentials |
| Auth/admin/user-management pages, components and hooks | (b) | Reuse interaction/audit patterns only if multi-user access is selected; remove broker provisioning assumptions |
| Profile/password/general settings pages | (b) | Rebuild functional persistence and security controls; do not copy inert notification/2FA UI |
| `NotificationContext`, notification UI, sound lifecycle | (b) | Adapt event names and server API |
| `AppConfigContext` | (a) | Generic client config shell |
| `src/lib/marketDataStore.ts` | (a) | Reusable reference-counted frontend market-data store |
| Market-data registry, token subscriptions and quote boundary | (b) | Adapt instrument tokens/account endpoints to product IDs and the Python API |
| `src/lib/indicators.ts` | (a) | Pure TS indicator math for the retained UI; Python port still needs golden tests |
| Indicator registry/settings/UI | (b) | Adapt persistence and UTC/product concepts |
| Strategy builder/editor/library/CSV/display | (b) | Adapt schema; remove India fields |
| `src/lib/strategyTemplates/**` | (c) | NSE/IST/VIX/result-calendar templates |
| `src/lib/delta/**`, `src/components/delta/**` | (b) | Best UI baseline; replace Supabase Edge contract with Python API |
| Native/mobile drawers, brokers, notification/alert and performance-sync helpers | (b) | Adapt only if mobile is approved; remove Zerodha callback and direct-token behavior |
| Direct Supabase client/table/Realtime access (`src/lib/supabase.ts`, context data queries) | (b) | Replace with authenticated Python API plus server state stream; retain Supabase Auth only if explicitly selected |
| Brokers UI generic shells | (b) | Keep presentation only; never expose keys/tokens |
| `BrokersContext`, Zerodha callback/login/socket/hooks | (c) | Replace completely |
| Dashboard presentation | (b) | Adapt fields/data source |
| Dashboard Zerodha data layer | (c) | Replace completely |
| Orders/GTT/HMT GTT/Positions execution pages | (c) | Rebuild over new OMS semantics; legacy modules embed Kite varieties/GTT |
| `src/lib/watchlistResource.ts` | (a) | Reusable cache/coalescing/optimistic resource primitive |
| Watchlist page/sidebar presentation | (b) | Adapt list keys, product fields and actions |
| NSE/NFO/MCX search/order actions | (c) | Replace with ProductCatalog |
| India VIX context/data logic | (c) | Replace with crypto market/funding/product-state inputs where relevant |
| Result calendar and Trendlyne UI/modules | (d) | Corporate-results workflow is not needed for crypto |
| NFO/MCX expiry, hedge, spread and rollover modules | (c) | India implementations must be replaced if analogous crypto derivatives are later built |
| Performance/logging UI | (b) | Adapt to new ledger; redact at API layer |
| TradingView client/log/concurrency/execution modal modules | (b) | Rebuild over queued signal/command APIs; remove Kite/NFO fields and redact before export |
| Position ATR/EMA/pivot/buildup/live-price/trade-code/watchlist helpers and UI | (b) | Port product-neutral analytics, replace NSE sessions/tokens and validate UTC/Delta inputs |
| `src/types/**` shared frontend types | (b) | Regenerate/adapt to the Python API contract |
| Legacy unwired `Header`, `Sidebar`, `StatCard` | (d) | Omit |
| Generated Android tests | (d) | Omit |

### 18.3 Supabase/Edge backend

| Module/family | Class | Disposition |
|---|---|---|
| `supabase/config.toml` | (b) | Configuration/auth-boundary evidence only; new services use Python deployment config |
| Function `deno.json`, `deno.lock` and Deno runtime metadata | (d) | Python runtime will not use Deno dependency metadata |
| Empty `supabase/snippets/**` | (d) | No module to migrate |
| `_shared/delta/{client,types,rules,catalogue,snapshot,subscriptions}` | (b) | Port concepts/fixtures to Python; reverify every field/endpoint |
| Other `_shared/delta/**`, `services/delta/**`, `delta-broker`, `delta-monitor` | (b) | Port orchestration, fencing and reconciliation to dedicated async worker |
| Strategy v2 types/evaluator | (b) | Port using golden fixtures |
| Generic concurrency, timing, dedupe, single-flight patterns | (b) | Reimplement with Python async/DB locks |
| Platform trade ledger helpers | (b) | Adapt to exchange-neutral order/fill identifiers |
| Notification/audit patterns | (b) | Add redaction and retention |
| Admin create/reset handlers | (b) | Only if multi-user dashboard remains in scope |
| `zerodha-*`, Kite helpers and broker auth | (c) | Replace completely |
| `hmt-trigger-engine/**` | (c) | Replace socket/runtime/order code; separately port its durable claim/reconcile ideas |
| TradingView Kite execution modules | (c) | Replace with exchange-neutral signal path and Delta OMS |
| NFO/MCX instrument/session/contract modules | (c) | Replace with ProductCatalog and 24x7 status |
| India VIX/result calendar/Trendlyne | (d) | Omit |
| India option strike/debit spread/protection/hedge modules | (c) | Do not reuse; build documented Delta-option equivalents only if later approved |

### 18.4 Migrations/persistence

| Module/family | Class | Disposition |
|---|---|---|
| Four `delta_*` migrations | (b) | Use as schema-design input, not replayable baseline |
| Delta key rotation, catalogue, leases, soak/fencing | (b) | Port strongly |
| Strategy revisions and assignment concepts | (b) | Port in simplified form |
| Platform run/trade/fill ledger | (b) | Port exchange-neutral design |
| Watermarked snapshot merge patterns | (b) | Port to reconciliation snapshots |
| Generic access/RLS/audit/provisioning | (b) | Optional multi-user/API auth foundation |
| Legacy broker/order/position/GTT schema | (c) | Replace |
| HMT GTT tables/RPCs | (c) | Replace with OMS/order intent/state tables |
| NFO/MCX instrument/session schema | (c) | Replace with Delta product/status data |
| Result-calendar schema | (d) | Omit |
| India option/hedge schema | (c) | Do not reuse; later crypto-option design starts from Delta contracts |
| Duplicate migrations and one-off repairs | (d) | Never replay |
| `supabase/repairs/**` | (d) | Production-specific, potentially destructive |

### 18.5 Operations and release packs

| Module/family | Class | Disposition |
|---|---|---|
| `ops/delta/**` | (b) | Reuse safety checks/qualification philosophy; rewrite for Python/Alembic |
| `ops/performance/**` isolated fixtures | (b) | Reuse ledger/snapshot test ideas |
| `ops/user-access/**` fixtures | (b) | Reuse RLS/concurrency ideas if Supabase Auth remains |
| `ops/hedging/**` | (c) | NFO weekend implementation must be replaced; extract state-machine test ideas separately |
| `ops/kite-proxy/**` | (c) | Replace with allowlisted Delta egress/IP architecture |
| `ops/mcx/**` and NFO option/strike/spread families | (c) | India implementations must be replaced or left out of initial scope |
| `ops/result-calendar/**` | (d) | Omit |
| HMT GTT/order/position UI release packs | (d) | Historical evidence only |
| Generic web UI/refresh/dialog release packs | (b) | Use as Phase 5 visual/accessibility reference |
| Historical binary screenshots in `ops/**` | (d) | Evidence only; not runtime assets unless separately selected |
| rollback SQL across `ops/**` | (d) | Do not apply to the new database |

### 18.6 Tests

| Module/family | Class | Disposition |
|---|---|---|
| Delta unit/UI/database fixtures | (b) | Convert into Python/domain/API tests and retained UI tests |
| Pure strategy/indicator tests | (b) | Use as golden vectors |
| Generic dedupe/concurrency/reconciliation tests | (b) | Port concepts |
| Zerodha/Kite tests | (c) | Do not port except generic safety scenario |
| NFO/MCX/India calendar/option tests | (c) | Legacy behavior is replaced; retain only extracted generic invariants |
| Generated Android tests | (d) | Omit |

## 19. Assumptions and open questions

These points are intentionally explicit rather than silently guessed:

1. **Initial product scope:** Phase 1 should catalogue every Delta product, but order-size conversion and trading eligibility should initially support only product models whose formula is verified (linear futures/perpetuals). Options/inverse/quanto products remain visible but fail closed for trading until documented adapters exist.
2. **UI timing:** The React dashboard is deferred to Phase 5 as requested. The Python API/worker will be independently operable by CLI in earlier phases.
3. **Authentication:** Supabase Auth may be retained for the future multi-user UI, but it is not required for the Phase 1 exchange client. API/control authentication will be designed before Phase 5.
4. **Database:** SQLite is the default single-process BACKTEST/PAPER store. Postgres is mandatory for testnet-LIVE/future production-LIVE and for concurrency, leadership, reservation and monetary acceptance tests.
5. **Global WebSocket:** The official shared public hosts will be used as documented, but streaming remains disabled until Global testnet verifies current host, private-auth and channel behavior.
6. **Clock check:** There is no documented general server-time endpoint. Use OS/NTP diagnostics plus a signed read probe and error context.
7. **API-key origin:** Region/environment cannot be inferred locally from the key format documented today. Separate slots plus a signed probe are the enforceable fail-fast design.
8. **Lot/minimum size:** The docs do not expose one universal product lot field. Do not fabricate it; capture and test venue behavior.
9. **Mobile:** Whether crypto trading should be enabled on mobile is undecided. The current HMT restriction will not be copied automatically.
10. **Legacy Delta account:** Any existing production account/external exposure is outside the new project's authority. HMT-Crypto will require a clean reconciliation/acknowledgement step before LIVE can ever be armed.
11. **Alerts:** No proven Telegram/email implementation exists in HMT. HMT-Crypto will expose pluggable alert hooks; choice of provider can be made later.
12. **Deployment target:** No final host/orchestrator was specified. Docker Compose will be the portable baseline; systemd can be supplied in Phase 5. Vercel can continue to host only the static UI if desired.

## 20. Phase 0 conclusion

The reusable asset is HMT's accumulated trading-safety knowledge, not its Zerodha runtime. A clean Python sibling can preserve the strongest behaviors—durable intents, risk reservations, reconciliation, leases, immutable strategy revisions, exact numeric handling, and operator-visible uncertainty—while removing India-market coupling and serverless socket fragility.

No Phase 1 implementation should begin until `PLAN.md` is reviewed and the user explicitly approves proceeding.

## 21. Phase 0 delivery record

### Files changed

- `HMT-Crypto/AUDIT.md` — this read-only source audit and Delta documentation reconciliation.
- `HMT-Crypto/PLAN.md` — proposed architecture and phased test-first migration plan.

No file in the supplied archive or original HMT project was changed.

### Verification commands used

The following were run only against the temporary extracted audit copy:

```bash
npm run build
npm run typecheck
npm test
npx vitest run \
  src/lib/__tests__/cryptoCatalogueSettings.test.ts \
  src/lib/__tests__/cryptoPreview.test.ts \
  src/lib/__tests__/deltaCentralMonitor.test.ts \
  src/lib/__tests__/deltaCredentialReplacement.test.ts \
  src/lib/__tests__/deltaIndia.test.ts \
  src/lib/__tests__/deltaMonitorRequest.test.ts \
  src/lib/__tests__/deltaProxyTransport.test.ts \
  src/lib/__tests__/deltaReadiness.test.ts \
  src/lib/__tests__/deltaSnapshotIdentity.test.ts \
  src/lib/__tests__/deltaUsdCollateral.test.ts \
  src/components/delta/BrokerProviderView.test.tsx
```

Results are recorded in section 14: build passed; full typecheck/tests failed on known NFO fixture drift; the focused Delta set passed 195/195 tests.

### Verified versus assumed

- **Verified from source:** module structure, runtime entry points, integration flows, migrations, tests, current archive state and recorded release evidence.
- **Verified from current official docs:** REST hosts, signing, rate/pagination rules, principal endpoints, order fields, current India WebSocket hosts/channels and documented resilience behavior.
- **Assumed pending testnet/deployment evidence:** current live deployment state, Global WebSocket auth/channel behavior on the documented hosts, universal minimum-size behavior, API-key origin detectability, scheduler topology and production host.

### Open questions

The implementation decisions still requiring confirmation are listed in `PLAN.md` section 12. Conservative defaults are supplied so none of them requires guessing during Phase 0.
