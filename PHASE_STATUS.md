# HMT Crypto implementation status

Status date: **2026-11-11**

This file separates implemented software from environment qualification and trading authorization.
An implemented order path is not permission to send a real order. Production entry gates remain
closed until the operator completes `docs/GO_LIVE_CHECKLIST.md` for the exact release artifact.

## Product scope

- Separate invitation-only application for Delta Exchange India futures/perpetuals.
- Supabase Auth and private Supabase PostgreSQL application data.
- FastAPI is the only application-data and administrative authority.
- One EC2 deployment with Elastic IP for all Delta private traffic.
- Admin, Trader, and Viewer roles with explicit per-account assignments.
- Live trading only. PAPER, spot, options, Global Delta, copy trading, and portfolio margin are out
  of scope.

## Completed implementation phases

### Foundation, identity, and administration

- Region/environment-pinned Delta clients, request signing, rate limiting, typed errors, immutable
  catalogue, exact contract sizing, WebSocket resilience, and fail-closed freshness checks.
- Supabase Auth session validation, invitation-only users, disabled-user and session-revocation
  enforcement, Admin/Trader/Viewer authorization, account assignments, and security audit records.
- Multi-account Delta onboarding with credential metadata in PostgreSQL and secret material isolated
  behind the AWS Secrets Manager boundary.
- Admin health, account live controls, emergency global entry disable, user management, account
  assignments, general settings, and current operational notifications.

### Market and private-account state

- Product catalogue and public quote worker with reconnect, resubscription, bounded persistence, and
  stale-feed detection.
- One supervised private runtime per enabled account, lease fencing, credential-version handover,
  balances, positions, open orders, fills, funding/fees, and REST reconciliation.
- Account state is unavailable unless reconciliation, credential version, and active worker lease
  agree. Foreign or uncertain state fails closed.

### Live order lifecycle

- Server-side preview and admission with exact tick/lot rules, quote freshness, account assignment,
  idempotency, risk policy, daily-loss, open-order, and projected-position checks.
- Durable lease-fenced submission queue, canonical signed Delta order requests, bounded one-shot
  dispatch, outcome evidence, and client-order-id recovery for uncertain submissions.
- Durable cancellation and shrink-only modification. Ambiguous actions are reconciled and never
  blindly retried.
- GTT-style protected exits with durable trigger state, reduce-only semantics, and controlled
  submission through the same risk and reconciliation boundaries.
- Manual trading, orders/fills, positions/balances, funding/fees, and performance UI.

### TradingView automation

- Versioned strategies and account mappings, one-time webhook keys stored as hashes, rotation and
  revocation, replay window, idempotent signal deduplication, symbol compatibility, risk admission,
  and rejection/execution logs.
- TradingView signals use the same durable live queue and do not bypass account, freshness, risk,
  or global entry gates.

## Verification evidence

- Each delivered checkpoint passed targeted backend lint/type/tests and the production UI build.
- Current UI campaign: type-check, lint, **50 tests**, and production build passed.
- Package/artifact build and targeted architecture/lifecycle tests passed.
- A sequential broader backend campaign reached **551 passing tests** before it was stopped because
  the complete suite is very large. This is not represented as a full-suite pass.
- No real Delta order has been placed by development validation.

## Remaining work is operational qualification, not feature development

The code phases are complete. Release remains **NO-GO for live entries** until all mandatory gates
in `docs/GO_LIVE_CHECKLIST.md` are evidenced for the exact commit, images, schema head, credentials,
Elastic IP, account, product allowlist, and risk configuration.

Required external evidence includes migration and least-privilege verification on Supabase,
Secrets Manager/IAM checks, current Delta read-contract captures, public/private feed recovery,
reconciliation stability, backup/restore, restart and lease-handover drills, alerting, rollback,
emergency-disable rehearsal, and an operator-controlled minimum-size acceptance order. The final
acceptance order is never executed automatically by tests or deployment.

## Safe verification commands

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
npm test -- --run
npm run build
npm audit
```

Credential/infrastructure tests require explicit opt-in. Do not point destructive migration
acceptance at the production Supabase project, and do not enable entry gates as part of a smoke
test.
