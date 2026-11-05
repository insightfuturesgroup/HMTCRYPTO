"""Explicit aggregate import for schema tooling; never creates an engine or DB.

Append-only grants/triggers and immutable-scope/version enforcement are deferred
to later reviewed migration units. Import this module before inspecting metadata.
"""

from hmt_crypto.persistence.accounting_observation_models import FundingSnapshot, WalletTransaction
from hmt_crypto.persistence.api_audit_models import ApiAuditEvent
from hmt_crypto.persistence.attempt_models import OrderAttempt
from hmt_crypto.persistence.catalogue_models import CatalogueProduct, CatalogueSnapshot
from hmt_crypto.persistence.command_models import CommandOperation
from hmt_crypto.persistence.coordination_models import (
    LeadershipLease,
    RecoveryIncarnation,
    SchedulerState,
    WorkerHeartbeat,
)
from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.execution_event_models import OrderAttemptEvent, OrderTransition
from hmt_crypto.persistence.fill_models import Fill, FillOrderLink
from hmt_crypto.persistence.identity_models import Account, CredentialVersion
from hmt_crypto.persistence.order_models import Order
from hmt_crypto.persistence.pnl_models import PnlSnapshot
from hmt_crypto.persistence.protection_models import (
    ExitIntent,
    PositionProtection,
    PositionProtectionOrder,
    ProtectionAuthorization,
)
from hmt_crypto.persistence.reconciliation_drift_models import (
    ReconciliationAcknowledgement,
    ReconciliationDrift,
)
from hmt_crypto.persistence.reconciliation_models import ReconciliationRun
from hmt_crypto.persistence.reconciliation_resource_models import ReconciliationResource
from hmt_crypto.persistence.risk_decision_models import RiskDecision
from hmt_crypto.persistence.risk_models import RiskHalt, RiskHaltEvent, RiskState
from hmt_crypto.persistence.risk_rate_consumption_models import RiskRateConsumption
from hmt_crypto.persistence.risk_reservation_models import RiskReservation
from hmt_crypto.persistence.signal_models import Signal
from hmt_crypto.persistence.snapshot_models import BalanceSnapshot, PositionSnapshot

metadata = Base.metadata

__all__ = [
    "Account",
    "ApiAuditEvent",
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
    "WorkerHeartbeat",
    "SchedulerState",
    "ReconciliationRun",
    "ReconciliationResource",
    "ReconciliationDrift",
    "ReconciliationAcknowledgement",
    "RiskState",
    "RiskHalt",
    "RiskHaltEvent",
    "RiskDecision",
    "RiskReservation",
    "RiskRateConsumption",
    "PositionSnapshot",
    "BalanceSnapshot",
    "WalletTransaction",
    "FundingSnapshot",
    "PnlSnapshot",
    "metadata",
]
