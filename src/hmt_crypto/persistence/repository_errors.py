"""Stable secret-safe repository failures."""

from hmt_crypto.persistence.errors import PersistenceError


class RepositoryConflictError(PersistenceError):
    _code = "repository_conflict"
    _message = "Durable repository identity conflicts"


class RepositoryInvariantError(PersistenceError):
    _code = "repository_invariant"
    _message = "Stored repository state is invalid"


class RepositoryStateError(PersistenceError):
    _code = "repository_state"
    _message = "Repository lifecycle does not admit this operation"


class FenceRejectedError(PersistenceError):
    _code = "fence_rejected"
    _message = "Repository fence is stale"


class RepositoryLimitError(PersistenceError):
    _code = "repository_limit"
    _message = "Repository resource bound exceeded"


class LeaseUnavailableError(PersistenceError):
    _code = "lease_unavailable"
    _message = "Leadership lease is unavailable"
