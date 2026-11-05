"""Construction-owned fill contracts exposed by the private orders facade."""

# ruff: noqa: E501 -- immutable source inventories stay within the reviewed module ceiling.

from __future__ import annotations

import re
import sys as _sys  # noqa: F401 -- verified through an owned module snapshot.
from types import FunctionType, ModuleType, UnionType  # noqa: F401
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from pydantic import ConfigDict, model_validator

from hmt_crypto.domain.account import (
    NumericFillOrderKind,
    SettlementFillOrderKind,
    _ExactTaggedUnion,
)
from hmt_crypto.domain.private_primitives import (
    AssetSymbol,
    ExactAwareUtcDatetime,
    ExactStrEnum,
    ExactUuid,
    ExternalUserText,
    NonNegativePrivateDecimal,
    PositivePrivateDecimal,
    PrivateBoundaryModel,
    PrivateDecimal,
    PrivateId,
    ProductSymbol,
    StrictNonNegativeInt,
)

_ORDERS_MODULE = "hmt_crypto.domain.private_orders"
_COMPACT_UUID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_NUMERIC_ORDER = re.compile(r"[1-9][0-9]{0,18}\Z", re.ASCII)
_MAX_I63 = 2**63 - 1

if TYPE_CHECKING:
    from hmt_crypto.domain.private_orders import (
        FillMetadataPresence,
        ObservedFillContractType,
        ObservedFillNotionalType,
        ObservedFillRole,
        ObservedFillType,
        ObservedOrderSide,
    )


class FillType(ExactStrEnum):
    __module__ = _ORDERS_MODULE

    NORMAL = "normal"
    ADL = "adl"
    LIQUIDATION = "liquidation"
    SETTLEMENT = "settlement"
    OTC = "otc"


class FillRole(ExactStrEnum):
    __module__ = _ORDERS_MODULE

    MAKER = "maker"
    TAKER = "taker"


class FillContractType(ExactStrEnum):
    __module__ = _ORDERS_MODULE

    FUTURES = "futures"
    PERPETUAL_FUTURES = "perpetual_futures"
    CALL_OPTIONS = "call_options"
    PUT_OPTIONS = "put_options"


class FillNotionalType(ExactStrEnum):
    __module__ = _ORDERS_MODULE

    VANILLA = "vanilla"
    INVERSE = "inverse"


class FillMetadataProfile(ExactStrEnum):
    __module__ = _ORDERS_MODULE


class SettlementFillOrderProfile(ExactStrEnum):
    __module__ = _ORDERS_MODULE


class FillMetadataValue(PrivateBoundaryModel):
    __module__ = _ORDERS_MODULE
    model_config = ConfigDict(defer_build=True)

    profile: FillMetadataProfile


class FillAssetSummary(PrivateBoundaryModel):
    __module__ = _ORDERS_MODULE
    model_config = ConfigDict(defer_build=True)

    symbol: AssetSymbol
    precision: StrictNonNegativeInt
    minimum_precision: StrictNonNegativeInt


class FillSpotIndexSummary(PrivateBoundaryModel):
    __module__ = _ORDERS_MODULE
    model_config = ConfigDict(defer_build=True)

    symbol: ProductSymbol


class FillProductSummary(PrivateBoundaryModel):
    __module__ = _ORDERS_MODULE
    model_config = ConfigDict(defer_build=True)

    id: PrivateId
    symbol: ProductSymbol
    contract_type: ObservedFillContractType
    tick_size: PositivePrivateDecimal
    contract_value: PositivePrivateDecimal
    contract_unit_currency: AssetSymbol
    notional_type: ObservedFillNotionalType
    quoting_asset: FillAssetSummary
    underlying_asset: FillAssetSummary
    settling_asset: FillAssetSummary
    spot_index: FillSpotIndexSummary


