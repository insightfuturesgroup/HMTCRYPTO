"""Canonical PnL calculation evidence; no formulas, readiness, or authorization."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.engine import Dialect

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.schema_support import NulFreeText
from hmt_crypto.persistence.types import _decimal_text, _utc

PNL_KIND = "pnl_snapshot"
COMPLETENESS = ("UNKNOWN", "PARTIAL", "COMPLETE")
MEASURE_FIELDS = (
    "baseline_equity",
    "observed_equity",
    "reconciled_realized_pnl",
    "mark_to_market_unrealized_pnl",
    "equity_drawdown_loss",
    "unresolved_loss_reserve",
    "conservative_daily_loss",
)
LOSS_FIELDS = (
    "equity_drawdown_loss",
    "unresolved_loss_reserve",
    "conservative_daily_loss",
)
PNL_FIELDS = frozenset(
    {
        *MEASURE_FIELDS,
        "calculation_policy_sha256",
        "calculation_version",
        "completeness",
        "input_set_sha256",
        "reconciliation_run_id",
        "reconciliation_version",
        "reference_asset_id",
        "reference_asset_symbol",
        "window_end_at",
        "window_start_at",
    }
)


def _positive_bigint(value: object) -> int:
    if type(value) is not int or not 1 <= value < 2**63:
        raise PersistenceValueError()
    return value


def _uuid(value: object) -> str:
    if type(value) is not UUID or value.int == 0:
        raise PersistenceValueError()
    return str(value)


def _text(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128 or not value.strip() or "\x00" in value:
        raise PersistenceValueError()
    return value


def _hash(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PersistenceValueError()
    return value


def _measure(value: object, *, nonnegative: bool) -> str | None:
    if value is None:
        return None
    encoded = _decimal_text(value, 38, 18)
    if nonnegative and Decimal(encoded) < 0:
        raise PersistenceValueError()
    return encoded


def _canonical_payload(values: object) -> str:
    if type(values) is not dict or set(values) != PNL_FIELDS:
        raise PersistenceValueError()
    owned = dict(values)
    completeness = owned["completeness"]
    if type(completeness) is not str or completeness not in COMPLETENESS:
        raise PersistenceValueError()
    measures = {
        field: _measure(owned[field], nonnegative=field in LOSS_FIELDS) for field in MEASURE_FIELDS
    }
    known_first_six = any(measures[field] is not None for field in MEASURE_FIELDS[:-1])
    if (
        (completeness == "UNKNOWN" and any(value is not None for value in measures.values()))
        or (
            completeness == "PARTIAL"
            and (not known_first_six or measures["conservative_daily_loss"] is not None)
        )
        or (completeness == "COMPLETE" and any(value is None for value in measures.values()))
    ):
        raise PersistenceValueError()
    start = _utc(owned["window_start_at"])
    end = _utc(owned["window_end_at"])
    if start > end:
        raise PersistenceValueError()
    payload = {
        "baseline_equity": measures["baseline_equity"],
        "calculation_policy_sha256": _hash(owned["calculation_policy_sha256"]),
        "calculation_version": _positive_bigint(owned["calculation_version"]),
        "completeness": completeness,
        "conservative_daily_loss": measures["conservative_daily_loss"],
        "equity_drawdown_loss": measures["equity_drawdown_loss"],
        "input_set_sha256": _hash(owned["input_set_sha256"]),
        "mark_to_market_unrealized_pnl": measures["mark_to_market_unrealized_pnl"],
        "observed_equity": measures["observed_equity"],
        "reconciled_realized_pnl": measures["reconciled_realized_pnl"],
        "reconciliation_run_id": _uuid(owned["reconciliation_run_id"]),
        "reconciliation_version": _positive_bigint(owned["reconciliation_version"]),
        "reference_asset_id": _positive_bigint(owned["reference_asset_id"]),
        "reference_asset_symbol": _text(owned["reference_asset_symbol"]),
        "unresolved_loss_reserve": measures["unresolved_loss_reserve"],
        "window_end_at": end.isoformat(timespec="microseconds"),
        "window_start_at": start.isoformat(timespec="microseconds"),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    if len(encoded.encode("ascii")) > 2048:
        raise PersistenceValueError()
    return encoded


def encode_pnl_evidence(schema_version: object, values: object) -> tuple[str, str]:
    """Own typed calculation semantics and return canonical payload plus digest."""
    try:
        if type(schema_version) is not int or schema_version != 1:
            raise PersistenceValueError()
        payload = _canonical_payload(values)
        envelope = '{"kind":"pnl_snapshot","payload":' + payload + ',"schema_version":1}'
        return payload, hashlib.sha256(envelope.encode("ascii")).hexdigest()
    except Exception:
        raise PersistenceValueError() from None


def decode_pnl_evidence(payload: object) -> dict[str, object]:
    """Restore exact typed values only from the standalone canonical v1 shape."""
    try:
        if type(payload) is not str or len(payload.encode("utf-8")) > 2048:
            raise PersistenceValueError()
        raw = json.loads(payload)
        if type(raw) is not dict:
            raise PersistenceValueError()
        for field in MEASURE_FIELDS:
            if field in raw and raw[field] is not None:
                raw[field] = Decimal(raw[field])
        if "reconciliation_run_id" in raw:
            raw["reconciliation_run_id"] = UUID(raw["reconciliation_run_id"])
        for field in ("window_start_at", "window_end_at"):
            if field in raw:
                raw[field] = datetime.fromisoformat(raw[field])
        canonical, _ = encode_pnl_evidence(1, raw)
        if canonical != payload:
            raise PersistenceValueError()
        return raw
    except Exception:
        raise PersistenceValueError() from None


def validate_pnl_evidence(schema_version: object, payload: object, digest: object) -> None:
    """Fail closed unless standalone payload and sibling digest agree exactly."""
    try:
        values = decode_pnl_evidence(payload)
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or encode_pnl_evidence(schema_version, values) != (payload, digest)
        ):
            raise PersistenceValueError()
    except Exception:
        raise PersistenceValueError() from None


class PnlEvidencePayload(NulFreeText):
    """Canonical standalone PnL payload admission on bind and result paths."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str:
        decode_pnl_evidence(value)
        assert isinstance(value, str)
        return value

    def process_result_value(self, value: object, dialect: Dialect) -> str:
        return self.process_bind_param(value, dialect)
