# HMT-Crypto

HMT-Crypto is a separate Python 3.11+ Delta Exchange trading system derived from the
broker-independent parts of HMT. It does not import, migrate, or modify the original HMT project.
The default is `PAPER` on Delta India testnet, all state is UTC, and production writes are disabled.

This repository contains the Phase 0 audit/plan and the offline implementation work for Phases
1–5: public market data, signed private-read boundaries, product sizing, persistence, OMS/risk
kernels, reconciliation evidence, resilient public streams, PAPER simulation, deterministic
backtesting, strategy parity fixtures, an authenticated control API, dashboard, durable kill-command
admission, migrations, and deployment assets.

## Safety and activation status

Do not treat a successful local test run as permission to trade. The following boundaries are
intentional:

- The current deliverable remains a PAPER/testnet release candidate. The comprehensive offline
  campaign completed on 2026-11-07: 11,566 backend tests passed with 22 credential/infrastructure
  skips, 50 UI tests passed, and backend/frontend lint, type-check, and production builds passed.
  This evidence does not qualify any external account, testnet mutation, or production deployment.

- No test or development command places a production order.
- `PRODUCTION_WRITES_FORBIDDEN=true` remains the required setting.
- Delta mutation routes have reviewed codecs, parsers, limiter/audit evidence and OMS/risk
  contracts, but venue dispatch remains offline until the testnet contract checks and account,
  clock, worker-leadership, reconciliation, and qualification authorities are issued.
- Delta India signed private reads are implemented behind sealed account/clock/limiter admission.
  Production composition is disabled until an authenticated account-binding probe and refreshable
  trusted-clock provider can be proven with the supplied account.
- Private WebSocket and Global WebSocket activation remain disabled until current testnet
  authentication/channel fixtures are captured. Public stream behavior is fixture-tested.
- The API queues a single durable global-kill command and records operator actions append-only.
  Venue cancellation/flattening remains unavailable until the testnet-qualified worker authority
  exists; the UI never reports queue admission as completed.
- Supabase, Vercel, alerts, and Delta private integration are not connected without credentials.

These are deployment gates, not hidden fallbacks. Missing or stale evidence fails closed.

## Architecture

```text
React dashboard -> FastAPI control API -> durable command/query repositories
                                              |
                                 single fenced async worker
                                              |
     ProductCatalog -> market data -> strategy -> risk -> OMS -> reconciliation
                                              |
                                Delta REST / WebSocket

Persistence: products, signals, commands, orders, attempts, transitions, fills,
positions, balances, PnL, funding, risk halts, reconciliation, and redacted audit evidence.
```

The API never signs or submits exchange orders. Exchange access belongs to one account-scoped
worker. SQLite is restricted to single-process BACKTEST/PAPER; multi-process or LIVE deployments
require PostgreSQL.

## Install

```bash
cd HMT-Crypto
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
```

Generate an Argon2id operator-password hash without storing the plaintext:

```bash
python -c 'from argon2 import PasswordHasher; print(PasswordHasher().hash(input()))'
```

Put the hash in `HMT_API_OPERATOR_PASSWORD_HASH`. Keep `.env` outside Git.

### Local SQLite PAPER setup

The SQLite initializer is explicit and refuses LIVE, PostgreSQL, or multi-process configuration:

```bash
hmt-crypto database init-dev
hmt-crypto account bootstrap --profile paper-primary
```

Copy the returned `account_id` into `.env`, then enable database-backed dashboard/commands:

```dotenv
HMT_API_DATABASE_CONTROL_ENABLED=true
HMT_API_ACCOUNT_ID=<returned UUID>
HMT_API_EMBED_PAPER_WORKER=true
```

`HMT_API_EMBED_PAPER_WORKER` is only for the single-process loopback SQLite preview. It lets the
browser exercise the durable PAPER kill workflow without Delta credentials. PostgreSQL deployments
must keep it `false` and run the separate `hmt-crypto-worker` service.

Run the API and dashboard in separate terminals:

```bash
hmt-crypto-api
```

```bash
cd ui
npm ci
npm run dev
```

The development defaults use `http://127.0.0.1:5173`. Production requires one HTTPS origin and
secure cookies.

The browser preview is deliberately truth-labelled: a new local account starts with no fabricated
positions, balances, or exchange reconciliation. Empty/unknown values are expected until public
data and a qualified testnet account are connected.

## CLI

All command output is one allowlisted JSON object; logs go to stderr with redaction.

```bash
hmt-crypto config-check
hmt-crypto clock-check
hmt-crypto database init-dev
hmt-crypto account bootstrap --profile paper-primary
hmt-crypto products sync
hmt-crypto products show --id 27
hmt-crypto products show --symbol BTCUSD
hmt-crypto ticker BTCUSD
hmt-crypto candles BTCUSD \
  --resolution 1h \
  --start 2026-11-02T00:00:00Z \
  --end 2026-11-03T00:00:00Z
```

The database commands do not create a Delta account or API key. LIVE account enrollment remains a
server-side, credential-bound operation and is not exposed to the browser.

## Region and environment isolation