class NumericFillOrderRef(PrivateBoundaryModel):
    __module__ = _ORDERS_MODULE
    model_config = ConfigDict(defer_build=True)

    kind: NumericFillOrderKind
    raw: ExternalUserText
    order_id: PrivateId

    @model_validator(mode="after")
    def require_matching_numeric_reference(self) -> NumericFillOrderRef:
        if self.raw != str(self.order_id):
            raise type(self)._safe_error(
                "private_fill_order_reference", "invalid fill order reference"
            ) from None
        return self


class SettlementFillOrderRef(PrivateBoundaryModel):
    __module__ = _ORDERS_MODULE
    model_config = ConfigDict(defer_build=True)

    kind: SettlementFillOrderKind
    profile: SettlementFillOrderProfile
    order_uuid: ExactUuid


FillOrderRef = Annotated[
    NumericFillOrderRef | SettlementFillOrderRef,
    _ExactTaggedUnion((("NUMERIC", NumericFillOrderRef), ("SETTLEMENT", SettlementFillOrderRef))),
]


class Fill(PrivateBoundaryModel):
    __module__ = _ORDERS_MODULE
    model_config = ConfigDict(defer_build=True)

    id: ExactUuid
    size: NonNegativePrivateDecimal
    notional: NonNegativePrivateDecimal
    product: FillProductSummary
    fill_type: ObservedFillType
    side: ObservedOrderSide
    price: NonNegativePrivateDecimal
    role: ObservedFillRole
    commission: PrivateDecimal
    created_at: ExactAwareUtcDatetime
    product_id: PrivateId
    product_symbol: ProductSymbol
    order_id: FillOrderRef
    settling_asset_id: PrivateId
    settling_asset_symbol: AssetSymbol
    meta_data: FillMetadataPresence

    @model_validator(mode="after")
    def require_consistent_fill(self) -> Fill:
        consistent_product = (
            self.product.id == self.product_id
            and self.product.symbol == self.product_symbol
            and self.product.settling_asset.symbol == self.settling_asset_symbol
        )
        settlement = self.fill_type.known is FillType.SETTLEMENT
        expected_order_type = SettlementFillOrderRef if settlement else NumericFillOrderRef
        if not consistent_product or type(self.order_id) is not expected_order_type:
            raise type(self)._safe_error("private_fill_value", "invalid private fill") from None
        return self


def parse_compact_fill_uuid(value: object) -> UUID:
    """Parse one exact lowercase compact UUID without widening wire grammar."""
    if type(value) is not str or _COMPACT_UUID.fullmatch(value) is None:
        del value
        raise ValueError("invalid compact fill UUID") from None
    raw = value
    try:
        result = UUID(hex=raw)
    except Exception:
        del raw, value
        raise ValueError("invalid compact fill UUID") from None
    if result.hex != raw:
        del raw, value, result
        raise ValueError("invalid compact fill UUID") from None
    return result


def parse_numeric_fill_order_ref(value: object) -> NumericFillOrderRef:
    """Parse one canonical positive signed-64-bit decimal order reference."""
    if type(value) is not str or _NUMERIC_ORDER.fullmatch(value) is None:
        del value
        raise ValueError("invalid numeric fill order reference") from None
    raw = value
    number = int(raw)
    if number > _MAX_I63:
        del number, raw, value
        raise ValueError("invalid numeric fill order reference") from None
    return NumericFillOrderRef(kind="NUMERIC", raw=raw, order_id=number)


def _module_namespace(
    name: str, modules: dict[str, object], module_type: object
) -> dict[str, object] | None:
    try:
        module = dict.__getitem__(modules, name)
    except KeyError:
        return None
    if type(module) is not module_type:
        return None
    try:
        namespace = dict.copy(object.__getattribute__(module, "__dict__"))
    except (AttributeError, TypeError, RuntimeError):
        return None
    actual_name = dict.get(namespace, "__name__")
    return namespace if type(actual_name) is str and actual_name == name else None


