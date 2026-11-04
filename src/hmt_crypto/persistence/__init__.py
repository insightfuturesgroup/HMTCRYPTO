"""Lazy public repository evidence API; importing never constructs database resources."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AccountScope": "repository_contracts",
    "LeadershipFence": "repository_contracts",
    "CommandClaim": "repository_contracts",
    "CommandCorrelation": "repository_contracts",
    "LeaseSnapshot": "repository_contracts",
    "CommandReceipt": "repository_contracts",
    "AttemptReceipt": "repository_contracts",
    "AttemptEventReceipt": "repository_contracts",
    "AttemptEventProposal": "repository_contracts",
    "EnqueueDisposition": "repository_contracts",
    "EventDisposition": "repository_contracts",
    "EnqueueResult": "repository_contracts",
    "ClaimResult": "repository_contracts",
    "AttemptAllocationResult": "repository_contracts",
    "EventAppendResult": "repository_contracts",
    "LeadershipRepository": "leadership_repository",
    "CommandRepository": "command_repository",
    "AttemptEventRepository": "attempt_event_repository",
    "RepositoryClock": "repository_clock",
    "RepositoryConflictError": "repository_errors",
    "RepositoryInvariantError": "repository_errors",
    "RepositoryStateError": "repository_errors",
    "FenceRejectedError": "repository_errors",
    "RepositoryLimitError": "repository_errors",
    "LeaseUnavailableError": "repository_errors",
}
__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(name)
    return getattr(import_module(f"hmt_crypto.persistence.{_EXPORTS[name]}"), name)