| Region | Environment | REST host | Credential slot |
| --- | --- | --- | --- |
| India | production | `https://api.india.delta.exchange` | `DELTA_INDIA_PROD_*` |
| India | testnet | `https://cdn-ind.testnet.deltaex.org` | `DELTA_INDIA_TESTNET_*` |
| Global | production | `https://api.delta.exchange` | `DELTA_GLOBAL_PROD_*` |
| Global | testnet | `https://testnet-api.delta.exchange` | `DELTA_GLOBAL_TESTNET_*` |

Accounts, keys, product IDs, catalogue revisions, limiter state, and reconciliation evidence never
cross these boundaries. Generic `DELTA_API_KEY`/`DELTA_API_SECRET` and unknown Delta/database
settings are rejected.

Private REST authentication follows the official prehash exactly:

```text
method + timestamp + path + query_string + payload
```

The signer emits `api-key`, `timestamp`, `signature`, and `User-Agent` immediately before send.
Secrets, signatures, signed prehashes, and full keys are never persisted or logged.

## Execution modes

| Mode | Data | Fills | Exchange writes |
| --- | --- | --- | --- |
| `BACKTEST` | recorded deterministic events | simulator | never |
| `PAPER` | live public or recorded events | simulator | never |
| `LIVE` | live public/private evidence | venue | gated; production activation remains off |

LIVE-shaped configuration requires both:

```dotenv
DELTA_LIVE_TRADING_ENABLED=true
DELTA_LIVE_CONFIRMATION=I_UNDERSTAND_THIS_CAN_PLACE_REAL_ORDERS
```

Those variables are necessary but never sufficient. They do not bypass clock, identity,
leadership, reconciliation, risk, limiter, testnet-proof, or production-qualification gates.

## Risk and OMS

The pure risk evaluator covers position and total exposure, leverage, loss per trade, daily loss,
open-order count, order rate, stale/invalid price, fat-finger collar, duplicate intent, risk halt,
reconciliation, product support, protection, and reservation ownership. Every risk-increasing venue
operation must obtain durable, atomic evidence immediately before dispatch.

Order state is append-only evidence around a strict transition kernel. Create/modify/cancel is never
blindly retried. A possibly-sent operation becomes uncertain and must be resolved through client
order ID, exchange order ID, history, and fills.

The kill workflow is resumable and ordered:

1. persist the global risk halt;
2. cancel risk-increasing orders;
3. optionally flatten with reduce-only operations;
4. cancel remaining orders;
5. reconcile exchange truth;
6. re-establish/verify protection when positions are retained;
7. perform final reconciliation and report completed or incomplete.

The dashboard requires a short-lived, truth-labelled safety preview and an exact confirmation
phrase before flattening. Unknown exchange counts remain visibly unknown. Cross-tab reservations
and the server's active-command lookup prevent blind duplicate submission.

## Backtesting and PAPER simulation

The event-driven backtester uses exact `Decimal` arithmetic, bounded deterministic replay, the same
PAPER portfolio/fill models, explicit latency/liquidity/slippage/fee assumptions, reduce-only and
bracket semantics, and explicit funding realization events. Missing required funding makes a run
incomplete rather than silently assuming zero.

The strategy evaluator and indicators are frozen against broker-independent HMT golden fixtures.
India-only sessions, holidays, VIX, result calendars, NSE/NFO product types, and forced 15:15
square-off are rejected or disabled. Crypto eligibility defaults to 24×7 UTC.

## Control API and dashboard

The same-origin API provides:

- liveness and component readiness;
- Argon2id login, bounded server-side sessions, secure/strict cookies, CSRF and origin checks;
- reconciled dashboard projections;
- truth-labelled, idempotent, durable global-kill admission and status;
- append-only audit records for login/logout, dashboard reads, kill previews/admission, and command
  status reads;
- authenticated Prometheus metrics and OpenAPI output.

The React/TypeScript dashboard displays region/environment/mode, UTC time, catalogue/stream/
reconciliation/risk state, exact decimal balances/PnL/margin, positions, leverage/liquidation/
funding fields when evidenced, open orders, and the guarded kill flow. It never receives Delta or
database credentials.

## PostgreSQL and Supabase

PostgreSQL uses Alembic; application startup never runs migrations. Supply two distinct server-side
URLs:

```dotenv
DATABASE_URL=postgresql+asyncpg://<runtime-role>:<password>@<session-pooler-or-direct-host>/<db>
DATABASE_MIGRATION_URL=postgresql+asyncpg://<owner-role>:<password>@<direct-host>/<db>
DATABASE_SINGLE_PROCESS=false
```

For Supabase, use a direct owner connection for migrations and a direct/session-pooler connection
for the persistent Python process. Do not use the transaction pooler for worker locks or long-lived
sessions. The `hmt_crypto` schema is private; `anon`, `authenticated`, and `PUBLIC` receive no app
table access. Never put a service-role key, database password, or Delta secret in Vercel.

```bash
alembic upgrade head
alembic current
hmt-crypto account bootstrap --profile paper-primary
```

