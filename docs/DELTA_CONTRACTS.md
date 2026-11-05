# Delta contract verification

This file records the external contracts used by HMT-Crypto. It is an engineering
evidence note, not an instruction to enable trading. Production writes remain disabled.

Last reviewed: **2026-11-06**

## Authoritative references

- Delta India API reference: <https://docs.delta.exchange/>
- Delta Global API reference: <https://docs-global.delta.exchange/>
- Delta India perpetual funding guide:
  <https://guides.delta.exchange/delta-exchange-india-user-guide/derivatives-guide/docs>

The India reference was reachable and reviewed live. The Global reference was reviewed
from the official site/search cache; Global streaming remains disabled until its current
hosts, authentication, channels, and payloads are proven against Global testnet.

## Hosts and account isolation

| Scope | REST | Public WebSocket | Private WebSocket |
| --- | --- | --- | --- |
| India production | `https://api.india.delta.exchange` | `wss://public-socket.india.delta.exchange` | `wss://socket.india.delta.exchange` |
| India testnet | `https://cdn-ind.testnet.deltaex.org` | `wss://socket-ind-pub.testnet.deltaex.org` | `wss://socket-ind.testnet.deltaex.org` |
| Global production | `https://api.delta.exchange` | officially documented but testnet proof required | `wss://socket.delta.exchange` |
| Global testnet | `https://testnet-api.delta.exchange` | officially documented but testnet proof required | `wss://testnet-socket.delta.exchange` |

India, Global, production, and testnet are separate security domains. Accounts, keys,
user IDs, product IDs, catalogue snapshots, limiter state, and reconciliation evidence
must never be reused across those boundaries. Delta documents a wrong-environment key as
an invalid-key condition; HMT-Crypto additionally rejects mismatched origins before I/O.

## REST authentication

Authenticated requests use HMAC-SHA256 over the exact bytes:

```text
method + timestamp + path + query_string + payload
```

The documented headers are `api-key`, `timestamp`, `signature`, and `User-Agent`.
The timestamp is Unix seconds and a signature is accepted only for approximately five
seconds, so the final timestamp and signature must be created immediately before send.
`SignatureExpired`, `UnauthorizedApiAccess`, invalid/wrong-environment keys, signature
mismatch, IP-whitelist failures, and CDN/`User-Agent` rejection are distinct failures.

HMT-Crypto never stores or logs the API secret, signature, signed prehash, raw auth
headers, or full key. A short irreversible key fingerprint is diagnostic metadata only.

## Rate limits

Delta India currently documents a default authenticated quota of **20,000 weighted units
per fixed five-minute window**, scoped by user ID. Delta Global currently documents
**10,000 units** for the same window. Therefore quota is a region-specific policy value,
not a universal constant.

The documented common weights are:

| Weight | Operations |
| ---: | --- |
| 3 | products, order book, tickers, open orders, open positions, balances, candles |
| 5 | create/edit/delete order, add/remove position margin |
| 10 | order history, fills, wallet transaction logs |
| 25 | batch-order APIs |

Unlisted endpoints default to weight 1 in the current documentation. A `429` response
includes `X-RATE-LIMIT-RESET` as milliseconds until the next request may be sent. Matching
engine operations also have a documented product-level ceiling of 500 operations/second.

The limiter is shared by every worker using the same external Delta user. Retrying a
mutation blindly is forbidden; an ambiguous create/modify/cancel must be reconciled by
client order ID, exchange order ID, order history, and fills.

## Pagination, numbers, and identifiers

- Collection pagination is cursor-based through `meta.after`/`meta.before` and `page_size`.
- Product and asset identifiers are numeric and scoped to region/environment.
- Contract order size is an integer number of contracts.
- Precision-sensitive numbers are strings; domain boundaries parse them as `Decimal`.
- Order `client_order_id` is limited to 32 characters and must be unique among a user's
  open orders.
- Order timestamps shown in the order API are Unix microseconds; other endpoints may use
  ISO-8601. Every parser therefore uses its endpoint-specific contract and never guesses.

## Orders and account operations

The reviewed order vocabulary includes market and limit orders, stop-loss and take-profit
triggers, `gtc` and `ioc` time-in-force, `post_only`, `reduce_only`, mark/last/spot trigger
methods, trailing amounts, and bracket stop/take-profit fields. Only fields admitted by a
reviewed route codec may reach a future signer.

Request margin mode is restricted to `isolated` or `portfolio`. Additional response values
may be preserved diagnostically but are not selectable without testnet evidence. Product
leverage and position-margin changes are product-scoped operations. Withdrawal, transfer,
deposit-address, and every other fund-movement route are permanently outside the allowlist.

Production mutations are not exercised by this project. Any future production activation
requires a separately reviewed, persisted qualification and operator action.

## WebSocket contract

India WebSocket authentication uses a `key-auth` message. Its signature prehash is:

```text
GET + timestamp + /live
```

Authentication must be freshly repeated after every connection. The documented failure
statuses distinguish incomplete payload, expired timestamp, missing key, invalid signature,
IP-whitelist rejection, and server error.

Current India public channels used by the design are `ticker`, `ob_l1`, `ob_l2`,
`ob_updates`, `trades`, `mark_price`, `spot_price`, `funding_rate`, `product_updates`,
`system_status`, and resolution-specific candlesticks. Current private channels are
`orders`, `positions`, `margins`, and `v2/user_trades`.

The private order stream sends an initial open/pending snapshot followed by incrementals;
sequence IDs are per symbol. Positions likewise send a snapshot and updates. The compact
`v2/user_trades` feed has per-symbol sequence IDs but intentionally omits commission, so a
streamed fill is provisional until REST reconciliation supplies authoritative fee data.

The current liveness guidance is:

- enable server heartbeat after connection;
- expect a heartbeat every 30 seconds and reconnect after a 35-second deadline;
- a client ping may use a five-second pong deadline;
- a connection with no activity for 60 seconds is disconnected;
- connection attempts are capped at 150 per IP in five minutes, with a documented 5–10
  minute pause after a handshake `429`.

Every reconnect must reauthenticate/resubscribe and then complete REST reconciliation
before readiness or new risk can be restored. A sequence gap, stale private stream, parse
failure, queue overflow, or ambiguous snapshot poisons readiness and forces a resnapshot.

## Fees and funding

Maker and taker commission rates are product fields. They must be captured with product/run
provenance; no global fee constant is authoritative.

For perpetuals, a positive funding rate means longs pay shorts, and a negative rate reverses
that direction. Delta India documents funding as a snapshot event: only positions present at
the realization snapshot pay or receive, regardless of holding time within the interval.
The payment is position value at the supplied index/mark input multiplied by the funding
rate. Intervals may vary by product, so the backtester consumes explicit funding realization
events rather than assuming every product uses one universal schedule. Missing required
funding history makes a result incomplete; it is never silently treated as zero.

Authoritative realized funding is reconciled from wallet transaction entries whose type is
`funding`. Stream and position aggregates are not added to wallet cashflows without proven
non-overlap.

## Explicitly unresolved or disabled

- Global WebSocket streaming is disabled pending a fresh Global testnet handshake and
  payload capture on the official hosts.
- Dead-man cancellation remains disabled until testnet proves its current schema and whether
  it can preserve verified reduce-only protection.
- Production order transport remains inactive.
- Options, inverse, quanto, MOVE, and unverified spot execution remain catalogue-visible but
  execution-disabled until their sizing, margin, risk, and settlement models are reviewed.
- Any discrepancy between this note and current official documentation requires a new review;
  the official documentation wins.
