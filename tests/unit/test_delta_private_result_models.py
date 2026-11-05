"""Lookup, leverage, and paginated private result contracts."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from delta_private_model_test_support import account_api, valid_account
from pydantic import TypeAdapter, ValidationError

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module", autouse=True)
def _release_later_owner_modules() -> Iterator[None]:
    """Keep this later-owner contract module neutral to earlier-owner suites."""
    yield
    import sys

    domain = sys.modules.get("hmt_crypto.domain")
    for name in (
        "hmt_crypto.domain.private_orders",
        "hmt_crypto.domain._private_order_contracts",
        "hmt_crypto.domain._private_fill_contracts",
        "hmt_crypto.domain._private_result_contracts",
        "hmt_crypto.domain.private_wallet",
        "hmt_crypto.domain._private_wallet_contracts",
        "hmt_crypto.domain.private_positions",
        "hmt_crypto.domain._private_positions_contracts",
    ):
        sys.modules.pop(name, None)
        if domain is not None:
            domain.__dict__.pop(name.rsplit(".", 1)[1], None)


def _orders() -> object:
    from hmt_crypto.domain import private_orders

    return private_orders


def _context() -> object:
    from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

    api = account_api()
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    return api.ObservationContext(
        account=valid_account(api),
        endpoint_id=PrivateRouteId.OPEN_ORDERS,
        correlation_id=UUID("00000000-0000-0000-0000-000000000301"),
        requested_at=now,
        received_at=now,
        source="delta_rest",
    )


def _provenance(mode: object, count: int = 1) -> object:
    from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

    api = account_api()
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    account = valid_account(api)
    correlations = tuple(UUID(int=index + 1) for index in range(count))
    return api.PrivatePaginationProvenance(
        account=account,
        endpoint_id=PrivateRouteId.OPEN_ORDERS,
        first_requested_at=now,
        last_received_at=now,
        page_correlation_ids=correlations,
        credential_version=account.credential_version,
        policy_version="delta-docs-2026-11-04-private-v1",
        mode=mode,
    )


def _latest() -> object:
    from hmt_crypto.domain.private_positions import LatestPosition

    return LatestPosition(product_id=27, signed_contracts=-2, entry_price=Decimal("100"))


def test_leverage_result_keeps_positive_decimal_and_response_encoding() -> None:
    """Catch leverage coercion or observation encoding becoming write authority."""
    orders = _orders()
    value = orders.Leverage(
        leverage=Decimal("10"),
        leverage_encoding=orders.LeverageEncoding.JSON_INTEGER,
        order_margin=Decimal("0"),
        product_id=27,
    )
    assert value.leverage == Decimal("10")
    assert value.leverage_encoding is orders.LeverageEncoding.JSON_INTEGER
    assert tuple(member.value for member in orders.LeverageEncoding) == (
        "DECIMAL_STRING",
        "JSON_INTEGER",
    )
    for change in (
        {"leverage": Decimal("0")},
        {"leverage": Decimal("-1")},
        {"order_margin": Decimal("-1")},
        {"leverage": 10.0},
    ):
        with pytest.raises(ValidationError):
            orders.Leverage.model_validate(value.model_dump() | change)


def test_margin_mode_and_acknowledgement_are_narrow_results() -> None:
    """Catch coercive success or mutation-response fields widening the contract."""
    from hmt_crypto.domain import private_positions

    orders = _orders()
    observed = private_positions.ObservedMarginMode(
        raw="cross", known=private_positions.MarginMode.CROSS
    )
    result = orders.MarginModeResult(id="12345", margin_mode=observed)
    assert result.margin_mode.known is private_positions.MarginMode.CROSS
    assert orders.MutationAcknowledgement(success=True).success is True
    for invalid in (False, 1, "true"):
        with pytest.raises(ValidationError):
            orders.MutationAcknowledgement(success=invalid)
    with pytest.raises(ValidationError):
        orders.MutationAcknowledgement(success=True, message="ok")


def test_lookup_found_is_disjoint_and_not_found_stays_closed() -> None:
    """Catch nullable collapse or an invented generic absence code."""
    orders = _orders()
    adapter = TypeAdapter(orders.LatestPositionLookupResult)
    found = adapter.validate_python({"kind": "FOUND", "value": _latest()})
    assert type(found) is orders.LatestPositionLookupFound
    assert found.value.product_id == 27
    assert tuple(orders.ReviewedAbsenceCode) == ()
    with pytest.raises(ValidationError):
        orders.LookupNotFound(kind="NOT_FOUND", absence_code="UNAVAILABLE")
    for invalid in (
        {"kind": "FOUND", "value": _latest(), "absence_code": "x"},
        {"kind": "NOT_FOUND", "absence_code": "UNAVAILABLE"},
        None,
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


def test_cursor_page_requires_exact_tuple_and_explicit_nullable_cursors() -> None:
    """Catch item-list normalization or silently missing pagination metadata."""
    orders = _orders()
    page = orders.PrivateCursorPage[object]
    assert page is account_api().module._RejectedPrivateGenericSpecialization
    valid = orders.OrderCursorPage(
        context=_context(),
        items=(),
        after={"kind": "NULL"},
        before={"kind": "NULL"},
    )
    assert type(valid.items) is tuple
    for field in ("after", "before"):
        payload = valid.model_dump()
        payload.pop(field)
        with pytest.raises(ValidationError):
            orders.OrderCursorPage.model_validate(payload)

    class TupleSubclass(tuple[object, ...]):
        pass

    for invalid in ([], TupleSubclass(())):
        with pytest.raises(ValidationError):
            orders.OrderCursorPage(
                context=_context(),
                items=invalid,
                after={"kind": "NULL"},
                before={"kind": "NULL"},
            )


@pytest.mark.parametrize("operation", ("copy", "python", "json"))
def test_corrupted_page_items_cannot_cross_owned_boundaries(operation: str) -> None:
    """Catch stored list corruption reaching copy or serializers."""
    orders = _orders()
    page = orders.OrderCursorPage(
        context=_context(),
        items=(),
        after={"kind": "NULL"},
        before={"kind": "NULL"},
    )
    actions = {
        "copy": page.model_copy,
        "python": page.model_dump,
        "json": page.model_dump_json,
    }
    object.__getattribute__(page, "__dict__")["items"] = []
    with pytest.raises(ValidationError):
        actions[operation]()


def test_strict_pagination_requires_strict_mode_and_correlation_count() -> None:
    """Catch incomplete or mismatched provenance being represented as strict."""
    api = account_api()
    orders = _orders()
    result = orders.StrictOrderPaginationResult(
        kind="STRICT",
        items=(),
        complete=True,
        page_count=1,
        provenance=_provenance(api.CollectionMode.STRICT),
    )
    assert result.complete is True
    assert not hasattr(result, "truncation_reason")
    for change in (
        {"complete": False},
        {"page_count": 2},
        {"provenance": _provenance(api.CollectionMode.DIAGNOSTIC)},
    ):
        with pytest.raises(ValidationError):
            orders.StrictOrderPaginationResult.model_validate(result.model_dump() | change)


@pytest.mark.parametrize(
    ("complete", "reason", "valid"),
    (
        (True, None, True),
        (False, "PAGE_LIMIT", True),
        (False, "ITEM_LIMIT", True),
        (True, "PAGE_LIMIT", False),
        (False, None, False),
    ),
)
def test_diagnostic_pagination_has_exact_completion_truth_table(
    complete: bool, reason: str | None, valid: bool
) -> None:
    """Catch contradictory diagnostic completion and truncation evidence."""
    api = account_api()
    orders = _orders()
    kwargs = {
        "kind": "DIAGNOSTIC",
        "items": (),
        "complete": complete,
        "truncation_reason": None if reason is None else api.TruncationReason(reason),
        "page_count": 1,
        "provenance": _provenance(api.CollectionMode.DIAGNOSTIC),
    }
    if valid:
        assert orders.DiagnosticFillPaginationResult(**kwargs).complete is complete
    else:
        with pytest.raises(ValidationError):
            orders.DiagnosticFillPaginationResult(**kwargs)


@pytest.mark.parametrize(
    "model_name",
    (
        "StrictWalletTransactionPaginationResult",
        "StrictOrderPaginationResult",
        "StrictFillPaginationResult",
        "DiagnosticWalletTransactionPaginationResult",
        "DiagnosticOrderPaginationResult",
        "DiagnosticFillPaginationResult",
    ),
)
def test_all_pagination_result_items_reject_mutable_or_subclass_tuples(
    model_name: str,
) -> None:
    """Catch one specialization omitting the shared local exact-tuple gate."""
    api = account_api()
    orders = _orders()
    model = getattr(orders, model_name)
    diagnostic = model_name.startswith("Diagnostic")
    kwargs = {
        "kind": "DIAGNOSTIC" if diagnostic else "STRICT",
        "items": [],
        "complete": True,
        "page_count": 1,
        "provenance": _provenance(
            api.CollectionMode.DIAGNOSTIC if diagnostic else api.CollectionMode.STRICT
        ),
    }
    if diagnostic:
        kwargs["truncation_reason"] = None
    with pytest.raises(ValidationError):
        model(**kwargs)


@pytest.mark.parametrize(
    ("model_name", "construct_fails"),
    (
        ("Leverage", False),
        ("MarginModeResult", False),
        ("MutationAcknowledgement", False),
        ("LatestPositionLookupFound", True),
        ("OrderCursorPage", True),
        ("StrictOrderPaginationResult", True),
        ("DiagnosticOrderPaginationResult", True),
    ),
)
def test_result_models_inherit_closed_boundary_policy_and_reject_construct(
    model_name: str, construct_fails: bool
) -> None:
    """Catch one concrete result model weakening A1d or construct admission."""
    model = getattr(_orders(), model_name)
    for key, expected in {
        "extra": "forbid",
        "frozen": True,
        "hide_input_in_errors": True,
        "revalidate_instances": "always",
        "strict": True,
        "validate_default": True,
    }.items():
        assert model.model_config[key] == expected
    if construct_fails:
        with pytest.raises(ValidationError):
            model.model_construct()
    else:
        forged = model.model_construct()
        with pytest.raises(ValidationError):
            forged.model_dump()


def test_json_integer_leverage_requires_integral_decimal_evidence() -> None:
    """Catch a fractional response being mislabeled as observed JSON integer leverage."""
    orders = _orders()
    common = {"order_margin": Decimal("0"), "product_id": 27}
    assert orders.Leverage(
        leverage=Decimal("1.5"),
        leverage_encoding=orders.LeverageEncoding.DECIMAL_STRING,
        **common,
    ).leverage == Decimal("1.5")
    with pytest.raises(ValidationError):
        orders.Leverage(
            leverage=Decimal("1.5"),
            leverage_encoding=orders.LeverageEncoding.JSON_INTEGER,
            **common,
        )


@pytest.mark.parametrize("after_value", (None, "after:cursor"))
@pytest.mark.parametrize("before_value", (None, "before:cursor"))
def test_cursor_page_preserves_all_four_explicit_cursor_combinations(
    after_value: str | None, before_value: str | None
) -> None:
    """Catch NULL/VALUE cursors being collapsed or inferred from each other."""
    orders = _orders()

    def branch(value: str | None) -> dict[str, object]:
        return {"kind": "NULL"} if value is None else {"kind": "VALUE", "value": value}

    page = orders.OrderCursorPage(
        context=_context(), items=(), after=branch(after_value), before=branch(before_value)
    )
    assert page.after.kind == ("NULL" if after_value is None else "VALUE")
    assert page.before.kind == ("NULL" if before_value is None else "VALUE")


def test_lookup_outer_union_rejects_subclasses_and_corrupted_found_state() -> None:
    """Catch forged outer containers, model subclasses, or corrupt tags crossing lookup."""
    orders = _orders()
    adapter = TypeAdapter(orders.LatestPositionLookupResult)

    class Root(dict[str, object]):
        pass

    class FoundSubclass(orders.LatestPositionLookupFound):
        pass

    valid = {"kind": "FOUND", "value": _latest()}
    with pytest.raises(ValidationError):
        adapter.validate_python(Root(valid))
    with pytest.raises(ValidationError):
        FoundSubclass(**valid)
    found = orders.LatestPositionLookupFound(**valid)
    object.__getattribute__(found, "__dict__")["kind"] = "NOT_FOUND"
    with pytest.raises(ValidationError):
        adapter.validate_python(found)


@pytest.mark.parametrize(
    ("model_name", "kind", "mode"),
    (
        ("StrictOrderPaginationResult", "DIAGNOSTIC", "STRICT"),
        ("StrictOrderPaginationResult", "STRICT", "DIAGNOSTIC"),
        ("DiagnosticOrderPaginationResult", "STRICT", "DIAGNOSTIC"),
        ("DiagnosticOrderPaginationResult", "DIAGNOSTIC", "STRICT"),
    ),
)
def test_pagination_kind_and_provenance_mode_are_independently_exact(
    model_name: str, kind: str, mode: str
) -> None:
    """Catch kind or provenance mode being inferred from the other field."""
    api = account_api()
    model = getattr(_orders(), model_name)
    diagnostic = model_name.startswith("Diagnostic")
    kwargs = {
        "kind": kind,
        "items": (),
        "complete": True,
        "page_count": 1,
        "provenance": _provenance(api.CollectionMode(mode)),
    }
    if diagnostic:
        kwargs["truncation_reason"] = None
    with pytest.raises(ValidationError):
        model(**kwargs)


@pytest.mark.parametrize("coordinate_tag", (False, True))
def test_lookup_not_found_public_branch_and_coordinated_alias_tag_stay_source_bound(
    coordinate_tag: bool,
) -> None:
    """Catch public and alias-tag rewrites inventing an unavailable NOT_FOUND branch."""
    import importlib

    orders = _orders()
    registry = importlib.import_module("hmt_crypto.domain._private_generic_registry")
    alias = orders.LatestPositionLookupResult
    original_branch = orders.LookupNotFound
    original_tags = alias.__private_tags__

    class ForgedNotFound(original_branch):
        invented: str

    orders.LookupNotFound = ForgedNotFound
    if coordinate_tag:
        alias.__private_tags__ = (original_tags[0], ("NOT_FOUND", ForgedNotFound))
    try:
        assert registry._load_reviewed_rows() is None
    finally:
        orders.LookupNotFound = original_branch
        alias.__private_tags__ = original_tags
    assert len(registry._load_reviewed_rows()) == 51
    with pytest.raises(ValidationError):
        orders.LookupNotFound(kind="NOT_FOUND", absence_code="invented")
