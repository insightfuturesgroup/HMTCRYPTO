"""Exact-tuple admission regressions for reviewed future generic payloads."""

from __future__ import annotations

import ast
import gc
import importlib
import inspect
import sys
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import ModuleType, SimpleNamespace
from uuid import UUID

import pytest
from delta_private_model_test_support import (
    account_api,
    reachable_exceptions,
    valid_account,
)
from pydantic import ValidationError

pytestmark = pytest.mark.unit

_POSITIONS = "hmt_crypto.domain.private_positions"
_POSITIONS_LEAF = "hmt_crypto.domain._private_positions_contracts"
_ORDERS = "hmt_crypto.domain.private_orders"


@contextmanager
def _future_positions_world() -> Iterator[SimpleNamespace]:
    """Install the two reviewed positions rows without creating A2.2 files."""
    from hmt_crypto.domain.private_primitives import ExactStrEnum, PrivateBoundaryModel

    api = account_api()
    saved = {
        name: (dict.__contains__(sys.modules, name), dict.get(sys.modules, name))
        for name in (_POSITIONS_LEAF, _POSITIONS, _ORDERS)
    }
    leaf = ModuleType(_POSITIONS_LEAF)
    positions = ModuleType(_POSITIONS)

    class MarginMode(ExactStrEnum):
        __module__ = _POSITIONS

        CROSS = "CROSS"

    class MarginedPosition(PrivateBoundaryModel):
        __module__ = _POSITIONS

        product_id: int

    MarginMode.__qualname__ = "MarginMode"
    MarginedPosition.__qualname__ = "MarginedPosition"
    batch = tuple[MarginedPosition, ...]
    for module in (leaf, positions):
        module.MarginMode = MarginMode
        module.MarginedPosition = MarginedPosition
        module.MarginedPositionBatch = batch
    positions.ObservedEnum = api.ObservedEnum
    positions.Observed = api.Observed

    try:
        sys.modules.pop(_ORDERS, None)
        sys.modules[_POSITIONS_LEAF] = leaf
        sys.modules[_POSITIONS] = positions
        observed_mode = api.module._declare_reviewed_model_candidate(
            api.ObservedEnum,
            MarginMode,
            canonical_name="ObservedMarginMode",
            owner_module=_POSITIONS,
        )
        positions.ObservedMarginMode = observed_mode
        observed_batch = api.module._declare_reviewed_model_candidate(
            api.Observed,
            batch,
            canonical_name="ObservedMarginedPositions",
            owner_module=_POSITIONS,
        )
        positions.ObservedMarginedPositions = observed_batch
        record = api.module._ReviewedGenericSpecialization
        positions._POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS = (
            record(api.ObservedEnum, MarginMode, observed_mode),
            record(api.Observed, batch, observed_batch),
        )
        api.module._rebuild_reviewed_model_candidate(observed_mode)
        api.module._rebuild_reviewed_model_candidate(observed_batch)
        yield SimpleNamespace(
            api=api,
            positions=positions,
            batch=batch,
            MarginMode=MarginMode,
            MarginedPosition=MarginedPosition,
            ObservedMarginMode=observed_mode,
            ObservedMarginedPositions=observed_batch,
        )
    finally:
        for name, (present, module) in saved.items():
            if present:
                sys.modules[name] = module
            else:
                sys.modules.pop(name, None)


def _context(world: SimpleNamespace) -> object:
    from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

    requested = datetime(2026, 11, 5, 10, 30, tzinfo=UTC)
    return world.api.ObservationContext(
        account=valid_account(world.api),
        endpoint_id=PrivateRouteId.MARGINED_POSITIONS,
        correlation_id=UUID("00000000-0000-0000-0000-000000000099"),
        requested_at=requested,
        received_at=requested,
        source="delta_rest",
    )


def _valid_observation(world: SimpleNamespace) -> object:
    position = world.MarginedPosition(product_id=7)
    return world.ObservedMarginedPositions(context=_context(world), value=(position,))


class _HostileTuple(tuple[object, ...]):
    calls: list[str] = []

    def __iter__(self) -> Iterator[object]:
        type(self).calls.append("iter")
        raise AssertionError("hostile tuple iteration ran")

    def __len__(self) -> int:
        type(self).calls.append("len")
        raise AssertionError("hostile tuple length ran")

    def __getitem__(self, index: object) -> object:
        del index
        type(self).calls.append("getitem")
        raise AssertionError("hostile tuple indexing ran")

    def __repr__(self) -> str:
        type(self).calls.append("repr")
        raise AssertionError("hostile tuple repr ran")

    def __eq__(self, other: object) -> bool:
        del other
        type(self).calls.append("eq")
        raise AssertionError("hostile tuple equality ran")

    def __ne__(self, other: object) -> bool:
        del other
        type(self).calls.append("ne")
        raise AssertionError("hostile tuple inequality ran")


def test_reviewed_payload_exact_tuple_policy_is_identity_derived_and_tristate() -> None:
    """Only a reviewed specialization whose exact payload is tuple-alias gets the policy."""
    registry = importlib.import_module("hmt_crypto.domain._private_generic_registry")
    with _future_positions_world() as world:
        assert (
            registry._reviewed_payload_requires_exact_tuple(
                world.ObservedMarginedPositions, world.api.Observed
            )
            is True
        )
        assert (
            registry._reviewed_payload_requires_exact_tuple(
                world.api.OpaqueCursorValueField, world.api.ValueField
            )
            is False
        )
        assert registry._reviewed_payload_requires_exact_tuple(object(), world.api.Observed) is None


