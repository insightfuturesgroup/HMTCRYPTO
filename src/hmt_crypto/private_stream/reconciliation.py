"""Exact private-REST reconciliation executed after stream baselines."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn, Self, SupportsIndex
from uuid import UUID
from weakref import WeakKeyDictionary

from hmt_crypto.domain.account import AccountRef, CollectionMode
from hmt_crypto.domain.private_orders import (
    StrictFillPaginationResult,
    StrictOrderPaginationResult,
)
from hmt_crypto.domain.private_positions import ObservedMarginedPositions
from hmt_crypto.domain.private_wallet import ObservedBalanceSnapshot
from hmt_crypto.errors import ConfigurationError, ExchangeProtocolError
from hmt_crypto.exchange.delta.private_read_client import DeltaIndiaPrivateReadClient
from hmt_crypto.exchange.delta.private_ws_models import (
    MarginUpdate,
    OrderSnapshot,
    OrderUpdate,
    PositionSnapshot,
    PositionUpdate,
    PrivateWsObservation,
    UserTradeUpdate,
)
from hmt_crypto.exchange.delta.private_ws_protocol import (
    PrivateWsGenerationLease,
    PrivateWsSource,
    _generation_state,
    _issue_private_ws_source,
    _plan_state,
)
from hmt_crypto.exchange.private_requests import FillQuery, MarginedPositionQuery, OpenOrderQuery


def _error(operation: str) -> ExchangeProtocolError:
    return ExchangeProtocolError(context={"operation": operation, "scope": "private"})


def _same_account(left: AccountRef, right: AccountRef) -> bool:
    return left.model_dump() == right.model_dump()


@dataclass(slots=True)
class _ReceiptState:
    generation: UUID
    account: AccountRef
    product_id: int
    catalogue_snapshot_id: UUID
    catalogue_revision: int
    catalogue_content_sha256: str
    observed_at: datetime
    evidence_sha256: str
    balances: ObservedBalanceSnapshot
    positions: ObservedMarginedPositions
    open_orders: StrictOrderPaginationResult
    fills: StrictFillPaginationResult


_RECEIPTS: WeakKeyDictionary[PrivateWsReconciliationReceipt, _ReceiptState]
_CLASSES_CLOSED = False


class PrivateWsReconciliationReceipt:
    """Opaque proof of four complete account-bound REST collections."""

    __slots__ = ("__weakref__",)

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("private WebSocket reconciliation receipt cannot be constructed")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if _CLASSES_CLOSED:
            raise TypeError("private WebSocket reconciliation receipt cannot be subclassed")

    def __copy__(self) -> NoReturn:
        raise TypeError("private WebSocket reconciliation receipt cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("private WebSocket reconciliation receipt cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("private WebSocket reconciliation receipt cannot be serialized")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("private WebSocket reconciliation receipt cannot be serialized")

    @property
    def generation(self) -> UUID:
        return UUID(int=_receipt_state(self).generation.int)

    @property
    def account(self) -> AccountRef:
        return _receipt_state(self).account.model_copy()

    @property
    def product_id(self) -> int:
        return _receipt_state(self).product_id

    @property
    def catalogue_snapshot_id(self) -> UUID:
        return UUID(int=_receipt_state(self).catalogue_snapshot_id.int)

    @property
    def catalogue_revision(self) -> int:
        return _receipt_state(self).catalogue_revision

    @property
    def catalogue_content_sha256(self) -> str:
        return _receipt_state(self).catalogue_content_sha256

    @property
    def observed_at(self) -> datetime:
        return _receipt_state(self).observed_at

    @property
    def evidence_sha256(self) -> str:
        return _receipt_state(self).evidence_sha256

    @property
    def balances(self) -> ObservedBalanceSnapshot:
        """Return an owned exact balance reset."""
        return ObservedBalanceSnapshot.model_validate(_receipt_state(self).balances)

    @property
    def positions(self) -> ObservedMarginedPositions:
        """Return an owned exact position reset."""
        return ObservedMarginedPositions.model_validate(_receipt_state(self).positions)

    @property
    def open_orders(self) -> StrictOrderPaginationResult:
        """Return an owned exact complete open-order reset."""
        return StrictOrderPaginationResult.model_validate(_receipt_state(self).open_orders)

    @property
    def fills(self) -> StrictFillPaginationResult:
        """Return an owned exact complete fill reset."""
        return StrictFillPaginationResult.model_validate(_receipt_state(self).fills)


_CLASSES_CLOSED = True
_RECEIPTS = WeakKeyDictionary()


@dataclass(slots=True)
class _ResetState:
    lease: PrivateWsGenerationLease
    source: PrivateWsSource
    receipt: PrivateWsReconciliationReceipt


_RESETS: WeakKeyDictionary[PrivateWsReconciliationReset, _ResetState]
_RESET_CLASSES_CLOSED = False


class PrivateWsReconciliationReset:
    """Opaque first-generation event carrying complete exchange REST truth."""

    __slots__ = ("__weakref__",)

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("private WebSocket reconciliation resets cannot be constructed")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if _RESET_CLASSES_CLOSED:
            raise TypeError("private WebSocket reconciliation resets cannot be subclassed")

    def __copy__(self) -> NoReturn:
        raise TypeError("private WebSocket reconciliation resets cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("private WebSocket reconciliation resets cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("private WebSocket reconciliation resets cannot be serialized")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("private WebSocket reconciliation resets cannot be serialized")

    @property
    def source(self) -> PrivateWsSource:
        return _reset_state(self).source

    @property
    def evidence_sha256(self) -> str:
        """Return the canonical digest while this generation remains current."""
        return _reset_state(self).receipt.evidence_sha256

    @property
    def balances(self) -> ObservedBalanceSnapshot:
        """Return the complete REST balance reset while current."""
        return _reset_state(self).receipt.balances

    @property
    def positions(self) -> ObservedMarginedPositions:
        """Return the complete REST position reset while current."""
        return _reset_state(self).receipt.positions

    @property
    def open_orders(self) -> StrictOrderPaginationResult:
        """Return the complete REST open-order reset while current."""
        return _reset_state(self).receipt.open_orders

    @property
    def fills(self) -> StrictFillPaginationResult:
        """Return the complete REST fill reset while current."""
        return _reset_state(self).receipt.fills


_RESET_CLASSES_CLOSED = True
_RESETS = WeakKeyDictionary()


def _receipt_state(value: object) -> _ReceiptState:
    if type(value) is not PrivateWsReconciliationReceipt:
        raise _error("attest_delta_private_ws_reconciliation")
    state = _RECEIPTS.get(value)
    if state is None:
        raise _error("attest_delta_private_ws_reconciliation")
    return state


def _reset_state(value: object) -> _ResetState:
    if type(value) is not PrivateWsReconciliationReset:
        raise _error("attest_delta_private_ws_reconciliation_reset")
    state = _RESETS.get(value)
    if state is None:
        raise _error("attest_delta_private_ws_reconciliation_reset")
    _generation_state(state.lease)
    _receipt_state(state.receipt)
    return state


def _attest_private_ws_reconciliation(
    value: object,
    *,
    generation: UUID,
    account: AccountRef,
    product_id: int,
    catalogue_snapshot_id: UUID,
    catalogue_revision: int,
    catalogue_content_sha256: str,
) -> PrivateWsReconciliationReceipt:
    state = _receipt_state(value)
    if (
        state.generation != generation
        or not _same_account(state.account, account)
        or state.product_id != product_id
        or state.catalogue_snapshot_id != catalogue_snapshot_id
        or state.catalogue_revision != catalogue_revision
        or state.catalogue_content_sha256 != catalogue_content_sha256
    ):
        raise _error("attest_delta_private_ws_reconciliation")
    assert isinstance(value, PrivateWsReconciliationReceipt)
    return value


def _attest_private_ws_reconciliation_barrier(
    value: object,
    *,
    pending: tuple[tuple[PrivateWsObservation, datetime], ...],
) -> PrivateWsReconciliationReceipt:
    """Prove every already-received frame preceded its resource's REST request."""
    state = _receipt_state(value)
    if type(pending) is not tuple or len(pending) > 64:
        raise _error("attest_delta_private_ws_reconciliation_barrier")
    for item in pending:
        if type(item) is not tuple or len(item) != 2:
            raise _error("attest_delta_private_ws_reconciliation_barrier")
        observation, received_at = item
        if (
            type(received_at) is not datetime
            or received_at.tzinfo is not UTC
            or received_at.fold != 0
        ):
            raise _error("attest_delta_private_ws_reconciliation_barrier")
        if type(observation) is MarginUpdate:
            requested_at = state.balances.context.requested_at
        elif type(observation) in {PositionSnapshot, PositionUpdate}:
            requested_at = state.positions.context.requested_at
        elif type(observation) in {OrderSnapshot, OrderUpdate}:
            requested_at = state.open_orders.provenance.first_requested_at
        elif type(observation) is UserTradeUpdate:
            requested_at = state.fills.provenance.first_requested_at
        else:
            raise _error("attest_delta_private_ws_reconciliation_barrier")
        if received_at > requested_at:
            raise _error("attest_delta_private_ws_reconciliation_barrier")
    assert isinstance(value, PrivateWsReconciliationReceipt)
    return value


