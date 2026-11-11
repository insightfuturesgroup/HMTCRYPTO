# Delta India Live activation checklist

This is an evidence gate, not an instruction to trade. Deployment keeps global and per-account
entry gates disabled. A real acceptance order requires a separate, contemporaneous operator
approval after every preceding mandatory item is complete.

## 1. Release identity and infrastructure

- [ ] Record the reviewed Git commit, image digests, Alembic head, configuration digest, EC2 instance
      ID, Elastic IP, Supabase project reference, and AWS account.
- [ ] `crypto.helpmetrade.club` resolves only to the recorded Elastic IP and HTTPS is valid.
- [ ] Only Caddy publishes ports 80/443; database, API, worker, and Docker are not public.
- [ ] IMDSv2, Systems Manager access, encrypted EBS, snapshots, host clock synchronization, EC2
      recovery alarm, memory/disk alarms, and container restart alerts are verified.
- [ ] Delta confirms the recorded Elastic IP is allowlisted for the exact production API key.
- [ ] The EC2 instance role can read only this application's Delta secret paths and cannot list or
      read unrelated secrets.
- [ ] CloudFront and Vercel are not required for the minimal single-EC2 deployment.

## 2. Supabase and identity

- [ ] Migration owner and `hmt_crypto_app` runtime credentials are distinct.
- [ ] Runtime uses the supported session pooler with TLS; migration uses the direct owner endpoint
      with verified CA and host IPv6 where required.
- [ ] Alembic upgrades a disposable database from empty to the expected head and upgrades the target
      project once through the migration container.
- [ ] Runtime role cannot alter schema, bypass private ownership, or mutate append-only audit data.
- [ ] Supabase Auth public registration is disabled; invitation, password reset, disabled user,
      session expiry/revocation, Admin/Trader/Viewer boundaries, and account isolation pass.
- [ ] Backup/PITR is enabled and a restore into an isolated project is completed and timed.
- [ ] Browser configuration contains no database, service-role, Delta, or AWS secret.

## 3. Delta credentials and read qualification

- [ ] Production key has only the permissions required for trading and reads; withdrawal, transfer,
      deposit-address, and other fund-movement permissions are absent.
- [ ] API key and secret exist only in AWS Secrets Manager; PostgreSQL contains ARN/version,
      fingerprint, status, and timestamps only.
- [ ] Account onboarding validates the external account identity through the EC2 Elastic IP.
- [ ] Credential replacement invalidates prior reconciliation evidence and hands over worker lease
      authority without overlap.
- [ ] Current official Delta India REST/WebSocket contracts are checked on the qualification date.
- [ ] Public products/quotes and private balances, positions, orders, fills, funding, and fees agree
      with read-only REST truth.
- [ ] Invalid signature, expired signature, wrong environment, revoked key, and IP rejection generate
      distinct redacted evidence without leaking credentials.

## 4. Feed, reconciliation, and failure drills

- [ ] Public WebSocket disconnect/reconnect/resubscribe and stale quote rejection are demonstrated.
- [ ] Private stream loss forces fail-closed state and REST reconciliation before readiness returns.
- [ ] Worker restart, host restart, database interruption, lease expiry, and lease handover are
      exercised without concurrent signing authority.
- [ ] REST timeout and uncertain submission recovery prove no blind retry or duplicate order.
- [ ] Partial fill, exchange-side/foreign order, foreign position, cancellation, modification, and
      credential-version changes reconcile deterministically.
- [ ] Funding and fee totals are traceable to stored exchange evidence.
- [ ] A sustained healthy read-only qualification window is recorded with no unexplained readiness
      gap. Duration is chosen by the operator/risk owner; elapsed time alone is not acceptance.

## 5. Trading and risk controls

- [ ] First-live product allowlist, order types, minimum/maximum quantities, tick/lot rules, maximum
      order notional, projected position notional, daily loss, and open-order limits are approved.
- [ ] Preview expiry and quote/account/reconciliation freshness thresholds are approved and tested.
- [ ] Pending same-product orders are included in worst-case projected exposure.
- [ ] Manual order idempotency, TradingView replay/deduplication, and incompatible-symbol rejection
      pass without an exchange mutation.
- [ ] Cancel remains available when entries are disabled; modification cannot increase exposure.
- [ ] GTT/protected exits are reduce-only, survive restart, handle partial fills, and cannot create a
      reverse position.
- [ ] Global entry disable and per-account disable are exercised; disabled gates survive restart.
- [ ] Emergency procedure is rehearsed: disable entries, preserve/cancel protection as approved,
      reconcile, assess exposure, and record the incident.

## 6. TradingView and operations

- [ ] Webhook keys are displayed once, stored only as hashes, rotatable/revocable, and never logged.
- [ ] Strategy versions, account mapping, signal age, replay window, idempotency, symbol routing,
      preview freshness, and rejection reasons are verified.
- [ ] Operations contact, log/audit retention, alert channel, escalation owner, and response times are
      recorded.
- [ ] Alerts cover stale public/private data, missing lease, reconciliation failure, uncertain
      actions, credential errors, risk halt, container/host health, certificate expiry, and backup
      failure.
- [ ] Deployment, rollback, database restore, credential rotation, and emergency-disable runbooks are
      rehearsed by an operator other than the implementer where practical.

## 7. Controlled activation

- [ ] Independent review approves the exact release artifact and unresolved findings are accepted in
      writing by the risk owner.
- [ ] Global and account entry gates are confirmed disabled immediately before activation.
- [ ] Operator explicitly approves enabling only one account and the first restricted product.
- [ ] The approved account entry/exit gate is staged first; the global entry gate is enabled last.
- [ ] The operator separately authorizes one minimum-supported-size order from the normal UI.
- [ ] Order, fill, fee, position, and protection are reconciled; emergency disable is immediately
      available.
- [ ] Expansion to additional products, sizes, accounts, or TradingView automation receives a new
      explicit approval based on accumulated evidence.

If any required evidence is missing, stale, contradictory, or unexplained, the decision is
**NO-GO** and entry gates remain disabled.