See `docs/SUPABASE_DATABASE_OPERATIONS.md` for roles, TLS, retention, disposable acceptance, backup,
restore, and downgrade policy.

## Minimal EC2 deployment

The approved initial production shape is one EC2 instance with an Elastic IP, GoDaddy DNS at
`crypto.helpmetrade.club`, Caddy-managed HTTPS, and a separate Supabase project. It intentionally
avoids CloudFront, NAT Gateway, a load balancer, Route 53, and Vercel while qualification remains
PAPER/testnet-only. See `docs/EC2_MINIMAL_DEPLOYMENT.md` and `docker-compose.ec2.yml`.

## Docker Compose and Vercel

Docker is optional. Keep Compose substitution values separate from the API process environment:

```bash
cp deploy/compose.env.example .env
cp deploy/api.env.example deploy/api.env
# Edit both files. deploy/api.env must remain mode 0600 and contain no Delta credentials.
docker compose build
docker compose run --rm migrate
docker compose run --rm api hmt-crypto account bootstrap --profile paper-primary
# Add HMT_API_ACCOUNT_ID and enable database control in deploy/api.env.
docker compose up -d
```

Compose binds the UI only to `127.0.0.1:8080`, runs migrations separately, waits for health, uses
read-only filesystems/capability drops, and includes a credential-free PAPER/BACKTEST worker for
durable simulated execution and kill controls. It does not include an exchange-writing worker
while testnet authority is unqualified. The split-container profile requires an HTTPS edge in
front of that loopback port: set `HMT_API_ALLOWED_ORIGIN` to the exact browser-visible HTTPS origin
and keep secure cookies enabled. Direct HTTP access to port 8080 is intentionally not an
authenticated operator profile.

The `ui/vercel.json` deployment is static and same-origin only. Before Vercel is usable, configure
an approved HTTPS reverse proxy/rewrite for `/api/v1/**` to the FastAPI service, preserving cookies,
CSRF, and `Idempotency-Key`, with caching disabled. See `ui/DEPLOYMENT.md`.

## Tests

Offline verification performs no real Delta write:

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

Credential- and infrastructure-dependent tests skip unless their explicit environment variables
are present. Public smoke tests use GET only. PostgreSQL migration acceptance requires a disposable,
acknowledged database. Never point destructive acceptance at a real Supabase project.

## Delta API keys to provide later

Create separate keys for every region/environment. Start with Delta India testnet:

- use Read Data for market-data-only qualification;
- use a separate key with Trading permission when qualifying private Orders, Positions, and Wallet
  reads—the current Delta India documentation says those account endpoints require Trading, so
  possession of that key must be treated as write-capable even while this application remains
  write-disabled;
- retain Trading for the later minimum-size testnet mutation proof only after its explicit opt-in;
- never enable withdrawal, transfer, or fund-movement permissions;
- enable IP whitelisting and provide the worker host's static egress IP;
- provide the Delta external user/account identity separately so binding can be verified;
- keep production and testnet keys in different secret files/slots.

Do not send secrets in screenshots, chat messages, Git, Supabase tables, Vercel variables, or browser
forms. Use the deployment secret manager or a root-owned `0600` secret file.

## Safe go-live checklist

Production stays read-only until all items are evidenced:

- exact Delta account, region, environment, key permission and IP binding;
- host NTP/clock monitoring and signed-read clock-expiry drill;
- fresh complete catalogue and supported-product allowlist;
- clean startup/periodic/reconnect reconciliation of balances, positions, orders and fills;
- risk limits reviewed in account currency and minimum-contract units;
- no unmanaged/external exposure and verified protective orders;
- public/private stream reconnect, sequence-gap and REST fallback soak;
- uncertain-order, crash/restart, leadership-loss and duplicate-worker drills;
- cancel-only and cancel-and-flatten testnet kill drills;
- Supabase migration, least-privilege roles, backup and restore proof;
- alerts and operator runbook tested;
- at least 24 continuous hours of bound production read-only qualification;
- explicit human approval for a separately reviewed minimum-size staged activation.

This project does not perform that activation.

The operational, credential, Supabase, Vercel, testnet, recovery, risk and staged-activation
checklist is maintained in `docs/GO_LIVE_CHECKLIST.md`.

## Reference documents

- `AUDIT.md` — original HMT audit and per-module reuse classification.
- `PLAN.md` — target architecture and phased migration plan.
- `PHASE_STATUS.md` — implemented, externally unverified, and activation-blocked work by phase.
- `docs/DELTA_CONTRACTS.md` — verified Delta hosts, auth, routes, limits, streams, fees and funding.
- `docs/GO_LIVE_CHECKLIST.md` — credential intake and safe deployment/activation gates.
- `docs/PHASE_REPORTS.md` — files, test commands, verified/assumed behavior, and open questions.
- `docs/SUPABASE_DATABASE_OPERATIONS.md` — hosted PostgreSQL operations and security policy.

Delta's official documentation is the source of truth. If current behavior differs from the frozen
contract evidence, stop, reverify the official page/testnet behavior, and update the contract tests
before changing execution code.
