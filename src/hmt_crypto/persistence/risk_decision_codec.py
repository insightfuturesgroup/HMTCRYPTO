"""Canonical hashing, encoding, decoding, and Core payload boundary."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import ClassVar, cast
from uuid import UUID

from sqlalchemy.engine import Dialect

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.risk_decision_contracts import (
    CHECK_EVIDENCE_FIELDS,
    CHECK_INPUT_FIELDS,
    CHECK_KINDS,
    CONTEXT_FIELDS,
    DECISION_PAYLOAD_FIELDS,
    RESERVATION_PLAN_INPUT_FIELDS,
    RESERVATION_PLAN_STORED_FIELDS,
    ReservationPlanV1,
    RiskCheckV1,
    RiskDecisionEncoding,
    _canonical,
    _fail,
    _hash,
    _timestamp,
)
from hmt_crypto.persistence.risk_decision_semantics import (
    _context,
    _owned_checks,
    _owned_plan,
)
from hmt_crypto.persistence.schema_support import NulFreeText
from hmt_crypto.persistence.types import _decimal_text


def _stored_check(check: RiskCheckV1, decision_id: UUID) -> dict[str, object]:
    raw: dict[str, object] = {
        "check_kind": check.check_kind,
        "outcome": check.outcome,
        "reason_code": check.reason_code,
        "metric_kind": check.metric_kind,
        "observed_decimal": None
        if check.observed_decimal is None
        else _decimal_text(check.observed_decimal, 38, 18),
        "limit_decimal": None
        if check.limit_decimal is None
        else _decimal_text(check.limit_decimal, 38, 18),
        "observed_integer": check.observed_integer,
        "limit_integer": check.limit_integer,
        "unit": check.unit,
        "evidence_observed_at": _timestamp(check.evidence_observed_at),
        "input_set_sha256": check.input_set_sha256,
    }
    envelope = {
        "kind": "risk_check_evidence",
        "payload": {"decision_id": str(decision_id), **raw},
        "schema_version": 1,
    }
    return {
        **raw,
        "evidence_sha256": hashlib.sha256(_canonical(envelope).encode("ascii")).hexdigest(),
    }


def _stored_plan(
    plan: ReservationPlanV1 | None, decision_id: UUID
) -> tuple[dict[str, object], str | None]:
    if plan is None:
        return ({field: None for field in RESERVATION_PLAN_STORED_FIELDS}, None)
    raw: dict[str, object] = {
        "approved_contracts": plan.approved_contracts,
        "approved_side": plan.approved_side,
        "reference_asset_id": plan.reference_asset_id,
        "reference_asset_symbol": plan.reference_asset_symbol,
        "collateral_asset_id": plan.collateral_asset_id,
        "collateral_asset_symbol": plan.collateral_asset_symbol,
        "reserved_incremental_gross_exposure": _decimal_text(
            plan.reserved_incremental_gross_exposure, 38, 18
        ),
        "reserved_worst_case_loss": _decimal_text(plan.reserved_worst_case_loss, 38, 18),
        "reserved_collateral": _decimal_text(plan.reserved_collateral, 38, 18),
        "reserved_open_order_slots": plan.reserved_open_order_slots,
        "reserved_pending_rate_units": plan.reserved_pending_rate_units,
        "rate_policy_version": plan.rate_policy_version,
        "rate_policy_sha256": plan.rate_policy_sha256,
        "rate_window_start_at": _timestamp(plan.rate_window_start_at),
        "rate_window_end_at": _timestamp(plan.rate_window_end_at),
    }
    envelope = {
        "kind": "risk_reservation_plan",
        "payload": {"decision_id": str(decision_id), **raw},
        "schema_version": 1,
    }
    digest = hashlib.sha256(_canonical(envelope).encode("ascii")).hexdigest()
    return ({**raw, "reservation_plan_sha256": digest}, digest)


def _payload_context(context: dict[str, object]) -> dict[str, object]:
    encoded: dict[str, object] = {}
    for field, value in context.items():
        if type(value) is UUID:
            encoded[field] = str(value)
        elif type(value) is datetime:
            encoded[field] = _timestamp(value)
        else:
            encoded[field] = value
    return encoded


def encode_risk_decision_evidence(
    schema_version: object,
    values: object,
    check_results: object,
    reservation_plan: object,
) -> RiskDecisionEncoding:
    """Own all v1 inputs and return canonical decision evidence and digests."""
    try:
        if type(schema_version) is not int or schema_version != 1:
            _fail()
        context = _context(values)
        checks = _owned_checks(check_results, context)
        plan = _owned_plan(reservation_plan, context)
        if context["outcome"] == "APPROVED" and (
            context["observed_halt_set_state"] != "INITIALIZED"
            or context["observed_reconciliation_state"] != "READY_CLAIM"
            or context["reconciliation_completeness"] != "COMPLETE"
        ):
            _fail()
        if (
            context["outcome"] == "APPROVED"
            and context["risk_effect"] == "RISK_INCREASE"
            and (
                context["observed_accounting_state"] != "COMPLETE_CLAIM"
                or context["pnl_completeness"] != "COMPLETE"
            )
        ):
            _fail()
        units = {check.unit for check in checks}
        reference_required = (
            "REFERENCE_ASSET" in units or context["pnl_snapshot_id"] is not None or plan is not None
        )
        collateral_required = "COLLATERAL_ASSET" in units or plan is not None
        if reference_required != (context["evaluation_reference_asset_id"] is not None):
            _fail()
        if collateral_required != (context["evaluation_collateral_asset_id"] is not None):
            _fail()
        if plan is not None:
            by_kind = {check.check_kind: check for check in checks}
            loss = by_kind["MAX_LOSS_PER_TRADE"]
            collateral = by_kind["COLLATERAL_MARGIN"]
            if loss.outcome == "PASS" and loss.observed_decimal != plan.reserved_worst_case_loss:
                _fail()
            if (
                collateral.outcome == "PASS"
                and collateral.observed_decimal != plan.reserved_collateral
            ):
                _fail()

        decision_id = context["decision_id"]
        assert isinstance(decision_id, UUID)
        stored_checks = [_stored_check(check, decision_id) for check in checks]
        check_summary = [
            {
                "check_kind": item["check_kind"],
                "evidence_sha256": item["evidence_sha256"],
                "outcome": item["outcome"],
            }
            for item in stored_checks
        ]
        set_envelope = {
            "kind": "risk_check_set",
            "payload": {
                "check_set_version": 1,
                "decision_id": str(decision_id),
                "results": check_summary,
            },
            "schema_version": 1,
        }
        check_set_sha256 = hashlib.sha256(_canonical(set_envelope).encode("ascii")).hexdigest()
        stored_plan, plan_sha256 = _stored_plan(plan, decision_id)
        payload_dict = {
            **_payload_context(context),
            "check_results": stored_checks,
            "check_set_sha256": check_set_sha256,
            "check_set_version": 1,
            "reservation_plan": stored_plan,
        }
        payload = _canonical(payload_dict)
        if len(payload.encode("ascii")) > 16_384:
            _fail()
        envelope = {"kind": "risk_decision", "payload": payload_dict, "schema_version": 1}
        return RiskDecisionEncoding(
            payload=payload,
            evidence_sha256=hashlib.sha256(_canonical(envelope).encode("ascii")).hexdigest(),
            check_set_sha256=check_set_sha256,
            reservation_plan_sha256=plan_sha256,
        )
    except Exception:
        raise PersistenceValueError() from None


_UUID_CONTEXT = {
    "decision_id",
    "account_id",
    "risk_state_id",
    "command_id",
    "order_id",
    "order_origin_command_id",
    "observed_claim_owner_id",
    "leadership_lease_id",
    "recovery_incarnation_id",
    "observed_leadership_owner_id",
    "observed_command_leader_ref",
    "observed_command_recovery_ref",
    "reconciliation_run_id",
    "pnl_snapshot_id",
}
_TIME_CONTEXT = {
    "evaluated_at",
    "observed_claim_expires_at",
    "observed_leadership_expires_at",
    "reconciliation_observed_at",
}


def decode_risk_decision_evidence(payload: object) -> RiskDecisionEncoding:
    """Decode and re-encode exact canonical v1 payload, validating internal hashes."""
    try:
        if type(payload) is not str or len(payload.encode("utf-8")) > 16_384:
            _fail()
        raw = json.loads(payload)
        if type(raw) is not dict or set(raw) != DECISION_PAYLOAD_FIELDS:
            _fail()
        stored_checks = raw["check_results"]
        if type(stored_checks) is not list or len(stored_checks) != len(CHECK_KINDS):
            _fail()
        checks: dict[str, dict[str, object]] = {}
        for expected, stored in zip(CHECK_KINDS, stored_checks, strict=True):
            if type(stored) is not dict or set(stored) != CHECK_EVIDENCE_FIELDS:
                _fail()
            if stored["check_kind"] != expected:
                _fail()
            item = {field: stored[field] for field in CHECK_INPUT_FIELDS}
            for field in ("observed_decimal", "limit_decimal"):
                if item[field] is not None:
                    item[field] = Decimal(cast(str, item[field]))
            if item["evidence_observed_at"] is not None:
                item["evidence_observed_at"] = datetime.fromisoformat(
                    cast(str, item["evidence_observed_at"])
                )
            checks[expected] = item
        stored_plan = raw["reservation_plan"]
        if type(stored_plan) is not dict or set(stored_plan) != RESERVATION_PLAN_STORED_FIELDS:
            _fail()
        if all(value is None for value in stored_plan.values()):
            plan: dict[str, object] | None = None
        else:
            plan = {field: stored_plan[field] for field in RESERVATION_PLAN_INPUT_FIELDS}
            for field in (
                "reserved_incremental_gross_exposure",
                "reserved_worst_case_loss",
                "reserved_collateral",
            ):
                plan[field] = Decimal(cast(str, plan[field]))
            for field in ("rate_window_start_at", "rate_window_end_at"):
                plan[field] = datetime.fromisoformat(cast(str, plan[field]))
        context = {field: raw[field] for field in CONTEXT_FIELDS}
        for field in _UUID_CONTEXT:
            if context[field] is not None:
                context[field] = UUID(context[field])
        for field in _TIME_CONTEXT:
            if context[field] is not None:
                context[field] = datetime.fromisoformat(context[field])
        encoded = encode_risk_decision_evidence(1, context, checks, plan)
        if (
            encoded.payload != payload
            or encoded.check_set_sha256 != raw["check_set_sha256"]
            or encoded.reservation_plan_sha256 != raw["reservation_plan"]["reservation_plan_sha256"]
        ):
            _fail()
        return encoded
    except Exception:
        raise PersistenceValueError() from None


def validate_risk_decision_evidence(
    schema_version: object, payload: object, digest: object
) -> None:
    """Fail closed unless canonical payload and outer digest agree exactly."""
    try:
        if (
            type(schema_version) is not int
            or schema_version != 1
            or _hash(digest) != decode_risk_decision_evidence(payload).evidence_sha256
        ):
            _fail()
    except Exception:
        raise PersistenceValueError() from None


class RiskDecisionEvidencePayload(NulFreeText):
    """Core adapter for standalone canonical payload and internal digest checks."""

    cache_ok = True
    _maximum: ClassVar[int] = 16_384

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        result = super().process_bind_param(value, dialect)
        if result is not None:
            decode_risk_decision_evidence(result)
        return result

    def process_result_value(self, value: object, dialect: Dialect) -> str | None:
        return self.process_bind_param(value, dialect)
