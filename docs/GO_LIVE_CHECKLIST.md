# HMT-Crypto go-live checklist

This checklist is an activation gate, not an instruction to trade. A checked unit-test box does
not authorize a Delta request. Production writes remain disabled until a separately reviewed
activation change is approved by the operator.

## 1. Material to provide securely

Do not paste secrets into chat, tickets, screenshots, Git, Supabase rows, Vercel variables, or the
browser. Install them on the server through a secret manager or a root-owned `0600` credential
file.

### Delta India testnet first

- [ ] Delta India demo/testnet account is created and KYC/account prerequisites are complete.
- [ ] A market-data-only key has **Read Data** permission where a key is needed.
- [ ] A separate private-account testnet key has **Trading** permission. Current Delta India
      documentation says Orders, Positions, and Wallet endpoints require Trading even for reads;
      therefore this key is handled as write-capable while application write gates remain closed.
- [ ] Mutation opt-in is enabled only for the later bounded testnet-write qualification.
- [ ] Withdrawal, transfer, deposit-address, and other fund-movement permissions are absent.
- [ ] Testnet external user/account ID is provided separately from the key.
- [ ] API key and secret are installed in the India-testnet credential slot.
- [ ] Static worker egress IPv4 address is allowlisted at Delta, if the account supports it.
- [ ] A separate production read-only key is created only after testnet qualification.
- [ ] India, Global, testnet, and production keys remain separate; no key is copied between slots.

Global is optional. If Global is required, repeat the entire contract capture and qualification on
Global testnet before enabling its REST or WebSocket private paths.

### Supabase

- [ ] Supabase organization/project reference and chosen region are recorded.
- [ ] Exact direct database URL for the migration owner is supplied server-side.
- [ ] Exact direct or session-pooler URL for the runtime role is supplied server-side.
- [ ] Migration-owner and runtime-role credentials are distinct.
- [ ] TLS/CA requirements and network restrictions are recorded.
- [ ] Backup/PITR retention and a disposable restore target are selected.
- [ ] A disposable PostgreSQL database is available for destructive migration acceptance tests.

Supabase is PostgreSQL hosting for the Python services. The browser does not receive database
credentials, and Delta credentials are never stored in Supabase.

### Vercel and the API host

- [ ] Vercel team/account, project name, and production domain are supplied.
- [ ] The FastAPI backend has a stable HTTPS origin reachable only through an approved proxy.
- [ ] `/api/v1/**` is routed on the same browser origin to FastAPI with caching disabled.
- [ ] `Set-Cookie`, `X-CSRF-Token`, `Origin`, and `Idempotency-Key` are preserved.
- [ ] Delta and database secrets are absent from Vercel and all `VITE_*` values.
- [ ] The Python worker/API host is Linux/container capable, uses a non-root service account, and
      has persistent storage, NTP, a static egress address, and a supported secret store.
- [ ] TLS certificate/domain ownership and reverse-proxy trust boundaries are documented.

### Operations and alerts

- [ ] Alert provider is chosen: Telegram bot, email/webhook, or an existing incident system.
- [ ] Recipient/chat ID and server-side secret location are supplied.
- [ ] Alert deduplication, retry ceiling, and delivery-failure behavior are approved.
- [ ] Log retention, audit retention, backup retention, and operator contacts are approved.

## 2. Testnet identity and clock qualification

- [ ] Server NTP is enabled and monitored.
- [ ] Startup clock check is within the configured tolerance.
- [ ] A signed read proves the key belongs to the selected India-testnet account.
- [ ] Wrong-region and wrong-environment credentials fail before network I/O.
- [ ] `SignatureExpired`, invalid key, IP-whitelist rejection, and unauthorized access drills have
      distinct safe errors and alerts.
- [ ] The private-request trusted instant is refreshed immediately before every signing attempt.
- [ ] Credential rotation while flat invalidates old reconciliation and soak evidence.

## 3. Exchange contract qualification

- [ ] Current official Delta documentation and changelog are rechecked on the qualification date.
- [ ] Public and private testnet WebSocket handshakes are captured without secrets.
- [ ] Current `key-auth` success/failure envelopes are captured.
- [ ] Orders, positions, margins, and `v2/user_trades` snapshots/incrementals are captured and
      compared with REST truth.
- [ ] Sequence reset/gap behavior is proven; gaps poison readiness and force reconciliation.
- [ ] Product catalogue fields, page limits, filters, rate-limit weights, and reset headers match
      the frozen contract tests.
- [ ] Leverage request wire type and every intended mutation response are proven on testnet.
- [ ] Unsupported product models remain execution-disabled.
- [ ] Dead-man cancellation stays disabled unless testnet proves it cannot remove required
      reduce-only protection.

## 4. Database and recovery qualification

- [ ] Alembic upgrades a new disposable PostgreSQL database to the expected head.
- [ ] Least-privilege runtime and migration grants pass the acceptance suite.
- [ ] Append-only/audit mutation denial and account/environment isolation pass on PostgreSQL.
- [ ] Backup and restore are executed and timed.
- [ ] Startup reconciliation completes from exchange truth.
- [ ] Periodic and reconnect reconciliation complete without unmanaged exposure.
- [ ] Crash during submit, ambiguous response, partial fill, and restart are drilled without a
      duplicate order.
- [ ] A second worker cannot obtain write authority for the same account.
- [ ] Leadership/database-session loss stops signing and sending immediately.

## 5. Risk and emergency controls

- [ ] Product position limits, total exposure, leverage, loss-per-trade, daily loss, open-order
      limit, order rate, price collar, and duplicate window are approved in account units.
- [ ] Daily-loss reset time and valuation source are approved in UTC.
- [ ] Every non-flat position has reconciled reduce-only protection or new risk is halted.
- [ ] Price/catalogue/account/reconciliation staleness thresholds are approved.
- [ ] Cancel-only kill is drilled on testnet and reaches a reconciled terminal state.
- [ ] Cancel-and-flatten kill is drilled on testnet, including halted/skipped-product handling.
- [ ] Kill workflow is restarted at each durable step and resumes idempotently.
- [ ] UI queue admission is never mistaken for exchange completion.

## 6. Soak and staged activation

- [ ] PAPER runs continuously with live public prices and reconciled private read evidence.
- [ ] Public/private reconnect, REST fallback, rate limiting, and queue backpressure are soaked.
- [ ] At least 24 continuous hours of production **read-only** qualification are persisted and
      bound to account, region/environment, credential, egress, build, config, schema, product
      allowlist, and risk-policy versions.
- [ ] No critical gap, unresolved operation, unacknowledged exposure, stale stream, leadership
      ambiguity, or failed reconciliation occurred during the qualification window.
- [ ] Alerts, metrics, dashboard, backup, restart, and operator runbook are exercised.
- [ ] A fresh independent code/security review approves the exact release artifact.
- [ ] Operator explicitly approves a separate staged activation change.
- [ ] First live stage uses one allowlisted linear perpetual and the minimum supported size.
- [ ] Production kill remains immediately available and production activation can be revoked.

This repository deliberately does not check these environment-dependent boxes on behalf of the
operator.
