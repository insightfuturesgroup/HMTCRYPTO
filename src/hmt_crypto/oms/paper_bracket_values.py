"""Canonical scalar and proposal copying for protective bracket state."""

from __future__ import annotations

from decimal import Decimal

from hmt_crypto.oms.paper_identity import child_sha256, revision_sha256
from hmt_crypto.oms.paper_models import (
    PaperBracketAction,
    PaperBracketRole,
    PaperChildOrderProposal,
    PaperSide,
    PaperSimulationError,
    PaperTriggerDirection,
)
from hmt_crypto.oms.paper_values import _copy_decimal, _copy_uuid, _exact_int

_ACTIONS = tuple(PaperBracketAction)
_ROLES = tuple(PaperBracketRole)


def _fail() -> PaperSimulationError:
    return PaperSimulationError()


def _digest(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _fail() from None
    return value


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _copy_decimal(value, positive=True)


def _copy_proposal(value: object) -> PaperChildOrderProposal:
    if type(value) is not PaperChildOrderProposal:
        raise _fail() from None
    try:
        proposal = PaperChildOrderProposal(
            parent_order_id=_copy_uuid(object.__getattribute__(value, "parent_order_id")),
            child_id_sha256=_digest(object.__getattribute__(value, "child_id_sha256")),
            revision_sha256=_digest(object.__getattribute__(value, "revision_sha256")),
            replaces_revision_sha256=(
                None
                if object.__getattribute__(value, "replaces_revision_sha256") is None
                else _digest(object.__getattribute__(value, "replaces_revision_sha256"))
            ),
            oco_group_sha256=_digest(object.__getattribute__(value, "oco_group_sha256")),
            action=object.__getattribute__(value, "action"),
            role=object.__getattribute__(value, "role"),
            product_id=_exact_int(object.__getattribute__(value, "product_id"), minimum=1),
            side=object.__getattribute__(value, "side"),
            contracts=_exact_int(object.__getattribute__(value, "contracts"), minimum=1),
            kind=object.__getattribute__(value, "kind"),
            stop_price=_optional_decimal(object.__getattribute__(value, "stop_price")),
            limit_price=_optional_decimal(object.__getattribute__(value, "limit_price")),
            trail_amount=_optional_decimal(object.__getattribute__(value, "trail_amount")),
            trigger_direction=object.__getattribute__(value, "trigger_direction"),
            reduce_only=object.__getattribute__(value, "reduce_only"),
        )
    except PaperSimulationError:
        raise
    except Exception:
        raise _fail() from None
    if (
        not any(proposal.action is item for item in _ACTIONS)
        or not any(proposal.role is item for item in _ROLES)
        or proposal.side not in (PaperSide.BUY, PaperSide.SELL)
        or proposal.trigger_direction not in tuple(PaperTriggerDirection)
        or proposal.reduce_only is not True
        or (proposal.action is PaperBracketAction.CREATE)
        is not (proposal.replaces_revision_sha256 is None)
        or child_sha256(proposal.oco_group_sha256, proposal.role) != proposal.child_id_sha256
        or proposal.revision_sha256
        != revision_sha256(
            proposal.child_id_sha256,
            0 if proposal.action is PaperBracketAction.CANCEL else proposal.contracts,
        )
    ):
        raise _fail() from None
    return proposal


def _same_decimal(left: Decimal | None, right: Decimal | None) -> bool:
    return (left is None and right is None) or (
        left is not None and right is not None and left.as_tuple() == right.as_tuple()
    )


__all__: list[str] = []
