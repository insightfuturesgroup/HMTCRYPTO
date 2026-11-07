"""Scalar-only construction provenance for reviewed private generics."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, cast

_FamilyKind = Literal["MODEL", "ALIAS"]
_PayloadKind = Literal["SOURCE", "PRIOR_ROW"]
_PayloadShape = Literal[
    "ENUM", "MODEL", "ANNOTATED_ALIAS", "TUPLE_ALIAS", "PRIOR_MODEL", "PRIOR_ALIAS"
]
_DependencyKind = Literal["SOURCE", "MATCHING_PRIOR_FAMILY"]
_DependencyLiteral = Literal["MISSING", "NULL", "VALUE", "FOUND", "NOT_FOUND"]


@dataclass(frozen=True, slots=True, eq=False)
class _FamilySource:
    public_module: str
    public_name: str
    source_module: str
    source_name: str
    kind: _FamilyKind


@dataclass(frozen=True, slots=True, eq=False)
class _PayloadSource:
    public_module: str
    public_name: str
    kind: _PayloadKind
    shape: _PayloadShape
    source_module: str | None
    source_name: str | None
    prior_global_row: int | None
    prior_name: str | None


@dataclass(frozen=True, slots=True, eq=False)
class _AliasDependency:
    family_module: str
    family_name: str
    literal: _DependencyLiteral
    kind: _DependencyKind
    public_module: str | None
    public_name: str | None
    source_module: str | None
    source_name: str | None
    prior_family_module: str | None
    prior_family_name: str | None


_ACCOUNT = "hmt_crypto.domain.account"
_CORE = "hmt_crypto.domain._private_generic_core"
_PRIMITIVES = "hmt_crypto.domain.private_primitives"
_WALLET = "hmt_crypto.domain.private_wallet"
_WALLET_LEAF = "hmt_crypto.domain._private_wallet_contracts"
_POSITIONS = "hmt_crypto.domain.private_positions"
_POSITIONS_LEAF = "hmt_crypto.domain._private_positions_contracts"
_ORDERS = "hmt_crypto.domain.private_orders"
_ORDER_LEAF = "hmt_crypto.domain._private_order_contracts"
_FILL_LEAF = "hmt_crypto.domain._private_fill_contracts"
_RESULT_LEAF = "hmt_crypto.domain._private_result_contracts"
_ACCOUNT_LEAF = "hmt_crypto.domain._account_contracts"

_GENERIC_FAMILY_SOURCES = (
    _FamilySource(_ACCOUNT, "ValueField", _CORE, "ValueField", "MODEL"),
    _FamilySource(_ACCOUNT, "NullableField", _CORE, "NullableField", "ALIAS"),
    _FamilySource(_ACCOUNT, "ObservedEnum", _CORE, "ObservedEnum", "MODEL"),
    _FamilySource(_ACCOUNT, "FieldPresence", _CORE, "FieldPresence", "ALIAS"),
    _FamilySource(_ACCOUNT, "Observed", _CORE, "Observed", "MODEL"),
    _FamilySource(_ORDERS, "LookupFound", _RESULT_LEAF, "LookupFound", "MODEL"),
    _FamilySource(_ORDERS, "LookupResult", _RESULT_LEAF, "LookupResult", "ALIAS"),
    _FamilySource(_ORDERS, "PrivateCursorPage", _RESULT_LEAF, "PrivateCursorPage", "MODEL"),
    _FamilySource(
        _ORDERS,
        "StrictPrivatePaginationResult",
        _RESULT_LEAF,
        "StrictPrivatePaginationResult",
        "MODEL",
    ),
    _FamilySource(
        _ORDERS,
        "DiagnosticPrivatePaginationResult",
        _RESULT_LEAF,
        "DiagnosticPrivatePaginationResult",
        "MODEL",
    ),
)


def _source_payload(
    public_module: str,
    public_name: str,
    shape: Literal["ENUM", "MODEL", "ANNOTATED_ALIAS", "TUPLE_ALIAS"],
    source_module: str,
) -> _PayloadSource:
    return _PayloadSource(
        public_module, public_name, "SOURCE", shape, source_module, public_name, None, None
    )


def _prior_payload(
    public_name: str,
    shape: Literal["PRIOR_MODEL", "PRIOR_ALIAS"],
    prior_global_row: int,
) -> _PayloadSource:
    return _PayloadSource(
        _ORDERS, public_name, "PRIOR_ROW", shape, None, None, prior_global_row, public_name
    )


_GENERIC_PAYLOAD_SOURCES = (
    _source_payload(_PRIMITIVES, "OpaqueCursor", "ANNOTATED_ALIAS", _PRIMITIVES),
    _source_payload(_WALLET, "WalletTransactionType", "ENUM", _WALLET_LEAF),
    _source_payload(_WALLET, "WalletTransactionMetadataValue", "MODEL", _WALLET_LEAF),
    _source_payload(_WALLET, "BalanceSnapshot", "MODEL", _WALLET_LEAF),
    _source_payload(_POSITIONS, "MarginMode", "ENUM", _POSITIONS_LEAF),
    _source_payload(_POSITIONS, "MarginedPositionBatch", "TUPLE_ALIAS", _POSITIONS_LEAF),
    _source_payload(_ORDERS, "OrderSide", "ENUM", _ORDER_LEAF),
    _source_payload(_ORDERS, "OrderType", "ENUM", _ORDER_LEAF),
    _source_payload(_ORDERS, "StopOrderType", "ENUM", _ORDER_LEAF),
    _source_payload(_ORDERS, "MmpLevel", "ENUM", _ORDER_LEAF),
    _source_payload(_ORDERS, "StopTriggerMethod", "ENUM", _ORDER_LEAF),
    _source_payload(_ORDERS, "TimeInForce", "ENUM", _ORDER_LEAF),
    _source_payload(_ORDERS, "OrderState", "ENUM", _ORDER_LEAF),
    _source_payload(_ORDERS, "FillContractType", "ENUM", _FILL_LEAF),
    _source_payload(_ORDERS, "FillNotionalType", "ENUM", _FILL_LEAF),
    _source_payload(_ORDERS, "FillType", "ENUM", _FILL_LEAF),
    _source_payload(_ORDERS, "FillRole", "ENUM", _FILL_LEAF),
    _source_payload(_ACCOUNT, "ExactBool", "ANNOTATED_ALIAS", _ACCOUNT_LEAF),
    _source_payload(_PRIMITIVES, "PositivePrivateDecimal", "ANNOTATED_ALIAS", _PRIMITIVES),
    _prior_payload("ObservedMmpLevel", "PRIOR_MODEL", 11),
    _prior_payload("ObservedStopTriggerMethod", "PRIOR_MODEL", 12),
    _prior_payload("ObservedTimeInForce", "PRIOR_MODEL", 13),
    _source_payload(_PRIMITIVES, "PrivateDecimal", "ANNOTATED_ALIAS", _PRIMITIVES),
    _source_payload(_PRIMITIVES, "ObservedDeltaClientOrderId", "ANNOTATED_ALIAS", _PRIMITIVES),
    _source_payload(_ORDERS, "FillMetadataValue", "MODEL", _FILL_LEAF),
    _source_payload(_POSITIONS, "LatestPosition", "MODEL", _POSITIONS_LEAF),
    _source_payload(_ORDERS, "Order", "MODEL", _ORDER_LEAF),
    _source_payload(_WALLET, "WalletTransaction", "MODEL", _WALLET_LEAF),
    _source_payload(_ORDERS, "Fill", "MODEL", _FILL_LEAF),
    _prior_payload("LatestPositionLookupResult", "PRIOR_ALIAS", 37),
    _prior_payload("OrderLookupResult", "PRIOR_ALIAS", 38),
    _source_payload(_ORDERS, "Leverage", "MODEL", _RESULT_LEAF),
)


def _source_dependency(
    family_module: str,
    family_name: str,
    literal: _DependencyLiteral,
    public_name: str,
    source_module: str,
) -> _AliasDependency:
    return _AliasDependency(
        family_module,
        family_name,
        literal,
        "SOURCE",
        family_module,
        public_name,
        source_module,
        public_name,
        None,
        None,
    )


def _prior_dependency(
    family_module: str,
    family_name: str,
    literal: _DependencyLiteral,
    prior_family_name: str,
) -> _AliasDependency:
    return _AliasDependency(
        family_module,
        family_name,
        literal,
        "MATCHING_PRIOR_FAMILY",
        None,
        None,
        None,
        None,
        family_module,
        prior_family_name,
    )


_GENERIC_ALIAS_DEPENDENCIES = (
    _source_dependency(_ACCOUNT, "FieldPresence", "MISSING", "MissingField", _ACCOUNT_LEAF),
    _source_dependency(_ACCOUNT, "FieldPresence", "NULL", "NullField", _ACCOUNT_LEAF),
    _prior_dependency(_ACCOUNT, "FieldPresence", "VALUE", "ValueField"),
    _source_dependency(_ACCOUNT, "NullableField", "NULL", "NullField", _ACCOUNT_LEAF),
    _prior_dependency(_ACCOUNT, "NullableField", "VALUE", "ValueField"),
    _prior_dependency(_ORDERS, "LookupResult", "FOUND", "LookupFound"),
    _source_dependency(_ORDERS, "LookupResult", "NOT_FOUND", "LookupNotFound", _RESULT_LEAF),
)

_GENERIC_SOURCE_MODULES = tuple(
    dict.fromkeys(
        value
        for records in (
            _GENERIC_FAMILY_SOURCES,
            _GENERIC_PAYLOAD_SOURCES,
            _GENERIC_ALIAS_DEPENDENCIES,
        )
        for record in records
        for value in (record.source_module, record.public_module)
        if type(value) is str
    )
)


def _family_source_for(public_module: str, public_name: str) -> _FamilySource | None:
    if type(public_module) is not str or type(public_name) is not str:
        return None
    for record in _GENERIC_FAMILY_SOURCES:
        if record.public_module == public_module and record.public_name == public_name:
            return record
    return None


def _payload_source_for(public_name: str) -> _PayloadSource | None:
    if type(public_name) is not str:
        return None
    for record in _GENERIC_PAYLOAD_SOURCES:
        if record.public_name == public_name:
            return record
    return None


def _alias_dependencies_for(
    family_module: str, family_name: str
) -> tuple[_AliasDependency, ...] | None:
    if type(family_module) is not str or type(family_name) is not str:
        return None
    found = tuple(
        record
        for record in _GENERIC_ALIAS_DEPENDENCIES
        if record.family_module == family_module and record.family_name == family_name
    )
    return found or None


def _exact_rows(value: object) -> tuple[tuple[str, str, str, str, str, str], ...] | None:
    if type(value) is not tuple or tuple.__len__(value) != 51:
        return None
    rows: list[tuple[str, str, str, str, str, str]] = []
    for index in range(tuple.__len__(value)):
        row = tuple.__getitem__(value, index)
        if type(row) is not tuple or tuple.__len__(row) != 6:
            return None
        values = tuple(tuple.__getitem__(row, item) for item in range(6))
        if any(type(item) is not str for item in values):
            return None
        rows.append(cast(tuple[str, str, str, str, str, str], values))
    return tuple(rows)


def _record_fingerprint_is_valid() -> bool:
    directories = (
        _GENERIC_FAMILY_SOURCES,
        _GENERIC_PAYLOAD_SOURCES,
        _GENERIC_ALIAS_DEPENDENCIES,
    )
    specifications = (
        (
            _FamilySource,
            ("public_module", "public_name", "source_module", "source_name", "kind"),
        ),
        (
            _PayloadSource,
            (
                "public_module",
                "public_name",
                "kind",
                "shape",
                "source_module",
                "source_name",
                "prior_global_row",
                "prior_name",
            ),
        ),
        (
            _AliasDependency,
            (
                "family_module",
                "family_name",
                "literal",
                "kind",
                "public_module",
                "public_name",
                "source_module",
                "source_name",
                "prior_family_module",
                "prior_family_name",
            ),
        ),
    )
    if type(directories) is not tuple or type(specifications) is not tuple:
        return False
    scalars: list[tuple[object, ...]] = []
    for index in range(3):
        records = tuple.__getitem__(directories, index)
        record_type, field_names = tuple.__getitem__(specifications, index)
        if type(records) is not tuple or type(field_names) is not tuple:
            return False
        for record_index in range(tuple.__len__(records)):
            record = tuple.__getitem__(records, record_index)
            if type(record) is not record_type:
                return False
            try:
                values = tuple(
                    object.__getattribute__(record, tuple.__getitem__(field_names, field_index))
                    for field_index in range(tuple.__len__(field_names))
                )
            except Exception:
                return False
            if any(
                value is not None and type(value) is not str and type(value) is not int
                for value in values
            ):
                return False
            scalars.append(values)
    fingerprint = sha256(repr(tuple(scalars)).encode()).hexdigest()
    return fingerprint == "12cbefdd237b05956d9dba031f4572e8e7d9d5b51a2a6c25aad3bd492727d18c"


def _source_directory_is_valid(value: object) -> bool:
    rows = _exact_rows(value)
    if rows is None or not _record_fingerprint_is_valid():
        return False
    expected_owners = (_ACCOUNT,) * 2 + (_WALLET,) * 4 + (_POSITIONS,) * 2 + (_ORDERS,) * 43
    if tuple(row[0] for row in rows) != expected_owners:
        return False
    family_order: list[tuple[str, str]] = []
    payload_order: list[str] = []
    for row in rows:
        family_key = (row[2], row[3])
        if family_key not in family_order:
            family_order.append(family_key)
        if row[4] not in payload_order:
            payload_order.append(row[4])
    if tuple((item.public_module, item.public_name) for item in _GENERIC_FAMILY_SOURCES) != tuple(
        family_order
    ) or tuple(item.public_name for item in _GENERIC_PAYLOAD_SOURCES) != tuple(payload_order):
        return False
    for index, row in enumerate(rows):
        family = _family_source_for(row[2], row[3])
        payload = _payload_source_for(row[4])
        if family is None or payload is None:
            return False
        expected_kind = "model" if family.kind == "MODEL" else "alias"
        if row[1] != expected_kind:
            return False
        if payload.kind == "PRIOR_ROW":
            prior = payload.prior_global_row
            if prior is None or prior >= index:
                return False
            prior_shape = "PRIOR_MODEL" if rows[prior][1] == "model" else "PRIOR_ALIAS"
            if rows[prior][5] != payload.prior_name or payload.shape != prior_shape:
                return False
    expected_dependencies = (
        (_ACCOUNT, "FieldPresence", "MISSING"),
        (_ACCOUNT, "FieldPresence", "NULL"),
        (_ACCOUNT, "FieldPresence", "VALUE"),
        (_ACCOUNT, "NullableField", "NULL"),
        (_ACCOUNT, "NullableField", "VALUE"),
        (_ORDERS, "LookupResult", "FOUND"),
        (_ORDERS, "LookupResult", "NOT_FOUND"),
    )
    if (
        tuple(
            (item.family_module, item.family_name, item.literal)
            for item in _GENERIC_ALIAS_DEPENDENCIES
        )
        != expected_dependencies
    ):
        return False
    family_keys = tuple(family_order)
    for dependency in _GENERIC_ALIAS_DEPENDENCIES:
        if (dependency.family_module, dependency.family_name) not in family_keys:
            return False
        if (
            dependency.kind == "MATCHING_PRIOR_FAMILY"
            and (
                dependency.prior_family_module,
                dependency.prior_family_name,
            )
            not in family_keys
        ):
            return False
    return True


def _invalid_source_directory() -> None:
    raise RuntimeError("private generic source directory invalid") from None


def _validate_source_directory(
    row_scalars: tuple[tuple[object, object, object, object, object, object], ...],
) -> None:
    if _source_directory_is_valid(row_scalars):
        return
    del row_scalars
    _invalid_source_directory()
