"""Strict repository DTO admission, independent of database authority."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from hmt_crypto.persistence import repository_contracts as c
from hmt_crypto.persistence import repository_errors as errors
from hmt_crypto.persistence.execution_evidence import EventEvidence


class IntegerChild(int):
    pass


class StringChild(str):
    pass


@pytest.mark.parametrize(
    "alias,bad",
    [
        ("PositiveStrictInt", True),
        ("PositiveStrictInt", 1.0),
        ("PositiveStrictInt", "1"),
        ("PositiveStrictInt", IntegerChild(1)),
        ("PositiveStrictInt", 0),
        ("PositiveStrictInt", 2**63),
        ("NonNegativeStrictInt", -1),
        ("NonNegativeStrictInt", False),
        ("NulFreeBoundedString128", StringChild("x")),
        ("NulFreeBoundedString128", ""),
        ("NulFreeBoundedString128", "x\x00"),
        ("NulFreeBoundedString128", "x" * 129),
        ("Sha256Hex", "A" * 64),
        ("Sha256Hex", "a" * 63),
        ("UtcDateTime", datetime(2026, 1, 1)),
        ("UtcDateTime", "2026-01-01T00:00:00Z"),
        ("UtcDateTime", datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))),
    ],
)
def test_exact_alias_rejects(alias, bad):
    with pytest.raises((ValueError, ValidationError)):
        TypeAdapter(getattr(c, alias)).validate_python(bad)


def test_scope_is_frozen_strict_and_extra_forbidden():
    scope = c.AccountScope(account_id=uuid4(), region="india", environment="testnet", mode="PAPER")
    with pytest.raises(ValidationError):
        scope.mode = "LIVE"
    for changes in ({"account_id": str(uuid4())}, {"mode": "paper"}, {"secret": "discard"}):
        with pytest.raises(ValidationError):
            c.AccountScope(**(scope.model_dump() | changes))


@pytest.mark.parametrize(
    "kind,outcome",
    [
        ("DEFINITE_NOT_SENT", "NOT_SENT"),
        ("RESPONSE_ACCEPTED", "ACCEPTED"),
        ("RESPONSE_REJECTED", "REJECTED"),
        ("UNCERTAIN", "UNKNOWN"),
        ("RECONCILED", "RESOLVED"),
        ("SIMULATED_ACCEPTED", "ACCEPTED"),
        ("SIMULATED_REJECTED", "REJECTED"),
    ],
)
def test_event_outcome_table_and_pairs(kind, outcome):
    fields = dict(event_key="event", event_kind=kind, evidence=EventEvidence(outcome=outcome))
    if kind == "RECONCILED":
        fields.update(ingester_id=uuid4(), ingester_generation=1, reconciliation_ref=uuid4())
    assert c.AttemptEventProposal(**fields).event_kind == kind
    with pytest.raises((ValueError, ValidationError)):
        c.AttemptEventProposal(
            **(
                fields
                | {
                    "evidence": EventEvidence(
                        outcome="UNKNOWN" if outcome != "UNKNOWN" else "ACCEPTED"
                    )
                }
            )
        )
    with pytest.raises((ValueError, ValidationError)):
        c.AttemptEventProposal(**(fields | {"ingester_leader_ref": uuid4()}))


def test_dispatch_is_not_appendable():
    with pytest.raises(ValidationError):
        c.AttemptEventProposal(
            event_key="dispatch", event_kind="DISPATCH_STARTED", evidence=EventEvidence()
        )


def test_errors_are_stable_and_safe():
    for name in (
        "RepositoryConflictError",
        "RepositoryInvariantError",
        "RepositoryStateError",
        "FenceRejectedError",
        "RepositoryLimitError",
        "LeaseUnavailableError",
    ):
        error = getattr(errors, name)()
        assert error.__class__.__name__ == name
        assert "sql" not in str(error).lower()


def test_claim_pair_and_receipt_lifecycle():
    claim = dict(
        command_id=uuid4(),
        owner_id=uuid4(),
        generation=1,
        version=1,
        expires_at=datetime.now(UTC),
        leader_ref=None,
        leader_generation=None,
        recovery_ref=None,
        recovery_generation=None,
    )
    assert c.CommandClaim(**claim).generation == 1
    with pytest.raises(ValidationError):
        c.CommandClaim(**(claim | {"leader_ref": uuid4()}))


def test_public_repository_exports():
    import hmt_crypto.persistence as persistence

    for name in (
        "AccountScope",
        "LeadershipFence",
        "CommandClaim",
        "CommandCorrelation",
        "LeaseSnapshot",
        "CommandReceipt",
        "AttemptReceipt",
        "AttemptEventReceipt",
        "AttemptEventProposal",
        "EnqueueDisposition",
        "EventDisposition",
        "EnqueueResult",
        "ClaimResult",
        "AttemptAllocationResult",
        "EventAppendResult",
        "LeadershipRepository",
        "CommandRepository",
        "AttemptEventRepository",
        "RepositoryConflictError",
        "RepositoryInvariantError",
        "RepositoryStateError",
        "FenceRejectedError",
        "RepositoryLimitError",
        "LeaseUnavailableError",
    ):
        assert hasattr(persistence, name)
    assert not hasattr(persistence, "LockedCommand")
    assert not hasattr(persistence, "_mint_lock")


def test_proposal_rejects_subclassed_nested_evidence():
    class ExtendedEvent(EventEvidence):
        extra_field: str = "unreviewed"

    with pytest.raises(ValidationError):
        c.AttemptEventProposal(
            event_key="classified",
            event_kind="UNCERTAIN",
            evidence=ExtendedEvent(outcome="UNKNOWN"),
        )


def test_utc_rejects_hostile_timezone_before_invoking_hooks():
    from datetime import tzinfo

    calls = []

    class HostileZone(tzinfo):
        def utcoffset(self, dt):
            calls.append("hook")
            raise AssertionError("An untrusted timezone hook was invoked")

    with pytest.raises(ValueError):
        TypeAdapter(c.UtcDateTime).validate_python(datetime(2026, 1, 1, tzinfo=HostileZone()))
    assert not calls