def _issue_private_ws_reconciliation_reset(
    *,
    lease: PrivateWsGenerationLease,
    receipt: PrivateWsReconciliationReceipt,
    received_at: datetime,
) -> PrivateWsReconciliationReset:
    generation = _generation_state(lease)
    plan = _plan_state(generation.plan)
    checked = _attest_private_ws_reconciliation(
        receipt,
        generation=generation.generation,
        account=generation.account,
        product_id=plan.product_id,
        catalogue_snapshot_id=plan.catalogue_snapshot_id,
        catalogue_revision=plan.catalogue_revision,
        catalogue_content_sha256=plan.catalogue_content_sha256,
    )
    source = _issue_private_ws_source(
        lease=lease,
        received_at=received_at,
        reconciliation_evidence_sha256=checked.evidence_sha256,
    )
    reset = object.__new__(PrivateWsReconciliationReset)
    _RESETS[reset] = _ResetState(lease=lease, source=source, receipt=checked)
    return reset


def attest_private_ws_reconciliation_reset(value: object) -> PrivateWsReconciliationReset:
    """Return an exact reset only while its generation lease remains current."""
    _reset_state(value)
    assert isinstance(value, PrivateWsReconciliationReset)
    return value


class PrivateWsRestReconciler:
    """Run complete balances/positions/open-orders/fills reads for one product."""

    __slots__ = ("_account", "_client")

    def __init__(self, *, account: AccountRef, client: DeltaIndiaPrivateReadClient) -> None:
        if type(account) is not AccountRef or type(client) is not DeltaIndiaPrivateReadClient:
            raise ConfigurationError(context={"operation": "configure_private_ws_reconciler"})
        client_account = object.__getattribute__(client, "_account")
        if type(client_account) is not AccountRef or not _same_account(account, client_account):
            raise ConfigurationError(context={"operation": "configure_private_ws_reconciler"})
        self._account = account.model_copy()
        self._client = client

    @property
    def account(self) -> AccountRef:
        """Return the exact account bound to the sealed private-read client."""
        return self._account.model_copy()

    async def reconcile_after_baselines(
        self,
        *,
        generation: UUID,
        product_id: int,
        catalogue_snapshot_id: UUID,
        catalogue_revision: int,
        catalogue_content_sha256: str,
    ) -> PrivateWsReconciliationReceipt:
        """Collect all four resources strictly after stream snapshots were processed."""
        if (
            type(generation) is not UUID
            or generation.int == 0
            or type(product_id) is not int
            or not 1 <= product_id < 2**31
            or type(catalogue_snapshot_id) is not UUID
            or catalogue_snapshot_id.int == 0
            or type(catalogue_revision) is not int
            or catalogue_revision <= 0
            or type(catalogue_content_sha256) is not str
            or len(catalogue_content_sha256) != 64
            or any(character not in "0123456789abcdef" for character in catalogue_content_sha256)
        ):
            raise _error("reconcile_delta_private_ws")
        balances = await self._client.get_balances()
        positions = await self._client.get_margined_positions(
            MarginedPositionQuery(product_ids=(product_id,))
        )
        open_orders = await self._client.get_open_orders(
            OpenOrderQuery(product_ids=(product_id,), page_size=50),
            mode=CollectionMode.STRICT,
        )
        fills = await self._client.get_fills(
            FillQuery(product_ids=(product_id,), page_size=50),
            mode=CollectionMode.STRICT,
        )
        if (
            type(balances) is not ObservedBalanceSnapshot
            or type(positions) is not ObservedMarginedPositions
            or type(open_orders) is not StrictOrderPaginationResult
            or type(fills) is not StrictFillPaginationResult
            or open_orders.complete is not True
            or fills.complete is not True
            or not _same_account(balances.context.account, self._account)
            or not _same_account(positions.context.account, self._account)
            or not _same_account(open_orders.provenance.account, self._account)
            or not _same_account(fills.provenance.account, self._account)
        ):
            raise _error("reconcile_delta_private_ws")
        correlations = (
            balances.context.correlation_id,
            positions.context.correlation_id,
            *open_orders.provenance.page_correlation_ids,
            *fills.provenance.page_correlation_ids,
        )
        if len(correlations) > 130 or len(set(correlations)) != len(correlations):
            raise _error("reconcile_delta_private_ws")
        observed_at = max(
            balances.context.received_at,
            positions.context.received_at,
            open_orders.provenance.last_received_at,
            fills.provenance.last_received_at,
        ).astimezone(UTC)
        content = json.dumps(
            {
                "account_id": str(self._account.account_id),
                "balances": balances.model_dump(mode="json", round_trip=True),
                "catalogue_content_sha256": catalogue_content_sha256,
                "catalogue_revision": catalogue_revision,
                "catalogue_snapshot_id": str(catalogue_snapshot_id),
                "external_user_id": self._account.external_user_id,
                "fills": fills.model_dump(mode="json", round_trip=True),
                "generation": str(generation),
                "open_orders": open_orders.model_dump(mode="json", round_trip=True),
                "positions": positions.model_dump(mode="json", round_trip=True),
                "product_id": product_id,
            },
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        receipt = object.__new__(PrivateWsReconciliationReceipt)
        _RECEIPTS[receipt] = _ReceiptState(
            generation=UUID(int=generation.int),
            account=self._account.model_copy(),
            product_id=product_id,
            catalogue_snapshot_id=UUID(int=catalogue_snapshot_id.int),
            catalogue_revision=catalogue_revision,
            catalogue_content_sha256=catalogue_content_sha256,
            observed_at=observed_at,
            evidence_sha256=hashlib.sha256(content).hexdigest(),
            balances=ObservedBalanceSnapshot.model_validate(balances),
            positions=ObservedMarginedPositions.model_validate(positions),
            open_orders=StrictOrderPaginationResult.model_validate(open_orders),
            fills=StrictFillPaginationResult.model_validate(fills),
        )
        return receipt


__all__ = [
    "PrivateWsReconciliationReceipt",
    "PrivateWsReconciliationReset",
    "PrivateWsRestReconciler",
    "attest_private_ws_reconciliation_reset",
]
