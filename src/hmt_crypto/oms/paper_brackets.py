"""Pure deterministic bracket child and OCO lifecycle proposals."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from hmt_crypto.oms.paper_bracket_values import _copy_proposal, _digest, _same_decimal
from hmt_crypto.oms.paper_identity import child_sha256, config_sha256, oco_sha256, revision_sha256
from hmt_crypto.oms.paper_models import (
    PaperBracketAction,
    PaperBracketRole,
    PaperChildOrderProposal,
    PaperModelConfig,
    PaperOrderIntent,
    PaperOrderKind,
    PaperOrderState,
    PaperOrderStatus,
    PaperSide,
    PaperSimulationError,
    PaperTriggerDirection,
)
from hmt_crypto.oms.paper_state import _copy_config, _copy_state, submit_paper_order
from hmt_crypto.oms.paper_values import _copy_uuid, _exact_int


@dataclass(frozen=True, slots=True)
class PaperBracketLifecycle:
    """Latest logical child revisions and the first executed OCO member."""

    parent_order_id: UUID
    oco_group_sha256: str
    current_children: tuple[PaperChildOrderProposal, ...]
    executed_child_id_sha256: str | None
    child_states: tuple[PaperOrderState, ...]

    def __post_init__(self) -> None:
        parent = _copy_uuid(self.parent_order_id)
        group = _digest(self.oco_group_sha256)
        if (
            type(self.current_children) is not tuple
            or type(self.child_states) is not tuple
            or not 1 <= len(self.current_children) <= 2
            or len(self.current_children) != len(self.child_states)
        ):
            raise _fail() from None
        proposals = tuple(_copy_proposal(item) for item in self.current_children)
        states = tuple(_copy_state(item) for item in self.child_states)
        pairs = sorted(zip(proposals, states, strict=True), key=lambda item: item[0].role.value)
        proposals = tuple(item[0] for item in pairs)
        states = tuple(item[1] for item in pairs)
        executed = (
            None
            if self.executed_child_id_sha256 is None
            else _digest(self.executed_child_id_sha256)
        )
        if (
            len({item.role for item in proposals}) != len(proposals)
            or any(
                proposal.parent_order_id != parent
                or proposal.oco_group_sha256 != group
                or state.intent.order_id != UUID(hex=proposal.child_id_sha256[:32])
                or state.intent.product_id != proposal.product_id
                or state.intent.side is not proposal.side
                or state.intent.contracts != proposal.contracts
                or state.intent.kind is not proposal.kind
                or not _same_decimal(state.intent.stop_price, proposal.stop_price)
                or not _same_decimal(state.intent.limit_price, proposal.limit_price)
                or not _same_decimal(state.intent.trail_amount, proposal.trail_amount)
                or state.intent.trigger_direction is not proposal.trigger_direction
                or state.intent.reduce_only is not True
                or state.intent.bracket is not None
                or proposal.action is PaperBracketAction.CANCEL
                and state.status is not PaperOrderStatus.CANCELLED
                for proposal, state in zip(proposals, states, strict=True)
            )
            or executed is not None
            and (
                executed not in {item.child_id_sha256 for item in proposals}
                or not any(
                    proposal.child_id_sha256 == executed and state.filled_contracts > 0
                    for proposal, state in zip(proposals, states, strict=True)
                )
                or any(
                    proposal.child_id_sha256 != executed
                    and proposal.action is not PaperBracketAction.CANCEL
                    for proposal in proposals
                )
            )
            or executed is None
            and any(
                proposal.action is PaperBracketAction.CANCEL
                or state.status not in (PaperOrderStatus.WORKING, PaperOrderStatus.TRIGGERED)
                or state.filled_contracts != 0
                for proposal, state in zip(proposals, states, strict=True)
            )
            or executed is not None
            and any(
                proposal.child_id_sha256 == executed
                and proposal.action is PaperBracketAction.CANCEL
                for proposal in proposals
            )
        ):
            raise _fail() from None
        object.__setattr__(self, "parent_order_id", parent)
        object.__setattr__(self, "oco_group_sha256", group)
        object.__setattr__(self, "current_children", proposals)
        object.__setattr__(self, "executed_child_id_sha256", executed)
        object.__setattr__(self, "child_states", states)


@dataclass(frozen=True, slots=True)
class PaperBracketUpdate:
    """Next inert lifecycle plus create/replace/cancel proposals."""

    lifecycle: PaperBracketLifecycle
    proposals: tuple[PaperChildOrderProposal, ...]


def _fail() -> PaperSimulationError:
    return PaperSimulationError()


def _limit_is_outside_trigger(
    side: PaperSide,
    trigger: Decimal | None,
    limit: Decimal | None,
) -> bool:
    if trigger is None or limit is None:
        return False
    return limit > trigger if side is PaperSide.SELL else limit < trigger


def _safe_for_fill(state: PaperOrderState, fill_price: Decimal) -> bool:
    bracket = state.intent.bracket
    if bracket is None:
        return True
    is_long = state.intent.side is PaperSide.BUY
    child_side = PaperSide.SELL if is_long else PaperSide.BUY
    unsafe_trigger = (
        bracket.stop_loss_price is not None
        and (
            (is_long and bracket.stop_loss_price >= fill_price)
            or (not is_long and bracket.stop_loss_price <= fill_price)
        )
        or bracket.take_profit_price is not None
        and (
            (is_long and bracket.take_profit_price <= fill_price)
            or (not is_long and bracket.take_profit_price >= fill_price)
        )
    )
    unsafe_limit = _limit_is_outside_trigger(
        child_side, bracket.stop_loss_price, bracket.stop_loss_limit_price
    ) or _limit_is_outside_trigger(
        child_side, bracket.take_profit_price, bracket.take_profit_limit_price
    )
    return not (unsafe_trigger or unsafe_limit)


def _child_specs(
    state: PaperOrderState,
) -> tuple[
    tuple[
        PaperBracketRole,
        PaperOrderKind,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        PaperTriggerDirection,
    ],
    ...,
]:
    bracket = state.intent.bracket
    if bracket is None:
        return ()
    is_long = state.intent.side is PaperSide.BUY
    stop_direction = PaperTriggerDirection.BELOW if is_long else PaperTriggerDirection.ABOVE
    profit_direction = PaperTriggerDirection.ABOVE if is_long else PaperTriggerDirection.BELOW
    specs: list[
        tuple[
            PaperBracketRole,
            PaperOrderKind,
            Decimal | None,
            Decimal | None,
            Decimal | None,
            PaperTriggerDirection,
        ]
    ] = []
    if bracket.stop_loss_price is not None or bracket.stop_loss_trail_amount is not None:
        kind = (
            PaperOrderKind.TRAILING_STOP
            if bracket.stop_loss_trail_amount is not None
            else PaperOrderKind.STOP_LIMIT
            if bracket.stop_loss_limit_price is not None
            else PaperOrderKind.STOP_MARKET
        )
        specs.append(
            (
                PaperBracketRole.STOP_LOSS,
                kind,
                bracket.stop_loss_price,
                bracket.stop_loss_limit_price,
                bracket.stop_loss_trail_amount,
                stop_direction,
            )
        )
    if bracket.take_profit_price is not None:
        kind = (
            PaperOrderKind.STOP_LIMIT
            if bracket.take_profit_limit_price is not None
            else PaperOrderKind.STOP_MARKET
        )
        specs.append(
            (
                PaperBracketRole.TAKE_PROFIT,
                kind,
                bracket.take_profit_price,
                bracket.take_profit_limit_price,
                None,
                profit_direction,
            )
        )
    return tuple(specs)


def bracket_is_safe_for_fill(state: PaperOrderState, fill_price: Decimal) -> bool:
    """Return whether configured children genuinely protect this simulated fill."""
    return _safe_for_fill(_copy_state(state), fill_price)


def _new_child_state(
    parent: PaperOrderState,
    proposal: PaperChildOrderProposal,
    config: PaperModelConfig,
) -> PaperOrderState:
    intent = PaperOrderIntent(
        order_id=UUID(hex=proposal.child_id_sha256[:32]),
        paper_account_id=parent.intent.paper_account_id,
        mode=parent.intent.mode,
        product_id=parent.intent.product_id,
        side=proposal.side,
        contracts=proposal.contracts,
        kind=proposal.kind,
        tick_size=parent.intent.tick_size,
        limit_price=proposal.limit_price,
        stop_price=proposal.stop_price,
        trail_amount=proposal.trail_amount,
        trigger_direction=proposal.trigger_direction,
        reduce_only=True,
        bracket=None,
    )
    return submit_paper_order(
        intent,
        submitted_event_index=parent.last_event_index,
        config=config,
        stream_id=parent.stream_id,
    )


def _resized_child_state(
    state: PaperOrderState,
    proposal: PaperChildOrderProposal,
) -> PaperOrderState:
    delta = proposal.contracts - state.intent.contracts
    if delta <= 0 or state.status in (
        PaperOrderStatus.FILLED,
        PaperOrderStatus.CANCELLED,
        PaperOrderStatus.REJECTED,
    ):
        raise _fail() from None
    return _copy_state(
        replace(
            state,
            intent=replace(state.intent, contracts=proposal.contracts),
            remaining_contracts=state.remaining_contracts + delta,
        )
    )


def reconcile_bracket_children(
    state: PaperOrderState,
    *,
    config: PaperModelConfig,
    fill_price: Decimal,
    target_contracts: int,
    lifecycle: PaperBracketLifecycle | None,
) -> PaperBracketUpdate | None:
    """Create or deterministically replace stable logical bracket children."""
    target = _exact_int(target_contracts, minimum=1)
    owned_config = _copy_config(config)
    if state.intent.bracket is None:
        raise _fail() from None
    if (
        target != state.filled_contracts
        or config_sha256(owned_config) != state.config_sha256
        or not _safe_for_fill(state, fill_price)
    ):
        return None
    group = oco_sha256(state.intent, state.config_sha256)
    if lifecycle is not None:
        if type(lifecycle) is not PaperBracketLifecycle:
            raise _fail() from None
        lifecycle = PaperBracketLifecycle(
            lifecycle.parent_order_id,
            lifecycle.oco_group_sha256,
            lifecycle.current_children,
            lifecycle.executed_child_id_sha256,
            lifecycle.child_states,
        )
        if (
            lifecycle.parent_order_id != state.intent.order_id
            or lifecycle.oco_group_sha256 != group
            or lifecycle.executed_child_id_sha256 is not None
            or any(
                child.intent.paper_account_id != state.intent.paper_account_id
                or child.intent.mode is not state.intent.mode
                for child in lifecycle.child_states
            )
        ):
            raise _fail() from None
    previous = {
        proposal.role: (proposal, child_state)
        for proposal, child_state in (
            ()
            if lifecycle is None
            else zip(lifecycle.current_children, lifecycle.child_states, strict=True)
        )
    }
    child_side = PaperSide.SELL if state.intent.side is PaperSide.BUY else PaperSide.BUY
    current: list[PaperChildOrderProposal] = []
    child_states: list[PaperOrderState] = []
    proposals: list[PaperChildOrderProposal] = []
    for role, kind, stop, limit, trail, direction in _child_specs(state):
        child_id = child_sha256(group, role)
        revision = revision_sha256(child_id, target)
        prior_pair = previous.get(role)
        prior = None if prior_pair is None else prior_pair[0]
        if prior_pair is not None and target < prior_pair[0].contracts:
            raise _fail() from None
        if prior_pair is not None and target == prior_pair[0].contracts:
            current.append(prior_pair[0])
            child_states.append(prior_pair[1])
            continue
        action = PaperBracketAction.CREATE if prior is None else PaperBracketAction.REPLACE
        proposal = PaperChildOrderProposal(
            parent_order_id=_copy_uuid(state.intent.order_id),
            child_id_sha256=child_id,
            revision_sha256=revision,
            replaces_revision_sha256=None if prior is None else prior.revision_sha256,
            oco_group_sha256=group,
            action=action,
            role=role,
            product_id=state.intent.product_id,
            side=child_side,
            contracts=target,
            kind=kind,
            stop_price=stop,
            limit_price=limit,
            trail_amount=trail,
            trigger_direction=direction,
            reduce_only=True,
        )
        current.append(proposal)
        child_states.append(
            _new_child_state(state, proposal, owned_config)
            if prior_pair is None
            else _resized_child_state(prior_pair[1], proposal)
        )
        if prior is None or prior.contracts != target:
            proposals.append(proposal)
    next_lifecycle = PaperBracketLifecycle(
        _copy_uuid(state.intent.order_id), group, tuple(current), None, tuple(child_states)
    )
    return PaperBracketUpdate(next_lifecycle, tuple(proposals))


def advance_bracket_child(
    lifecycle: PaperBracketLifecycle,
    child_id_sha256: str,
    next_state: PaperOrderState,
    *,
    executed: bool,
) -> PaperBracketUpdate:
    """Advance one runtime and cancel its OCO sibling on the first fill."""
    if type(lifecycle) is not PaperBracketLifecycle or type(child_id_sha256) is not str:
        raise _fail() from None
    lifecycle = PaperBracketLifecycle(
        lifecycle.parent_order_id,
        lifecycle.oco_group_sha256,
        lifecycle.current_children,
        lifecycle.executed_child_id_sha256,
        lifecycle.child_states,
    )
    owned_state = _copy_state(next_state)
    matching = [
        (proposal, state)
        for proposal, state in zip(lifecycle.current_children, lifecycle.child_states, strict=True)
        if proposal.child_id_sha256 == child_id_sha256
    ]
    if (
        len(matching) != 1
        or owned_state.intent.order_id != UUID(hex=child_id_sha256[:32])
        or lifecycle.executed_child_id_sha256 is not None
        and lifecycle.executed_child_id_sha256 != child_id_sha256
    ):
        raise _fail() from None
    current: list[PaperChildOrderProposal] = []
    states: list[PaperOrderState] = []
    cancellations: list[PaperChildOrderProposal] = []
    for child, child_state in zip(lifecycle.current_children, lifecycle.child_states, strict=True):
        if child.child_id_sha256 == child_id_sha256:
            current.append(child)
            states.append(owned_state)
            continue
        if executed and lifecycle.executed_child_id_sha256 is None:
            cancelled = replace(
                child,
                action=PaperBracketAction.CANCEL,
                replaces_revision_sha256=child.revision_sha256,
                revision_sha256=revision_sha256(child.child_id_sha256, 0),
            )
            current.append(cancelled)
            states.append(
                _copy_state(
                    replace(
                        child_state,
                        status=PaperOrderStatus.CANCELLED,
                        last_event_index=owned_state.last_event_index,
                        last_observed_at=owned_state.last_observed_at,
                    )
                )
            )
            cancellations.append(cancelled)
        else:
            current.append(child)
            states.append(child_state)
    next_lifecycle = PaperBracketLifecycle(
        lifecycle.parent_order_id,
        lifecycle.oco_group_sha256,
        tuple(current),
        child_id_sha256 if executed else lifecycle.executed_child_id_sha256,
        tuple(states),
    )
    return PaperBracketUpdate(next_lifecycle, tuple(cancellations))


__all__ = [
    "PaperBracketAction",
    "PaperBracketLifecycle",
    "PaperBracketUpdate",
    "advance_bracket_child",
    "bracket_is_safe_for_fill",
    "reconcile_bracket_children",
]
