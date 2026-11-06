"""Fail-closed server composition for the India private-read client."""

from __future__ import annotations

from hmt_crypto.domain.account import AccountRef
from hmt_crypto.exchange import credentials
from hmt_crypto.exchange.delta.private_limiter_registry import PrivateLimiterLease
from hmt_crypto.exchange.delta.private_read_audit import PrivateReadAuditSink
from hmt_crypto.exchange.delta.private_read_client import DeltaIndiaPrivateReadClient
from hmt_crypto.exchange.delta.private_read_clock import PrivateReadTrustedInstantIssuer
from hmt_crypto.exchange.delta.private_read_runtime import _DeltaPrivateReadRuntime
from hmt_crypto.worker import clock as trusted_clock
from hmt_crypto.worker import leadership


def build_delta_india_private_read_client(
    *,
    account: AccountRef,
    signer: credentials.OwnedDeltaSignerSnapshot,
    account_binding: credentials.AccountBindingProofSnapshot,
    credential_currentness: credentials.Task27PrivateCredentialCurrentnessPort,
    worker_proof: leadership.Task210SinglePrivateWorkerProof,
    worker_currentness: leadership.Task210PrivateWorkerCurrentnessPort,
    clock: trusted_clock.Task210TrustedDispatchClock,
    clock_currentness: trusted_clock.Task210TrustedClockCurrentnessPort,
    trusted_instant_issuer: PrivateReadTrustedInstantIssuer,
    limiter_lease: PrivateLimiterLease,
    audit_sink: PrivateReadAuditSink,
) -> DeltaIndiaPrivateReadClient:
    """Compose only from exact pre-issued identity, clock, audit, and limiter evidence.

    There are deliberately no environment-derived defaults. Production remains
    unavailable until server bootstrap can issue every exact authority, including a
    verified account binding, refreshable trusted instants, a durable audit sink, and
    the central per-user limiter lease.
    """
    runtime = _DeltaPrivateReadRuntime(
        account=account,
        signer=signer,
        account_binding=account_binding,
        credential_currentness=credential_currentness,
        worker_proof=worker_proof,
        worker_currentness=worker_currentness,
        clock=clock,
        clock_currentness=clock_currentness,
        trusted_instant_issuer=trusted_instant_issuer,
        limiter_lease=limiter_lease,
        audit_sink=audit_sink,
    )
    return DeltaIndiaPrivateReadClient(account=account, runtime=runtime)


__all__ = ["build_delta_india_private_read_client"]
