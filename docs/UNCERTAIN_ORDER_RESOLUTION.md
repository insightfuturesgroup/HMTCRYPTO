# Uncertain order: preserve evidence, never resend blindly

An UNCERTAIN outcome does not establish whether Delta executed the order. Entries
remain blocked by durable recovery debt, including hidden rows; reduce-only exits,
cancel-only and halt behavior are unchanged. Do not delete database records or
manually mark ACCEPTED, resend the order, or infer execution from a net position alone.

1. Halt new entries for the affected account; retain the original intent/submission,
   client order ID, product, side, size and timestamps. Do not include credentials
   or webhook keys in incident records.
2. Inspect Delta order history, fills and current positions for that exact identity.
   A missing row in open orders is not proof of absence: market fills disappear
   from that list. Missing order ID, size or fills evidence cannot prove execution.
3. Allow automatic read-only recovery to reconcile the authoritative evidence. If
   a fill is found, verify the portal agrees, including protection status. Do not
   submit a replacement order to clear a display problem.
4. If evidence remains incomplete, leave UNCERTAIN and entries blocked, escalate
   with the retained identity/time window, and obtain complete exchange evidence.
   The existing admin resolution endpoint is not an override for missing evidence.
   Only use it after confirming non-execution; it also requires fresh reconciliation
   and checks exchange evidence. Capture its audit event and the evidence used.
5. Preserve all audit/submission/recovery records. Hiding a row is presentation,
   not removal of recovery debt. Reopen entries only through reviewed gates after
   reconciliation, protection and incident review pass.

Worker deployment thresholds: `HMT_RECOVERY_WARNING_SECONDS=15` and
`HMT_RECOVERY_CRITICAL_SECONDS=60`; critical must exceed warning. The API must
use the same values so portal severity agrees. Missing send time is critical.
`live_recovery_uncertainty_aged` is emitted when recovery checks exceed a threshold;
`hmt_live_recovery_aged_checks_total{severity="warning|critical"}` counts checks,
not unique orders. Route these worker logs to the operator's alerting system;
this commit does not configure an external notification destination. Alerts run
when recovery runs, not an independent timer, so detection includes cycle delay.
Orders expose age, severity and missing/ambiguous-evidence reason without another
database query. A generic reason does not prove which evidence field is missing;
the fixed-label missing-evidence counter/log supplies that diagnostic.

API and worker log their own deployment ceilings at startup. Conservative built-in
defaults remain $50/order and $100/exposure; no ceiling is raised automatically.
Explicit deployment configuration and a valid account policy are required to trade
products above those ceilings. The first market-worker quote batch warns with the
symbols whose one-contract ask notional exceeds the deployment ceiling. Prices
can change; this is a startup diagnostic, not proof a product is permanently
untradeable, and it does not replace admission checks. There are no new DB polls.