def _class_shape(value: object, base: object, name: str) -> bool:
    try:
        bases = type.__getattribute__(value, "__bases__")
        mro = type.__getattribute__(value, "__mro__")
        base_mro = type.__getattribute__(base, "__mro__")
        module = type.__getattribute__(value, "__module__")
        actual_name = type.__getattribute__(value, "__name__")
        qualname = type.__getattribute__(value, "__qualname__")
    except Exception:
        return False
    return (
        type(value) is type(base)
        and type(bases) is tuple
        and tuple.__len__(bases) == 1
        and tuple.__getitem__(bases, 0) is base
        and type(mro) is tuple
        and type(base_mro) is tuple
        and tuple.__len__(mro) == tuple.__len__(base_mro) + 1
        and tuple.__getitem__(mro, 0) is value
        and all(
            tuple.__getitem__(mro, index + 1) is tuple.__getitem__(base_mro, index)
            for index in range(tuple.__len__(base_mro))
        )
        and type(module) is str
        and module == _ORDERS_MODULE
        and type(actual_name) is str
        and actual_name == name
        and type(qualname) is str
        and qualname == name
    )


_PRIMITIVE_NAMES = (
    "AssetSymbol",
    "ExactAwareUtcDatetime",
    "ExactStrEnum",
    "ExactUuid",
    "ExternalUserText",
    "NonNegativePrivateDecimal",
    "PositivePrivateDecimal",
    "PrivateBoundaryModel",
    "PrivateDecimal",
    "PrivateId",
    "ProductSymbol",
    "StrictNonNegativeInt",
)
_ACCOUNT_NAMES = ("NumericFillOrderKind", "SettlementFillOrderKind", "_ExactTaggedUnion")
_PUBLIC_NAMES = (
    "FillType",
    "FillRole",
    "FillContractType",
    "FillNotionalType",
    "FillMetadataProfile",
    "SettlementFillOrderProfile",
    "FillMetadataValue",
    "FillAssetSummary",
    "FillSpotIndexSummary",
    "FillProductSummary",
    "NumericFillOrderRef",
    "SettlementFillOrderRef",
    "Fill",
)


