"""Single-writer in-memory market state with immutable consumer snapshots."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from typing import cast
from uuid import UUID

from hmt_crypto.exchange.base import MarketComponent
from hmt_crypto.market_data.contracts import (
    BookDeltaObservation,
    BookSnapshotObservation,
    MarketObservation,
    MarketSource,
    ProductStatusObservation,
    SpotObservation,
    SystemStatusObservation,
)
from hmt_crypto.market_data.orderbook import (
    BookApplyStatus,
    BookInvalidationReason,
    BookRecoveryAction,
    DeltaOrderBookEngine,
)
from hmt_crypto.market_data.readiness import (
    FreshnessRequirement,
    MarketFreshnessPolicy,
    MarketReadinessEvidence,
    MarketRequirements,
    duration_ns,
)
from hmt_crypto.market_data.state_models import (
    ComponentKey as _ComponentKey,
)
from hmt_crypto.market_data.state_models import (
    MarketPublication,
    MarketStateSnapshot,
    ProductMarketSnapshot,
)
from hmt_crypto.market_data.state_models import (
    SourceIdentity as _SourceIdentity,
)
from hmt_crypto.market_data.state_models import (
    StateRecord as _Record,
)
from hmt_crypto.market_data.state_models import (
    build_product_snapshot as _build_product_snapshot,
)
from hmt_crypto.market_data.state_models import (
    observation_component as _component,
)
from hmt_crypto.market_data.state_models import (
    observation_owner_id as _owner_id,
)
from hmt_crypto.market_data.state_models import (
    observation_source as _source,
)

_ZERO_UUID = UUID(int=0)
_DEDUP_LIMIT = 4_096
_CATALOGUE_COMPONENTS = {
    "ticker": MarketComponent.TICKER,
    "ob_l1": MarketComponent.L1,
    "ob_l2": MarketComponent.L2,
    "ob_updates": MarketComponent.FULL_BOOK,
    "trades": MarketComponent.TRADES,
    "mark_price": MarketComponent.MARK,
    "funding_rate": MarketComponent.FUNDING,
    "product_updates": MarketComponent.PRODUCT_STATUS,
}


class MarketStateStore:
    """Synchronous no-queue state owner; one actor is the sole caller of apply."""

    def __init__(
        self,
        *,
        freshness_policy: MarketFreshnessPolicy,
        monotonic_ns: Callable[[], int],
    ) -> None:
        if type(freshness_policy) is not MarketFreshnessPolicy or not callable(monotonic_ns):
            raise TypeError("market state dependencies are invalid")
        self._policy = freshness_policy
        self._clock = monotonic_ns
        self._last_monotonic_ns: int | None = None
        self._clock_poisoned = False
        self._revision = 0
        self._active_generation: UUID | None = None
        self._invalidated_generations: set[UUID] = set()
        self._records: dict[_ComponentKey, _Record] = {}
        self._seen: OrderedDict[
            _SourceIdentity, tuple[MarketObservation | None, MarketPublication]
        ] = OrderedDict()
        self._evicted_through: dict[UUID, int] = {}
        self._books: dict[int, DeltaOrderBookEngine] = {}
        self._system_blocked_catalogues: set[UUID] = set()
        self._product_blocked_catalogues: dict[int, set[UUID]] = {}

    def _now(self) -> int:
        value = self._clock()
        if type(value) is not int or value < 0:
            self._clock_poisoned = True
            return 0
        if self._last_monotonic_ns is not None and value < self._last_monotonic_ns:
            self._clock_poisoned = True
        else:
            self._last_monotonic_ns = value
        return value

    def _invalidate_key(self, key: _ComponentKey, reason: str) -> None:
        record = self._records.get(key)
        if record is not None:
            record.valid = False
            record.reason = reason

    def _accept_generation(self, generation: UUID) -> bool:
        if generation in self._invalidated_generations:
            return False
        if self._active_generation is None:
            self._active_generation = generation
            return True
        if generation == self._active_generation:
            return True
        if self._active_generation in self._invalidated_generations:
            self._active_generation = generation
            return True
        self._clock_poisoned = True
        return False

    @property
    def dedup_entry_count(self) -> int:
        """Return bounded in-memory source identities for observability/tests."""
        return len(self._seen)

    def _remember(
        self,
        identity: _SourceIdentity,
        observation: MarketObservation | None,
        publication: MarketPublication,
    ) -> None:
        self._seen[identity] = (observation, publication)
        self._seen.move_to_end(identity)
        while len(self._seen) > _DEDUP_LIMIT:
            evicted, _ = self._seen.popitem(last=False)
            self._evicted_through[evicted[0]] = max(
                evicted[1], self._evicted_through.get(evicted[0], 0)
            )

    def apply(self, observation: MarketObservation) -> MarketPublication:
        """Apply one observation synchronously; no internal queue can overflow."""
        component = _component(observation)
        source = _source(observation)
        identity = (source.connection_generation, source.local_sequence)
        if source.connection_generation in self._invalidated_generations:
            return MarketPublication(
                revision=self._revision,
                source_identity=identity,
                component=component,
                applied=False,
                duplicate=True,
                conflict=False,
            )
        prior = self._seen.get(identity)
        if prior is not None and (prior[0] is None or prior[0] == observation):
            self._seen.move_to_end(identity)
            return prior[1]
        key = (_owner_id(observation), component)
        if identity[1] <= self._evicted_through.get(identity[0], 0):
            return MarketPublication(
                revision=self._revision,
                source_identity=identity,
                component=component,
                applied=False,
                duplicate=True,
                conflict=False,
            )
        if prior is not None:
            assert prior[0] is not None
            prior_key = (_owner_id(prior[0]), _component(prior[0]))
            self._invalidate_key(prior_key, f"{prior_key[1].value}_conflicting_source")
            self._invalidate_key(key, f"{component.value}_conflicting_source")
            conflict_recovery = None
            for owner_id, affected_component in {prior_key, key}:
                if affected_component is MarketComponent.FULL_BOOK and owner_id is not None:
                    engine = self._books.get(owner_id)
                    if engine is not None:
                        engine.invalidate(BookInvalidationReason.IDENTITY)
                        record = self._records.get((owner_id, MarketComponent.FULL_BOOK))
                        if record is not None:
                            record.value = engine.publication
                    conflict_recovery = BookRecoveryAction.RESUBSCRIBE_SYMBOL_AFTER_5_SECONDS
            self._revision += 1
            publication = MarketPublication(
                revision=self._revision,
                source_identity=identity,
                component=component,
                applied=False,
                duplicate=False,
                conflict=True,
                book_recovery_action=conflict_recovery,
            )
            self._remember(identity, None, publication)
            return publication
        now = self._now()
        accepted_generation = self._accept_generation(source.connection_generation)
        recovery: BookRecoveryAction | None = None
        value: object = observation
        valid = accepted_generation and not self._clock_poisoned
        reason = None
        if not accepted_generation:
            reason = "generation_invalidated"
        elif self._clock_poisoned:
            reason = "monotonic_clock_regressed"
        if valid and type(observation) in {BookSnapshotObservation, BookDeltaObservation}:
            product_id = cast(
                BookSnapshotObservation | BookDeltaObservation, observation
            ).product_id
            engine = self._books.setdefault(product_id, DeltaOrderBookEngine())
            result = engine.apply(cast(BookSnapshotObservation | BookDeltaObservation, observation))
            value = result.publication
            recovery = result.required_action
            valid = result.status is BookApplyStatus.ACCEPTED
            reason = None if valid else "full_book_invalidated"
        self._records[key] = _Record(
            value=value,
            received_at_monotonic_ns=now,
            valid=valid,
            reason=reason,
        )
        if type(observation) is SystemStatusObservation and observation.status != "live":
            self._system_blocked_catalogues.add(source.catalogue_snapshot_id)
        if type(observation) is ProductStatusObservation and (
            observation.event in {"market_disruption", "start_auction"}
            or observation.trading_status != "operational"
        ):
            self._product_blocked_catalogues.setdefault(observation.product_id, set()).add(
                source.catalogue_snapshot_id
            )
        self._revision += 1
        publication = MarketPublication(
            revision=self._revision,
            source_identity=identity,
            component=component,
            applied=valid,
            duplicate=False,
            conflict=False,
            book_recovery_action=recovery,
        )
        self._remember(identity, observation, publication)
        return publication

    def poison_catalogue_mismatch(self, product_id: int | None, channel: str) -> None:
        """Poison only a resolvable affected product/component before reconnect."""
        if product_id is None:
            return
        if type(product_id) is not int or product_id <= 0 or type(channel) is not str:
            raise ValueError("catalogue mismatch identity is invalid")
        component = (
            MarketComponent.CANDLE
            if channel.startswith("candlestick_")
            else _CATALOGUE_COMPONENTS.get(channel)
        )
        if component is None:
            return
        record = self._records.get((product_id, component))
        if record is None or record.reason == "catalogue_identity_mismatch":
            return
        record.valid = False
        record.reason = "catalogue_identity_mismatch"
        if component is MarketComponent.FULL_BOOK:
            engine = self._books.get(product_id)
            if engine is not None:
                engine.invalidate(BookInvalidationReason.IDENTITY)
                record.value = engine.publication
        self._revision += 1

    def invalidate_book(self, product_id: int, reason: BookInvalidationReason) -> None:
        """Invalidate one exact book after a typed venue error or repair request."""
        if type(product_id) is not int or product_id <= 0:
            raise ValueError("book product identity is invalid")
        engine = self._books.get(product_id)
        if engine is not None:
            engine.invalidate(reason)
            record = self._records.get((product_id, MarketComponent.FULL_BOOK))
            if record is not None:
                record.value = engine.publication
                record.valid = False
                record.reason = "full_book_invalidated"
            self._revision += 1

    def invalidate_generation(self, generation: UUID, reason: str) -> None:
        """Retain display values while invalidating every component from a generation."""
        if type(generation) is not UUID or type(reason) is not str or not reason:
            raise ValueError("generation invalidation is invalid")
        self._invalidated_generations.add(generation)
        changed = False
        for key, record in self._records.items():
            value_source = getattr(record.value, "source", None)
            if (
                type(value_source) is MarketSource
                and value_source.connection_generation == generation
            ):
                record.valid = False
                if record.reason != "catalogue_identity_mismatch":
                    record.reason = "generation_invalidated"
                if key[1] is MarketComponent.FULL_BOOK and key[0] is not None:
                    engine = self._books.get(key[0])
                    if engine is not None:
                        engine.invalidate(BookInvalidationReason.GENERATION_INVALIDATED)
                        record.value = engine.publication
                        self._books.pop(key[0], None)
                changed = True
        if changed:
            self._revision += 1

    def _record_reason(
        self,
        key: _ComponentKey,
        requirement: FreshnessRequirement,
        *,
        now: int,
        evidence: MarketReadinessEvidence,
    ) -> str | None:
        record = self._records.get(key)
        name = requirement.component.value
        if record is None or record.value is None:
            return f"{name}_missing"
        if not record.valid:
            return record.reason or f"{name}_invalid"
        value_source = getattr(record.value, "source", None)
        if type(value_source) is not MarketSource:
            return f"{name}_source_missing"
        if value_source.connection_generation != evidence.connection_generation:
            return f"{name}_wrong_generation"
        if value_source.catalogue_snapshot_id != evidence.catalogue_snapshot_id:
            return f"{name}_wrong_catalogue"
        max_age = requirement.max_age
        if max_age is None:
            max_age = self._policy.max_age(requirement.component)
        limit = duration_ns(max_age)
        if limit is not None and now - record.received_at_monotonic_ns > limit:
            return f"{name}_stale"
        return None

    def _status_reasons(
        self,
        product_ids: tuple[int, ...],
        evidence: MarketReadinessEvidence,
        *,
        require_system: bool,
        require_product: bool,
        now: int,
    ) -> list[str]:
        reasons: list[str] = []
        system = self._records.get((None, MarketComponent.SYSTEM_STATUS))
        if system is None:
            if require_system:
                reasons.append("system_status_missing")
        elif isinstance(system.value, SystemStatusObservation):
            if not system.valid:
                reasons.append(system.reason or "system_status_invalid")
            elif system.value.status != "live":
                reasons.append(f"system_{system.value.status}")
            elif evidence.catalogue_snapshot_id in self._system_blocked_catalogues:
                reasons.append("catalogue_refresh_required_after_system_disruption")
            elif require_system:
                requirement = FreshnessRequirement(component=MarketComponent.SYSTEM_STATUS)
                found = self._record_reason(
                    (None, MarketComponent.SYSTEM_STATUS),
                    requirement,
                    now=now,
                    evidence=evidence,
                )
                if found is not None:
                    reasons.append(found)
        for product_id in product_ids:
            record = self._records.get((product_id, MarketComponent.PRODUCT_STATUS))
            if record is None:
                if require_product:
                    reasons.append(f"product_{product_id}_status_missing")
                continue
            if not isinstance(record.value, ProductStatusObservation):
                reasons.append(f"product_{product_id}_status_invalid")
                continue
            if require_product:
                found = self._record_reason(
                    (product_id, MarketComponent.PRODUCT_STATUS),
                    FreshnessRequirement(component=MarketComponent.PRODUCT_STATUS),
                    now=now,
                    evidence=evidence,
                )
                if found is not None:
                    reasons.append(found)
                    continue
            status = record.value
            if (
                status.event in {"market_disruption", "start_auction"}
                or status.trading_status != "operational"
            ):
                reasons.append(f"product_{product_id}_disrupted")
            elif evidence.catalogue_snapshot_id in self._product_blocked_catalogues.get(
                product_id, set()
            ):
                reasons.append(f"product_{product_id}_catalogue_refresh_required_after_disruption")
        return reasons

    def _product_snapshot(self, product_id: int) -> ProductMarketSnapshot:
        return _build_product_snapshot(self._records, product_id)

    def snapshot(
        self,
        requirements: MarketRequirements,
        *,
        evidence: MarketReadinessEvidence | None = None,
    ) -> MarketStateSnapshot:
        """Build one fail-closed immutable snapshot for exact consumer requirements."""
        if type(requirements) is not MarketRequirements:
            raise TypeError("market requirements are invalid")
        now = self._now()
        reasons: list[str] = []
        if self._clock_poisoned:
            reasons.append("monotonic_clock_regressed")
        if evidence is None:
            reasons.append("readiness_evidence_missing")
            safe_evidence = MarketReadinessEvidence(
                connection_generation=_ZERO_UUID,
                catalogue_snapshot_id=_ZERO_UUID,
                catalogue_fresh=False,
                supervisor_barrier_current=False,
            )
        else:
            safe_evidence = evidence
        if not safe_evidence.catalogue_fresh:
            reasons.append("catalogue_not_fresh")
        if not safe_evidence.supervisor_barrier_current:
            reasons.append("supervisor_barrier_not_current")
        if self._active_generation != safe_evidence.connection_generation:
            reasons.append("connection_generation_mismatch")
        for product_id in requirements.product_ids:
            for requirement in requirements.components:
                reason = self._record_reason(
                    (product_id, requirement.component),
                    requirement,
                    now=now,
                    evidence=safe_evidence,
                )
                if reason is not None:
                    reasons.append(reason)
        for asset_id in requirements.spot_asset_ids:
            reason = self._record_reason(
                (asset_id, MarketComponent.SPOT),
                FreshnessRequirement(component=MarketComponent.SPOT),
                now=now,
                evidence=safe_evidence,
            )
            if reason is not None:
                reasons.append(reason)
        reasons.extend(
            self._status_reasons(
                requirements.product_ids,
                safe_evidence,
                require_system=requirements.require_system_status,
                require_product=requirements.require_product_status,
                now=now,
            )
        )
        products = tuple(
            self._product_snapshot(product_id) for product_id in requirements.product_ids
        )
        system_record = self._records.get((None, MarketComponent.SYSTEM_STATUS))
        system_status = (
            system_record.value
            if system_record is not None
            and isinstance(system_record.value, SystemStatusObservation)
            else None
        )
        spots = tuple(
            record.value
            for (owner_id, component), record in sorted(
                self._records.items(), key=lambda item: (item[0][0] or 0, item[0][1].value)
            )
            if owner_id is not None
            and component is MarketComponent.SPOT
            and isinstance(record.value, SpotObservation)
        )
        unique_reasons = tuple(dict.fromkeys(reasons))
        return MarketStateSnapshot(
            revision=self._revision,
            connection_generation=self._active_generation or safe_evidence.connection_generation,
            usable=not unique_reasons,
            reasons=unique_reasons,
            products=products,
            system_status=system_status,
            spots=spots,
        )
