"""Fixed secret-safe failures for the session-bound API-audit append service."""

from hmt_crypto.persistence.errors import PersistenceError


class AuditEvidenceRejected(PersistenceError):
    """An append input is outside the accepted single-event evidence contract."""

    _code = "audit_evidence_rejected"
    _message = "API audit evidence was rejected"


class AuditConflict(PersistenceError):
    """A durable audit idempotency identity has divergent canonical evidence."""

    _code = "audit_conflict"
    _message = "API audit evidence conflicts"


class AuditInvariantViolation(PersistenceError):
    """Stored API-audit metadata or payload state is incomplete or corrupt."""

    _code = "audit_invariant_violation"
    _message = "Stored API audit state is invalid"


class AuditTransactionStateError(PersistenceError):
    """The supplied session is not a clean inactive transaction boundary."""

    _code = "audit_transaction_state"
    _message = "API audit transaction state is invalid"


class AuditUnavailable(PersistenceError):
    """Audit storage was unavailable before a new append could be admitted."""

    _code = "audit_unavailable"
    _message = "API audit storage is unavailable"


class AuditCommitUncertain(PersistenceError):
    """The owned transaction did not positively acknowledge commit."""

    _code = "audit_commit_uncertain"
    _message = "API audit commit was not acknowledged"


__all__ = [
    "AuditCommitUncertain",
    "AuditConflict",
    "AuditEvidenceRejected",
    "AuditInvariantViolation",
    "AuditTransactionStateError",
    "AuditUnavailable",
]
