# HMT-Crypto

HMT-Crypto is the new, separate Delta Exchange sibling of the original HMT project. Phase 1 is
a safety-first, read-only foundation: typed configuration, structured redaction, official public
REST market data, rate limiting, cursor pagination, an in-memory product catalogue, and exact
linear contract sizing. The original HMT project is not imported or modified.

> **Do not go live.** Phase 1 has no private REST client, order command, OMS, risk manager,
> reconciliation worker, database, or production qualification path. `order_write_capable` is
> always `false`, including when LIVE-shaped configuration is supplied.

## Requirements and installation

- Python 3.11 or newer
- A clock kept in sync by the host operating system
- No Delta API key for Phase 1 public reads

```bash
cd HMT-Crypto
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
hmt-crypto --help
```

The application defaults to `PAPER`, India, and testnet. Keep `.env` local; it is ignored by Git.

## Region and environment isolation

Delta India and Delta Global are separate platforms. Production and testnet are also separate;
accounts, API keys, product IDs, and catalogue state must never cross these boundaries.

| Region | Environment | REST base URL | Credential slot |
|---|---|---|---|
| India | production | `https://api.india.delta.exchange` | `DELTA_INDIA_PROD_*` |
| India | testnet | `https://cdn-ind.testnet.deltaex.org` | `DELTA_INDIA_TESTNET_*` |
| Global | production | `https://api.delta.exchange` | `DELTA_GLOBAL_PROD_*` |
| Global | testnet | `https://testnet-api.delta.exchange` | `DELTA_GLOBAL_TESTNET_*` |

Select with:

```dotenv
EXECUTION_MODE=PAPER
DELTA_REGION=india
DELTA_ENV=testnet
PRODUCTION_WRITES_FORBIDDEN=true
```

Each credential pair is optional in Phase 1. If supplied, both key and secret must be present in
the selected slot. Generic `DELTA_API_KEY` and `DELTA_API_SECRET` variables are rejected. Never
put exchange credentials in source control, logs, command arguments, Supabase, or Vercel.

An explicitly enabled development endpoint override is limited to non-LIVE testnet diagnostics.
A known Delta host must exactly match the selected region/environment or configuration fails; an
exact match resolves as the canonical endpoint, not as a development source. Genuine custom hosts
remain visibly marked as development endpoints and cannot publish a trusted product catalogue, so
`products sync` and `products show` fail closed before opening a client. This prevents custom or
cross-region data from acquiring canonical catalogue provenance.

## Read-only commands

Command results are one allowlisted JSON object. Help text is human-readable.

```bash
hmt-crypto config-check
hmt-crypto clock-check
hmt-crypto products sync
hmt-crypto products show --id 27
hmt-crypto products show --symbol BTCUSD
hmt-crypto ticker BTCUSD
hmt-crypto candles BTCUSD \
  --resolution 1h \
  --start 2026-11-02T00:00:00Z \
  --end 2026-11-03T00:00:00Z
```

- `config-check` is local and prints only selected mode/source/host, the credential slot, an
  irreversible API-key fingerprint, and `order_write_capable=false`.
- `clock-check` is local. It truthfully reports OS/NTP evidence as unavailable and the future
  signed private-read probe as `not_attempted`. Delta documents no server-time REST endpoint;
  this command does not claim otherwise.
- `products sync` fetches all cursor pages and atomically publishes a validated catalogue in the
  current process only. It does not persist data for a later CLI invocation.
- `products show` refreshes its own ephemeral catalogue before selecting exactly one ID or symbol.
- `ticker` and `candles` use only public GET endpoints. Candle times must be aware UTC values and
  whole seconds.

