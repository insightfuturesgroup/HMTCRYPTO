"""Tests-only issuers for B3's sealed worker and outcome-slot inputs."""

from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.exchange import private_dispatch
from hmt_crypto.exchange.delta.private_limiter_completion import LimiterWaitIntent
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

_ISSUED = datetime(2026, 11, 5, tzinfo=UTC)
_POLICY = "delta-docs-2026-11-04-private-v1"


def issue_worker(marker: object, process_incarnation_id: UUID) -> tuple[object, object]:
    """Issue the exact Task 2.10 proof/currentness pair through its test owner."""
    issuer = importlib.import_module("tests.integration.support.task210_worker_clock_issuer")
    return issuer.issue_single_private_worker(
        marker,
        proof_id=UUID(int=101),
        deployment_revision="b3-offline",
        external_user_id="12345",
        worker_incarnation_id=UUID(int=102),
        process_incarnation_id=process_incarnation_id,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        ownership_epoch=1,
        issued_at=_ISSUED,
        expires_at=_ISSUED + timedelta(hours=1),
        observed_at=_ISSUED + timedelta(seconds=1),
        process_started_monotonic_ns=0,
    )


def replace_worker(marker: object, currentness: object, current: object) -> object:
    """Publish an exact newer proof through the retained Task 2.10 owner."""
    issuer = importlib.import_module("tests.integration.support.task210_worker_clock_issuer")
    return issuer.replace_single_private_worker(
        marker,
        currentness,
        current=current,
        proof_id=UUID(int=103),
        ownership_epoch=2,
        issued_at=_ISSUED + timedelta(seconds=2),
        expires_at=_ISSUED + timedelta(hours=2),
        observed_at=_ISSUED + timedelta(seconds=3),
    )


def invalidate_worker(marker: object, currentness: object) -> None:
    """Publish terminal Task 2.10 ownership loss through the real owner."""
    issuer = importlib.import_module("tests.integration.support.task210_worker_clock_issuer")
    issuer.invalidate_worker_ownership(marker, currentness)


def issue_clock(
    marker: object,
    process_incarnation_id: UUID,
    *,
    observed_monotonic_ns: int,
) -> tuple[object, object]:
    """Issue the exact Task 2.10 prewarmed clock/currentness pair."""
    issuer = importlib.import_module("tests.integration.support.task210_worker_clock_issuer")
    return issuer.issue_trusted_dispatch_clock(
        marker,
        clock_owner_id=UUID(int=104),
        process_incarnation_id=process_incarnation_id,
        clock_epoch=1,
        correlation_epoch=1,
        sample_monotonic_ns=observed_monotonic_ns,
        observed_monotonic_ns=observed_monotonic_ns,
        utc_epoch_seconds=1_800_000_000,
        utc_upper_bound_ns=1_800_000_000_100_000_000,
        nonblocking_execution_guard_ns=1,
    )


def make_intent(process_incarnation_id: UUID, *, ordinal: int = 1) -> LimiterWaitIntent:
    """Build one exact India-testnet-LIVE place-order limiter intent."""
    attempt = UUID(int=200 + ordinal)
    return LimiterWaitIntent(
        account=AccountRef(
            account_id=UUID(int=10),
            external_user_id="12345",
            region=DeltaRegion.INDIA,
            environment=DeltaEnvironment.TESTNET,
            mode=ExecutionMode.LIVE,
            credential_version=ordinal,
        ),
        command_id=UUID(int=20),
        order_id=UUID(int=21),
        order_attempt_id=attempt,
        order_attempt_number=ordinal,
        root_event_id=UUID(int=22),
        logical_call_id=attempt,
        correlation_id=UUID(int=23),
        operation="PLACE_ORDER",
        route_id=PrivateRouteId.PLACE_ORDER,
        product_id=27,
        rest_weight=5,
        product_operations=1,
        policy_version=_POLICY,
        semantic_request_sha256="a" * 64,
        command_evidence_sha256="b" * 64,
        process_id=os.getpid(),
        process_incarnation_id=process_incarnation_id,
    )


def issue_outcome_slot(marker: object, intent: LimiterWaitIntent) -> object:
    """Issue one inert exact outcome slot through the neutral tests-only seam."""
    return private_dispatch._issue_venue_dispatch_outcome_slot_for_tests(
        marker,
        account=intent.account,
        command_id=intent.command_id,
        order_id=intent.order_id,
        order_attempt_id=intent.order_attempt_id,
        root_event_id=intent.root_event_id,
        logical_call_id=intent.logical_call_id,
        correlation_id=intent.correlation_id,
        order_attempt_number=intent.order_attempt_number,
        dispatch_event_id=UUID(int=300 + intent.order_attempt_number),
        consumption_id=UUID(int=400 + intent.order_attempt_number),
        operation=intent.operation,
        route_id=intent.route_id,
        policy_version=intent.policy_version,
        semantic_request_sha256=intent.semantic_request_sha256,
        command_evidence_sha256=intent.command_evidence_sha256,
    )


def advance_audited(
    registry: Any,
    handle: object,
    latch: object,
    *,
    ordinal: int,
) -> None:
    """Advance one exact owner-held test handoff through durable audit evidence."""
    registry.advance_pre_entry_once(
        handle,
        entry_latch=latch,
        phase="CLAIMED_FOR_UNSIGNED_PREPARATION",
    )
    registry.advance_pre_entry_once(
        handle,
        entry_latch=latch,
        phase="CLAIMED_FOR_UNSIGNED_PREPARATION",
        wire_request_evidence_sha256="a" * 64,
    )
    registry.advance_pre_entry_once(
        handle,
        entry_latch=latch,
        phase="AUDIT_RECORDED",
        wire_request_evidence_sha256="a" * 64,
        pre_dispatch_audit_receipt_id=UUID(int=900 + ordinal),
    )


def entry_latch(slot: object) -> object:
    """Return the exact latch issued with one tests-only outcome slot."""
    return private_dispatch._venue_dispatch_outcome_entry_latch(slot)


def outcome_marker() -> object:
    """Return the neutral leaf's exact tests-only marker."""
    return private_dispatch._private_dispatch_fixture_marker_for_tests()
