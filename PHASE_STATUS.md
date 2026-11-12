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

## Delivery-state matrix

The columns below are intentionally separate. A feature can exist in source without being fully
tested, deployed, production-verified, or qualified for Live use.

| Capability | Implemented | Tested | Deployed | Production verified | Live qualified |
| --- | --- | --- | --- | --- | --- |
| Identity, roles, invitations, and session controls | Yes | Targeted only | Yes | Partial | No |
| Delta account creation and assignment | Remediation in progress | Pending production-like acceptance | Previous version | No | No |
| Credential storage and replacement | Yes | Targeted only | Yes | No end-to-end evidence | No |
| Catalogue and public quotes | Yes | Targeted only | Yes | Partial | No |
| Private state and reconciliation | Yes | Targeted only | Yes | No sustained window | No |
| Manual order lifecycle | Yes | Targeted only | Yes, locked | Not exercised | No |
| GTT protected exits | Yes | Targeted only | Yes, locked | Not exercised | No |
| TradingView automation | Yes | Targeted only | Yes, locked | Not exercised | No |
| Shared UI shell, routing, caching, and performance | Remediation in progress | Pending full UI acceptance | No | No | No |
| Operational and emergency procedures | Documented | Drills pending | N/A | No | No |

The current release decision is **NO-GO**. Entry gates, Live dispatch, and production writes must
remain locked during remediation and qualification.

## Implemented software surface

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
- Current UI campaign: type-check, lint, **50 tests**, production build, and dependency audit passed.
- Modified backend surface: format/lint, mypy across **339 source files**, and **18 targeted tests**
  passed.
- The 2026-11-11 complete backend campaign finished with **11,659 passed, 22 skipped, and 15
  failed**. Three artifact failures were validation-environment setup errors and passed after the
  declared Hatchling backend was installed. **Twelve baseline architecture/schema contract failures
  remain unresolved**; the full campaign is therefore a fail, not a pass.
- Nginx cache policy was added, but deployed response headers remain unverified until the new UI
  image is built and inspected on EC2.
- No real Delta order has been placed by development validation.

## Remaining work

UI parity, workflow stabilization, full-suite verification, production acceptance, and the
machine-enforced six-hour operational qualification remain incomplete. Release remains **NO-GO for live entries** until all mandatory gates
in `docs/GO_LIVE_CHECKLIST.md` are evidenced for the exact commit, images, schema head, credentials,
Elastic IP, account, product allowlist, and risk configuration.

Required external evidence includes migration and least-privilege verification on Supabase,
Secrets Manager/IAM checks, current Delta read-contract captures, public/private feed recovery,
reconciliation stability, backup/restore, restart and lease-handover drills, alerting, rollback,
emergency-disable rehearsal, and an operator-controlled minimum-size acceptance order. The final
acceptance order is never executed automatically by tests or deployment.

## Safe verification commands

```bash
PYTHONPATH=.:src .venv/bin/python -m pytest -q
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
