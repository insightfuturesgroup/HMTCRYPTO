"""Explicit aggregate import for schema tooling; never creates an engine or DB.

Append-only grants/triggers and immutable-scope/version enforcement are deferred
to later reviewed migration units. Import this module before inspecting metadata.
"""

from hmt_crypto.persistence.attempt_models import OrderAttempt
from hmt_crypto.persistence.catalogue_models import CatalogueProduct, CatalogueSnapshot
from hmt_crypto.persistence.command_models import CommandOperation
from hmt_crypto.persistence.coordination_models import LeadershipLease, RecoveryIncarnation
from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.execution_event_models import OrderAttemptEvent, OrderTransition
from hmt_crypto.persistence.fill_models import Fill, FillOrderLink
from hmt_crypto.persistence.identity_models import Account, CredentialVersion
from hmt_crypto.persistence.order_models import Order
from hmt_crypto.persistence.protection_models import (
    ExitIntent,
    PositionProtection,
    PositionProtectionOrder,
    ProtectionAuthorization,
)
from hmt_crypto.persistence.signal_models import Signal

metadata = Base.metadata

__all__ = [
    "Account",
    "CredentialVersion",
    "CatalogueSnapshot",
    "CatalogueProduct",
    "Signal",
    "CommandOperation",
    "Order",
    "OrderAttempt",
    "OrderAttemptEvent",
    "OrderTransition",
    "Fill",
    "FillOrderLink",
    "ExitIntent",
    "ProtectionAuthorization",
    "PositionProtection",
    "PositionProtectionOrder",
    "RecoveryIncarnation",
    "LeadershipLease",
    "metadata",
]