The current candle resolution allowlist is the conservative common subset verified against live
India documentation: `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `1d`, `1w`.
Cached Global documentation also lists `7d`, `30d`, and `2w`; these remain disabled until a live
Global contract check is possible. The 100-product page size is a local safety cap; the India
documentation specifies 100 as its default, not a venue maximum.

## Contract sizing boundary

Phase 1 supports exact conversion only for catalogue-verified vanilla linear perpetual futures
and futures. Options, inverse, quanto, spot, MOVE/unknown products, margin policy, leverage policy,
funding, fees, liquidation, and expiry policy fail closed or remain intentionally deferred. Order
sizes are contracts; Phase 2 risk and OMS code must still enforce position, exposure, loss, price,
duplicate, and kill-switch policy before any future order.

## Tests and quality gates

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/python -m build
```

Tests use sanitized fixtures. Low-level byte streaming, framing, cancellation, and lifecycle tests
use `httpx.MockTransport` because it provides deterministic transport control; a representative
`respx` route test preserves compatibility with the plan's named mocking tool.

Public testnet smoke checks are off by default and need no API key. They perform only GET requests
against both testnet hosts and save no raw response or personal data:

```bash
RUN_DELTA_PUBLIC_TESTNET_SMOKE=1 \
  .venv/bin/python -m pytest -q -m 'integration and testnet' \
  tests/integration/test_delta_public_testnet_smoke.py
```

An unavailable testnet is reported as not verified; fixture-based acceptance remains deterministic.

## Future API keys

When private reads/trading are implemented in later phases:

1. Create a separate key for each region and environment.
2. Grant Read Data only when possible; add Trading only when that deployment genuinely needs it.
3. Never grant withdrawal or fund-transfer capability if the Delta UI presents such a permission.
   HMT-Crypto categorically blocks fund-movement endpoints.
4. Delta requires IP whitelisting for Trading keys; whitelist only the deployment's static egress
   IPs.
5. Rotate keys after any suspected exposure and never log keys, secrets, signatures, or signed
   prehash material.

Supabase is the planned persistence/auth platform and Vercel is planned for later deployment work.
Both are intentionally deferred until account details are supplied. Neither should receive Delta
credentials at this stage.

## Safe go-live checklist (future phases only)

Do not enable real trading until every item is implemented and independently verified:

- private signed-read clock probe and region/environment key validation;
- OMS idempotency and post-timeout reconciliation;
- mandatory pre-order risk checks and persisted reservations;
- startup/periodic balance, position, fill, and open-order reconciliation;
- WebSocket reconnect/resubscribe plus REST recovery;
- global kill switch tested for cancel and optional flatten behavior;
- Postgres/Supabase migrations, backups, alerts, and audit retention;
- testnet soak, paper-trading comparison, operator runbook, and explicit production approval.

## Documentation verification

Official docs were rechecked on 2026-11-03:

- [Delta India API reference](https://docs.delta.exchange/) — live reference verified.
- [Delta Global API reference](https://docs-global.delta.exchange/) — the live root returned HTTP
  502 during recheck; official cached content confirmed the current host, public routes, signing,
  candle cap, pagination shape, and 10,000-unit five-minute quota. Treat live Global behavior as
  not reverified until the official site is available again.

No host, route, authentication, or rate-policy drift was found for Phase 1 India REST.
Authentication remains HMAC-SHA256 over
`method + timestamp + path + query_string + payload`, sent with `api-key`, `timestamp`,
`signature`, and `User-Agent`. Signed timestamps are Unix seconds with an approximately five-second
validity window. Phase 1 does not send signed or private requests.

The 2026-11-03 read-only testnet smoke published complete catalogues for both regions (766 India
products and 280 Global products at observation time). Current product responses omit the
reduce-only flag for some families and return no settling asset for Global spot products;
HMT-Crypto maps the former to an explicit conservative `true` gate and represents the latter as
not applicable, without inventing an asset. Ticker/candle smoke remains unverified because both
testnets returned a 16-digit ticker timestamp and a floating-point `close` while the current
official ticker example shows seconds and does not document a timestamp unit. The parser remains
fail-closed pending an authoritative contract rather than inferring units or converting a float.
