Mandatory risk policy (P1-06)
===========================

Risk-increasing manual, TradingView and GTT submissions require an enabled policy.
Reduce-only exits are exempt from entry notional, exposure and daily-loss limits;
their position, direction, size and exit gates still apply.
The GTT runner supports protective exits only: a stored non-reduce-only GTT is
checked as an entry, then blocked with `gtt_entry_not_supported` if its policy is
otherwise valid. It must never be relabeled as a reduce-only exit.

The INR fields are maximum order notional, aggregate position notional, and daily
loss. All are required. Their stored USD equivalents are enforced only after
checking that INR / policy conversion rate equals the stored USD value at scale
18. Inconsistent policies fail closed. Claim reads lock the policy row. A policy
version change invalidates an earlier manual preview/submission.

`usd_inr_rate` is retained as a compatibility field. It is a policy conversion
rate, never an FX quote. Conversion is conservative only while the market INR/USD
rate is at or below the deployment constant. No market-rate fetch is performed.

Set the following environment variables consistently on API and private worker.
Values are deployment ceilings, never dashboard-editable. Defaults preserve the
previous approved scope, and do not open gates:

| Variable | Default |
| --- | --- |
| HMT_RISK_POLICY_CONVERSION_RATE | 100 |
| HMT_RISK_REFERENCE_RATE | 100 |
| HMT_RISK_MAX_ORDER_NOTIONAL | 50 USD |
| HMT_RISK_MAX_POSITION_NOTIONAL | 100 USD |
| HMT_RISK_MAX_DAILY_LOSS | 20 USD |
| HMT_RISK_MAX_ORDER_LIMIT_INR | 5000 INR |
| HMT_RISK_MAX_POSITION_LIMIT_INR | 10000 INR |
| HMT_RISK_MAX_DAILY_LOSS_LIMIT_INR | 2000 INR |
| HMT_RISK_MAX_OPEN_ORDERS | 5 |
| HMT_RISK_MAX_INFLIGHT_SIGNALS | 5 |

Startup warns if the deployment constant is below the configured reference rate.
The reference is supplied by the operator; it is not automatically refreshed.
Restart processes after changing deployment configuration. Deploy identical values
to every admission/dispatch process. Lowering ceilings may block existing policies.

`hmt_risk_policy_rejections_total{stage,reason}` distinguishes preview, admission,
receipt, signal_claim, submission_claim and save rejections. Errors identify missing
or disabled policies, malformed/non-positive values, exceeded ceilings, mismatched
conversion rates, inconsistent derived limits and changed policy versions.
API counters are available from the authenticated metrics endpoint. Worker
counters are process-local; structured `risk_policy_rejected` worker log events
carry the same stage/reason labels for collection. Worker counters are not
aggregated into the API process.

No migration is required. Roll back the application commit to restore prior code;
retain closed entry gates while investigating any policy rejection. Use the PG17
full-suite CI artifact before rollout. Verify exits with a disabled policy using
offline/staging qualification before live rollout.
