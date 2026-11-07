# Delta India private WebSocket contract record

Verified against the official India documentation on **2026-11-06**:

- <https://docs.delta.exchange/#websocket-feed>
- <https://docs.delta.exchange/#authenticating>
- <https://docs.delta.exchange/#private-channels>
- <https://docs.delta.exchange/#detecting-connection-drops>

This record is deliberately narrower than the venue's full feature set. Global and production
private streaming remain disabled. The credential-gated integration placeholder never reconstructs
authorities from environment secrets and never connects; an eventual deployment qualification must
inject host-composed sealed authorities and an exact governed India-testnet connector.

## Implemented contract

- Exact India testnet private host: `wss://socket-ind.testnet.deltaex.org`.
- Current auth type: `key-auth`.
- Prehash: `GET` + current epoch seconds + `/live` with HMAC-SHA256.
- The normative auth schema says `timestamp` is a JSON number. The Python example first converts it
  to a string; the normative schema wins and the encoder emits a number.
- Auth outcomes are closed to `authenticated`, `incomplete_payload`, `request_expired`,
  `api_key_not_found`, `invalid_signature`, `ip_not_whitelisted`, and
  `internal_server_error`. Venue messages are validated but never retained.
- Credential/account proof and trusted-clock currentness are revalidated before each auth frame.
  Signing, sending, and receiving the auth response share one four-second deadline, below Delta's
  documented five-second expiry window. A venue `request_expired` response revokes both authorities.
- The only admitted subscription is the exact four-channel set `orders`, `positions`, `margins`,
  and `v2/user_trades` for one explicit symbol. The numeric product ID is derived from a published
  India-testnet catalogue snapshot; the revision and content hash accompany every delivery.
- Heartbeat is enabled on every connection. Missing heartbeat at 35 seconds and missing pong at
  five seconds invalidate the generation.
- Every reconnect creates a new generation, reauthenticates, resubscribes, and requires a
  generation-bound final REST reconciliation of balances, positions, open orders, and fills after
  both order and position stream snapshots before it can become ready. Old-generation output is
  drained before the next connection is opened.
- Deliveries carry account/user, India/testnet/mode, connection generation, local sequence,
  receive time, catalogue ID/revision/content hash, and sealed reconciliation digest provenance.
  The stream validates documented `user_id` fields plus every product ID/symbol pair.
- Positions and margins have no documented sequence and are always labelled provisional. Order
  snapshots are also baselines rather than incrementals. Only sequence-checked order and v2 trade
  incrementals receive the sequenced label; v2 fees remain provisional.
- Frame, row, pending, and output limits are respectively 64 KiB, 64 rows, 64 observations, and 64
  deliveries. Overflow invalidates the generation without partial batch publication.

## Fail-closed ambiguities

1. The general v2 history says private channels have snapshots and sequence numbers, but the
   current `positions` and `margins` examples contain no sequence. The runtime therefore makes no
   sequence-gap claim for those channels. Positions require a current-generation snapshot;
   margins remain advisory and REST remains authoritative.
2. The orders prose calls the field `seq_id`; both current wire samples use `seq_no` (including
   `meta.seq_no` for snapshots). Only the documented sample fields are accepted. Any different
   field invalidates the generation pending a sanitized testnet fixture and contract update.
3. `v2/user_trades` uses compact field `se`, described as `sequence_id`/`sequence_no`, and can reset
   to 1 after venue restarts. The first observed value establishes only a connection-local
   baseline. A later gap, duplicate, or reset invalidates the generation; REST fills
   reconciliation is required to recover truth.
4. `v2/user_trades` intentionally omits commission. Stream fills are marked fee-provisional and
   cannot finalize fee/PnL accounting until REST reconciliation supplies the fee.
5. Delta says subscriptions using symbol `all` receive no snapshots. HMT-Crypto rejects `all` and
   currently restricts each private stream to exactly one catalogue-bound symbol so snapshot
   readiness and reconciliation scope are meaningful.
6. The positions delete shape is not separately documented. The strict parser currently requires
   the same numeric fields as the published update sample. A different testnet delete payload will
   stop the generation rather than infer absent values.
7. The order snapshot row omits `symbol` and several incremental fields. Rows are bound only to the
   snapshot's outer symbol; fields absent from the sample are represented as unknown, not guessed.

## Qualification still required

- Capture sanitized India testnet auth success, empty/non-empty position snapshots, empty/non-empty
  order snapshots, create/update/delete events, margins, v2 fills, sequence gaps, and venue restart
  behavior.
- Compare every capture with these strict parsers. Update fixtures and this record before widening
  a schema.
- Add a deployment composition root that injects the existing sealed credential/account-proof,
  trusted-clock, published catalogue, exact physical connector/attempt governor, and private REST
  client. No raw-secret private-WebSocket issuer exists in the production namespace.
- Run a reconnect soak with a static whitelisted egress IP and confirm REST order/position/balance/
  fill reconciliation after every generation.
- Capture a real testnet subscription acknowledgement for the exact four-channel plan. The current
  parser requires an exact acknowledgement and deliberately rejects any undocumented shape.