# fmt: off
def _fill_contract_snapshot(_source: object = None) -> tuple[object, ...] | None:
    """Return one callback-free construction snapshot or fail closed."""
    try:
        namespace = dict.copy(globals())
    except RuntimeError:
        return None
    if type(_source) is not tuple or tuple.__len__(_source) != 2:
        return None
    names, identities = tuple.__getitem__(_source, 0), tuple.__getitem__(_source, 1)
    if (
        dict.get(namespace, "_FILL_SOURCE_CAPTURE") is not _source
        or type(names) is not tuple
        or type(identities) is not tuple
        or tuple.__len__(names) != tuple.__len__(identities)
        or any(dict.get(namespace, name) is not tuple.__getitem__(identities, index) for index, name in enumerate(names))
    ):
        return None
    sys_module = dict.get(namespace, "_sys")
    module_type = dict.get(namespace, "ModuleType")
    if type(sys_module) is not module_type:
        return None
    try:
        sys_namespace = dict.copy(object.__getattribute__(sys_module, "__dict__"))
        modules = dict.copy(object.__getattribute__(sys_module, "modules"))
    except (TypeError, RuntimeError):
        return None
    sys_name = dict.get(sys_namespace, "__name__")
    if type(sys_name) is not str or sys_name != "sys" or dict.get(modules, "sys") is not sys_module:
        return None
    types_source = _module_namespace("types", modules, module_type)
    typing_source = _module_namespace("typing", modules, module_type)
    primitives = _module_namespace("hmt_crypto.domain.private_primitives", modules, module_type)
    account = _module_namespace("hmt_crypto.domain.account", modules, module_type)
    account_source = _module_namespace("hmt_crypto.domain._account_contracts", modules, module_type)
    core_source = _module_namespace("hmt_crypto.domain._private_generic_core", modules, module_type)
    if (
        types_source is None
        or typing_source is None
        or primitives is None
        or account is None
        or account_source is None
        or core_source is None
    ):
        return None
    for name in ("FunctionType", "ModuleType", "UnionType"):
        if dict.get(namespace, name) is not dict.get(types_source, name):
            return None
    if dict.get(namespace, "Annotated") is not dict.get(typing_source, "Annotated"):
        return None
    for source, names in ((primitives, _PRIMITIVE_NAMES), (account, _ACCOUNT_NAMES)):
        for name in names:
            if dict.get(namespace, name) is not dict.get(source, name):
                return None
    for name in ("NumericFillOrderKind", "SettlementFillOrderKind"):
        if dict.get(account, name) is not dict.get(account_source, name):
            return None
    if dict.get(account, "_ExactTaggedUnion") is not dict.get(core_source, "_ExactTaggedUnion"):
        return None
    enum_base = dict.get(namespace, "ExactStrEnum")
    model_base = dict.get(namespace, "PrivateBoundaryModel")
    values: list[object] = []
    for index, name in enumerate(_PUBLIC_NAMES):
        value = dict.get(namespace, name)
        if not _class_shape(value, enum_base if index < 6 else model_base, name):
            return None
        values.append(value)
    alias = dict.get(namespace, "FillOrderRef")
    if type(alias) is not type(Annotated[object, None]):
        return None
    try:
        inner = object.__getattribute__(alias, "__origin__")
        alias_args = object.__getattribute__(alias, "__args__")
        metadata = object.__getattribute__(alias, "__metadata__")
        inner_args = object.__getattribute__(inner, "__args__")
    except Exception:
        return None
    if (
        type(inner) is not UnionType
        or type(alias_args) is not tuple
        or tuple.__len__(alias_args) != 1
        or tuple.__getitem__(alias_args, 0) is not inner
        or type(inner_args) is not tuple
        or tuple.__len__(inner_args) != 2
        or tuple.__getitem__(inner_args, 0) is not values[10]
        or tuple.__getitem__(inner_args, 1) is not values[11]
        or type(metadata) is not tuple
        or tuple.__len__(metadata) != 1
    ):
        return None
    tagged = tuple.__getitem__(metadata, 0)
    if type(tagged) is not dict.get(namespace, "_ExactTaggedUnion"):
        return None
    try:
        tags = object.__getattribute__(tagged, "tags")
    except Exception:
        return None
    expected_tags = (("NUMERIC", values[10]), ("SETTLEMENT", values[11]))
    if type(tags) is not tuple or tuple.__len__(tags) != 2:
        return None
    for index in range(2):
        tag = tuple.__getitem__(tags, index)
        expected = tuple.__getitem__(expected_tags, index)
        if (
            type(tag) is not tuple
            or tuple.__len__(tag) != 2
            or type(tuple.__getitem__(tag, 0)) is not str
            or tuple.__getitem__(tag, 0) != tuple.__getitem__(expected, 0)
            or tuple.__getitem__(tag, 1) is not tuple.__getitem__(expected, 1)
        ):
            return None
    functions = (
        "parse_compact_fill_uuid",
        "parse_numeric_fill_order_ref",
        "_rebuild_fill_independent",
        "_rebuild_fill_dependent",
    )
    for name in functions:
        function = dict.get(namespace, name)
        if type(function) is not FunctionType:
            return None
        module = object.__getattribute__(function, "__module__")
        actual_name = object.__getattribute__(function, "__name__")
        qualname = object.__getattribute__(function, "__qualname__")
        if not (
            type(module) is str
            and module == __name__
            and type(actual_name) is str
            and actual_name == name
            and type(qualname) is str
            and qualname == name
        ):
            return None
        values.append(function)
    narrow = {
        name: dict.__getitem__(namespace, name)
        for name in (*_PRIMITIVE_NAMES, *_ACCOUNT_NAMES, *_PUBLIC_NAMES, "FillOrderRef")
    }
    return (*values, alias, narrow)
# fmt: on


