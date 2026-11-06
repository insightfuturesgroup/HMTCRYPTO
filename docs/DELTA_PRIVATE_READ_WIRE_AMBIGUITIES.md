# Delta India private-read wire ambiguities

Reviewed against <https://docs.delta.exchange/> on 2026-11-06.

- `GET /v2/wallet/transactions` declares `asset_symbol` as an integer even though the
  property is named as a symbol and Delta's other asset objects encode symbols as strings.
  The strict parser accepts only a string and production composition remains disabled until
  an India-testnet capture confirms the live encoding.
- Wallet transaction `meta_data` is documented only as a generic object and the example is
  empty. The parser admits missing, null, or exactly `{}`; any populated object fails closed
  until Delta publishes a typed schema or testnet evidence is reviewed.
- Settlement fill `order_id` is documented only as a "UUID string" without a canonical text
  spelling. The parser currently admits only the same lowercase compact UUID grammar that
  Delta explicitly demonstrates for fill IDs. Hyphenated or uppercase spellings fail closed
  pending India-testnet evidence.
- The fill metadata schema is type-dependent. Profiles implement the documented normal,
  liquidation, ADL, settlement, and OTC field sets and discard raw metadata after validation.
  New keys or shapes fail closed and require a docs/testnet profile revision.

No credentialed private-testnet qualification has been run. Production private-read
composition intentionally has no issuer until verified account binding, refreshable trusted
clock observations, a durable audit sink, and the central limiter lifecycle are all available.