def test_reviewed_tuple_payload_accepts_an_exact_tuple() -> None:
    """The exact canonical tuple remains valid for the reviewed future specialization."""
    with _future_positions_world() as world:
        observed = _valid_observation(world)
        assert type(observed.value) is tuple
        assert observed.value[0].product_id == 7


@pytest.mark.parametrize("entrypoint", ["constructor", "model_validate"])
def test_future_observed_enum_mismatch_is_input_free(entrypoint: str) -> None:
    """The A2.1 future seam sanitizes a known/unknown enum mismatch itself."""
    raw = "future_margin_canary"
    with _future_positions_world() as world:
        payload = {"raw": raw, "known": world.MarginMode.CROSS}
        with pytest.raises(ValidationError) as captured:
            if entrypoint == "constructor":
                world.ObservedMarginMode(**payload)
            else:
                world.ObservedMarginMode.model_validate(payload)
        expected = (
            ("private_observed_enum_value", "invalid observed enum")
            if entrypoint == "constructor"
            else ("private_model_value", "invalid private model value")
        )
        assert captured.value.errors(include_url=False) == [
            {
                "type": expected[0],
                "loc": (),
                "msg": expected[1],
                "input": None,
            }
        ]
        for reachable in reachable_exceptions(captured.value):
            rendered = f"{reachable!s} {reachable!r} {reachable.args!r}"
            if isinstance(reachable, ValidationError):
                details = reachable.errors(include_url=False)
                rendered += repr(details)
                assert all(detail.get("input") is None for detail in details)
            assert raw not in rendered


def test_hostile_tuple_subclass_rejects_before_every_callback() -> None:
    """Pydantic cannot normalize a hostile tuple subclass into reviewed state."""
    _HostileTuple.calls = []
    with _future_positions_world() as world:
        raw = _HostileTuple((world.MarginedPosition(product_id=7),))
        with pytest.raises(ValidationError) as captured:
            world.ObservedMarginedPositions.model_validate(
                {"context": _context(world), "value": raw}
            )
        assert all(detail.get("input") is None for detail in captured.value.errors())
    assert _HostileTuple.calls == []


@pytest.mark.parametrize(
    "operation",
    ("revalidate", "copy", "python-dump", "json-dump", "json-encode"),
)
def test_corrupted_stored_tuple_subclass_fails_every_owned_boundary(operation: str) -> None:
    """Revalidation, copying, and all model encoders reject corrupted tuple state."""
    _HostileTuple.calls = []
    with _future_positions_world() as world:
        observed = _valid_observation(world)
        state = object.__getattribute__(observed, "__dict__")
        dict.__setitem__(state, "value", _HostileTuple(tuple.__new__(tuple)))
        actions: dict[str, Callable[[], object]] = {
            "revalidate": lambda: world.ObservedMarginedPositions.model_validate(observed),
            "copy": lambda: observed.model_copy(),
            "python-dump": lambda: observed.model_dump(),
            "json-dump": lambda: observed.model_dump(mode="json"),
            "json-encode": lambda: observed.model_dump_json(),
        }
        with pytest.raises(ValidationError) as captured:
            actions[operation]()
        assert all(detail.get("input") is None for detail in captured.value.errors())
        assert "stored-tuple-canary" not in repr(captured.value.args)
    assert _HostileTuple.calls == []


def test_rejected_tuple_subclass_is_not_retained() -> None:
    """The fixed generic error owns no object stored inside a rejected tuple subclass."""
    _HostileTuple.calls = []

    with _future_positions_world() as world:
        position = world.MarginedPosition(product_id=7)
        position_ref = weakref.ref(position)
        raw = _HostileTuple((position,))
        caught: ValidationError | None = None
        try:
            world.ObservedMarginedPositions.model_validate(
                {"context": _context(world), "value": raw}
            )
        except ValidationError as error:
            caught = error
        else:
            raise AssertionError("tuple subclass unexpectedly accepted")
        assert caught is not None
        del position, raw, caught
        gc.collect()
        assert position_ref() is None
    assert _HostileTuple.calls == []


def test_reconstructed_and_unreviewed_tuple_aliases_do_not_gain_tuple_policy() -> None:
    """Structural alias equality cannot substitute for a reviewed payload identity."""
    registry = importlib.import_module("hmt_crypto.domain._private_generic_registry")
    with _future_positions_world() as world:
        reconstructed = tuple[world.MarginedPosition, ...]
        assert reconstructed == world.batch and reconstructed is not world.batch
        rejected = world.api.Observed[reconstructed]
        unreviewed = world.api.Observed[tuple[int, ...]]
        assert rejected is world.api.module._RejectedPrivateGenericSpecialization
        assert unreviewed is world.api.module._RejectedPrivateGenericSpecialization
        assert registry._reviewed_payload_requires_exact_tuple(rejected, world.api.Observed) is None
        assert (
            registry._reviewed_payload_requires_exact_tuple(unreviewed, world.api.Observed) is None
        )


def test_exact_tuple_policy_contains_no_payload_or_owner_name_special_case() -> None:
    """The policy implementation derives category only after registry identity resolution."""
    registry = importlib.import_module("hmt_crypto.domain._private_generic_registry")
    tree = ast.parse(inspect.getsource(registry._reviewed_payload_requires_exact_tuple))
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and type(node.value) is str
    }
    assert not literals & {
        "MarginedPositionBatch",
        "ObservedMarginedPositions",
        "hmt_crypto.domain.private_positions",
        "hmt_crypto.domain._private_positions_contracts",
    }