def _owned_namespace(namespace: object, snapshot: object) -> dict[str, object]:
    if type(namespace) is not dict or type(snapshot) is not tuple or len(snapshot) != 19:
        raise RuntimeError("invalid fill rebuild source") from None
    owned = dict.copy(namespace)
    narrow = dict.copy(tuple.__getitem__(snapshot, 18))
    for index, name in enumerate(_PUBLIC_NAMES):
        if dict.get(owned, name) is not tuple.__getitem__(snapshot, index):
            raise RuntimeError("invalid fill rebuild source") from None
        dict.__setitem__(narrow, name, tuple.__getitem__(snapshot, index))
    if dict.get(owned, "FillOrderRef") is not tuple.__getitem__(snapshot, 17):
        raise RuntimeError("invalid fill rebuild source") from None
    dict.__setitem__(narrow, "FillOrderRef", tuple.__getitem__(snapshot, 17))
    return narrow


def _rebuild_fill_independent(namespace: object, snapshot: object) -> None:
    types_namespace = _owned_namespace(namespace, snapshot)
    if type(snapshot) is not tuple:
        raise RuntimeError("invalid fill rebuild source") from None
    for index in (6, 7, 8, 10, 11):
        model = tuple.__getitem__(snapshot, index)
        type.__getattribute__(model, "model_rebuild")(
            force=True, raise_errors=True, _types_namespace=types_namespace
        )


def _rebuild_fill_dependent(namespace: object, snapshot: object) -> None:
    types_namespace = _owned_namespace(namespace, snapshot)
    if type(namespace) is not dict:
        raise RuntimeError("invalid fill rebuild source") from None
    if type(snapshot) is not tuple:
        raise RuntimeError("invalid fill rebuild source") from None
    owned = dict.copy(namespace)
    for name in (
        "FillMetadataPresence",
        "ObservedFillContractType",
        "ObservedFillNotionalType",
        "ObservedFillRole",
        "ObservedFillType",
        "ObservedOrderSide",
    ):
        if not dict.__contains__(owned, name):
            raise RuntimeError("invalid fill rebuild source") from None
        dict.__setitem__(types_namespace, name, dict.__getitem__(owned, name))
    for index in (9, 12):
        model = tuple.__getitem__(snapshot, index)
        type.__getattribute__(model, "model_rebuild")(
            force=True, raise_errors=True, _types_namespace=types_namespace
        )


# fmt: off
_FILL_SOURCE_NAMES = (
    "_COMPACT_UUID", "_NUMERIC_ORDER", "_MAX_I63", "FillType", "FillRole", "FillContractType", "FillNotionalType", "FillMetadataProfile", "SettlementFillOrderProfile", "FillMetadataValue", "FillAssetSummary", "FillSpotIndexSummary", "FillProductSummary", "NumericFillOrderRef", "SettlementFillOrderRef", "FillOrderRef", "Fill", "parse_compact_fill_uuid", "parse_numeric_fill_order_ref", "_module_namespace", "_class_shape", "_fill_contract_snapshot", "_owned_namespace", "_rebuild_fill_independent", "_rebuild_fill_dependent",
)
_FILL_SOURCE_IDENTITIES = (
    _COMPACT_UUID, _NUMERIC_ORDER, _MAX_I63, FillType, FillRole, FillContractType, FillNotionalType, FillMetadataProfile, SettlementFillOrderProfile, FillMetadataValue, FillAssetSummary, FillSpotIndexSummary, FillProductSummary, NumericFillOrderRef, SettlementFillOrderRef, FillOrderRef, Fill, parse_compact_fill_uuid, parse_numeric_fill_order_ref, _module_namespace, _class_shape, _fill_contract_snapshot, _owned_namespace, _rebuild_fill_independent, _rebuild_fill_dependent,
)
_FILL_SOURCE_CAPTURE = (_FILL_SOURCE_NAMES, _FILL_SOURCE_IDENTITIES)
_fill_contract_snapshot.__defaults__ = (_FILL_SOURCE_CAPTURE,)

__all__ = [
    "Fill", "FillAssetSummary", "FillContractType", "FillMetadataProfile", "FillMetadataValue",
    "FillNotionalType", "FillOrderRef", "FillProductSummary", "FillRole", "FillSpotIndexSummary",
    "FillType", "NumericFillOrderRef", "SettlementFillOrderProfile", "SettlementFillOrderRef",
    "parse_compact_fill_uuid", "parse_numeric_fill_order_ref",
]
# fmt: on
