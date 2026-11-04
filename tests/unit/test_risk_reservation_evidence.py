"""Canonical pre-dispatch reservation/release evidence is fail-closed."""

import hashlib
import json
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from risk_reservation_test_support import (
    EVENT_RECORDED_AT,
    ORDER_ID,
    PROOF_OBSERVED_AT,
    RESERVATION_KEY,
    attempt,
    attempt_set_values,
    current_intent,
    event,
    release_values,
    reservation_key_values,
)

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_evidence import encode_evidence


def evidence():
    import hmt_crypto.persistence.risk_reservation_evidence as module

    return module


def attempt_set(attempts=None, **changes):
    rows = [attempt()] if attempts is None else attempts
    current = changes.pop("current_intent", current_intent())
    return evidence().encode_attempt_evidence_set(
        1,
        attempt_set_values(),
        rows,
        current_intent=current,
        observed_command_attempt_count=changes.pop("observed_command_attempt_count", len(rows)),
        locked_attempt_count=changes.pop("locked_attempt_count", len(rows)),
        locked_event_count=changes.pop(
            "locked_event_count", sum(len(item["events"]) for item in rows)
        ),
        **changes,
    )


def release_proof(values=None, attempts=None, *, current=None):
    return evidence().encode_risk_reservation_release_proof(
        1,
        release_values() if values is None else values,
        attempt_set() if attempts is None else attempts,
        current_intent=current_intent() if current is None else current,
    )


def test_reservation_key_known_vector_and_mutation_binding():
    module = evidence()
    assert module.derive_reservation_key(1, reservation_key_values()) == RESERVATION_KEY
    for field, replacement in (
        ("account_id", uuid4()),
        ("command_operation_kind", "MODIFY_ORDER"),
        ("decision_recorded_at", PROOF_OBSERVED_AT),
        ("product_id", 1102),
        ("reservation_plan_sha256", "e" * 64),
    ):
        assert (
            module.derive_reservation_key(1, reservation_key_values(**{field: replacement}))
            != RESERVATION_KEY
        )


def test_literal_empty_one_attempt_release_key_and_complete_release_proof_vectors():
    literal = json.loads(
        (Path(__file__).parents[1] / "fixtures/risk_reservation_v1.json").read_text()
    )
    module = evidence()
    empty = module.encode_attempt_evidence_set(
        1,
        attempt_set_values(),
        [],
        current_intent=current_intent(),
        observed_command_attempt_count=0,
        locked_attempt_count=0,
        locked_event_count=0,
    )
    one = attempt_set()
    proof = release_proof(attempts=one)
    assert module.derive_reservation_key(1, reservation_key_values()) == literal["reservation_key"]
    assert (empty.canonical, empty.sha256) == (
        literal["empty_attempt_set"]["canonical"],
        literal["empty_attempt_set"]["sha256"],
    )
    assert (one.canonical, one.sha256) == (
        literal["one_attempt_set"]["canonical"],
        literal["one_attempt_set"]["sha256"],
    )
    assert (proof.release_key, proof.payload, proof.sha256) == (
        literal["release_key"],
        literal["release_proof"]["payload"],
        literal["release_proof"]["sha256"],
    )
    assert hashlib.sha256(empty.canonical.encode("ascii")).hexdigest() == empty.sha256
    assert hashlib.sha256(one.canonical.encode("ascii")).hexdigest() == one.sha256


def test_release_key_known_vector_and_scope_parent_status_mutation_binding():
    literal = json.loads(
        (Path(__file__).parents[1] / "fixtures/risk_reservation_v1.json").read_text()
    )
    module = evidence()
    fields = {
        "account_id",
        "command_id",
        "command_operation_kind",
        "decision_id",
        "environment",
        "mode",
        "observed_terminal_command_status",
        "order_id",
        "product_id",
        "region",
        "reservation_id",
        "reservation_key",
    }
    base = {key: value for key, value in release_values().items() if key in fields}
    assert module.derive_release_key(1, base) == literal["release_key"]
    for field, replacement in (
        ("account_id", uuid4()),
        ("command_id", uuid4()),
        ("observed_terminal_command_status", "CANCELLED"),
        ("product_id", 1102),
        ("reservation_id", uuid4()),
        ("reservation_key", "e" * 64),
    ):
        changed = dict(base)
        changed[field] = replacement
        assert module.derive_release_key(1, changed) != literal["release_key"]


def test_independent_counts_and_capacity_are_hash_bound_in_complete_release_proof():
    module = evidence()
    baseline_attempts = attempt_set()
    baseline = release_proof(attempts=baseline_attempts)
    different_command_count = module.encode_attempt_evidence_set(
        1,
        attempt_set_values(),
        [attempt()],
        current_intent=current_intent(),
        observed_command_attempt_count=9,
        locked_attempt_count=1,
        locked_event_count=1,
    )
    changed_count = release_proof(attempts=different_command_count)
    changed_capacity = release_proof(
        values=release_values(
            planned_collateral=Decimal("11"),
            remaining_collateral=Decimal("11"),
        ),
        attempts=baseline_attempts,
    )
    assert len({baseline.sha256, changed_count.sha256, changed_capacity.sha256}) == 3
    assert changed_count.observed_command_attempt_count == 9


def test_release_rejects_attempt_set_encoded_for_another_reservation_and_decision():
    module = evidence()
    encoded_for_first = attempt_set()
    second_decision_id = uuid4()
    second_reservation_id = uuid4()
    second_key_values = reservation_key_values(decision_id=second_decision_id)
    second_reservation_key = module.derive_reservation_key(1, second_key_values)
    with pytest.raises(PersistenceValueError):
        release_proof(
            values=release_values(
                decision_id=second_decision_id,
                reservation_id=second_reservation_id,
                reservation_key=second_reservation_key,
            ),
            attempts=encoded_for_first,
        )


def test_release_rejects_publicly_forged_self_hashed_attempt_set_dataclass():
    module = evidence()
    legitimate = attempt_set()
    forged = module.AttemptEvidenceSetEncoding(
        canonical=legitimate.canonical,
        sha256=legitimate.sha256,
        observed_command_attempt_count=legitimate.observed_command_attempt_count,
        attempt_count=legitimate.attempt_count,
        event_count=legitimate.event_count,
    )
    with pytest.raises(PersistenceValueError):
        release_proof(attempts=forged)


def test_release_revalidates_nested_semantics_even_when_owned_token_is_copied():
    module = evidence()
    legitimate = attempt_set()
    raw = json.loads(legitimate.canonical)
    stored_event = raw["payload"]["attempts"][0]["events"][0]
    payload, digest = encode_evidence("event", "DISPATCH_STARTED", 1, {"outcome": "UNKNOWN"})
    stored_event.update(
        event_kind="DISPATCH_STARTED",
        evidence_payload=payload,
        evidence_sha256=digest,
    )
    forged_canonical = json.dumps(
        raw,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    forged = module.AttemptEvidenceSetEncoding(
        canonical=forged_canonical,
        sha256=hashlib.sha256(forged_canonical.encode("ascii")).hexdigest(),
        observed_command_attempt_count=legitimate.observed_command_attempt_count,
        attempt_count=legitimate.attempt_count,
        event_count=legitimate.event_count,
    )
    object.__setattr__(forged, "_owned_token", legitimate.__dict__["_owned_token"])
    with pytest.raises(PersistenceValueError):
        release_proof(attempts=forged)


def test_release_rejects_copied_token_with_rehashed_alternate_command_intent():
    module = evidence()
    legitimate = attempt_set()
    payload, digest = encode_evidence(
        "intent",
        "PLACE_ORDER",
        1,
        {
            "product_id": 1101,
            "requested_contracts": 9,
            "side": "sell",
            "order_type": "market",
            "reduce_only": False,
        },
    )
    raw = json.loads(legitimate.canonical)
    raw["payload"]["attempts"][0].update(
        evidence_payload=payload,
        evidence_sha256=digest,
    )
    forged_canonical = json.dumps(
        raw,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    forged = module.AttemptEvidenceSetEncoding(
        canonical=forged_canonical,
        sha256=hashlib.sha256(forged_canonical.encode("ascii")).hexdigest(),
        observed_command_attempt_count=1,
        attempt_count=1,
        event_count=1,
    )
    object.__setattr__(forged, "_owned_token", legitimate.__dict__["_owned_token"])
    with pytest.raises(PersistenceValueError):
        release_proof(attempts=forged)


@pytest.mark.parametrize(
    "command_intent",
    [
        {
            "product_id": 1102,
            "requested_contracts": 2,
            "side": "buy",
            "order_type": "market",
            "reduce_only": False,
        },
        {
            "product_id": 1101,
            "requested_contracts": 9,
            "side": "sell",
            "order_type": "market",
            "reduce_only": False,
        },
    ],
)
def test_normal_attempt_set_rejects_current_command_intent_that_disagrees_with_decision(
    command_intent,
):
    payload, digest = encode_evidence("intent", "PLACE_ORDER", 1, command_intent)
    current = current_intent(
        command_evidence_payload=payload,
        command_evidence_sha256=digest,
        order_product_id=command_intent["product_id"],
        order_requested_contracts=command_intent["requested_contracts"],
        order_requested_side=command_intent["side"],
        order_requested_order_type=command_intent["order_type"],
        order_requested_reduce_only=command_intent["reduce_only"],
    )
    with pytest.raises(PersistenceValueError):
        evidence().encode_attempt_evidence_set(
            1,
            attempt_set_values(),
            [attempt(evidence_payload=payload, evidence_sha256=digest)],
            current_intent=current,
            observed_command_attempt_count=1,
            locked_attempt_count=1,
            locked_event_count=1,
        )


@pytest.mark.parametrize(
    "command_intent,snapshot_changes",
    [
        (
            {
                "product_id": 1101,
                "requested_contracts": 2,
                "side": "buy",
                "order_type": "limit",
                "limit_price": Decimal("10"),
                "reduce_only": False,
            },
            {},
        ),
        (
            {
                "product_id": 1101,
                "requested_contracts": 2,
                "side": "buy",
                "order_type": "limit",
                "limit_price": Decimal("10"),
                "reduce_only": False,
            },
            {
                "order_requested_order_type": "limit",
                "order_requested_limit_price": Decimal("11"),
            },
        ),
        (
            {
                "product_id": 1101,
                "requested_contracts": 2,
                "side": "buy",
                "order_type": "market",
                "reduce_only": True,
            },
            {},
        ),
    ],
)
def test_normal_attempt_set_binds_order_type_price_and_reduce_only_intent(
    command_intent, snapshot_changes
):
    payload, digest = encode_evidence("intent", "PLACE_ORDER", 1, command_intent)
    current = current_intent(
        command_evidence_payload=payload,
        command_evidence_sha256=digest,
        **snapshot_changes,
    )
    with pytest.raises(PersistenceValueError):
        evidence().encode_attempt_evidence_set(
            1,
            attempt_set_values(),
            [attempt(evidence_payload=payload, evidence_sha256=digest)],
            current_intent=current,
            observed_command_attempt_count=1,
            locked_attempt_count=1,
            locked_event_count=1,
        )


def test_release_rejects_owned_set_for_different_valid_current_command_intent():
    module = evidence()
    payload, digest = encode_evidence(
        "intent",
        "PLACE_ORDER",
        1,
        {
            "product_id": 1101,
            "requested_contracts": 9,
            "side": "sell",
            "order_type": "market",
            "reduce_only": False,
        },
    )
    owned = module.encode_attempt_evidence_set(
        1,
        attempt_set_values(),
        [attempt(evidence_payload=payload, evidence_sha256=digest)],
        current_intent=current_intent(
            command_evidence_payload=payload,
            command_evidence_sha256=digest,
            decision_approved_contracts=9,
            decision_approved_side="sell",
            order_requested_contracts=9,
            order_requested_side="sell",
        ),
        observed_command_attempt_count=1,
        locked_attempt_count=1,
        locked_event_count=1,
    )
    with pytest.raises(PersistenceValueError):
        release_proof(attempts=owned)


@pytest.mark.parametrize("replacement", [True, 1.0])
def test_release_handoff_rejects_bool_or_float_outer_counts_even_when_equal(replacement):
    module = evidence()
    legitimate = attempt_set()
    raw = json.loads(legitimate.canonical)
    raw["schema_version"] = replacement
    for field in (
        "observed_command_attempt_count",
        "order_attempt_evidence_count",
        "order_attempt_event_evidence_count",
    ):
        raw["payload"][field] = replacement
    forged_canonical = json.dumps(
        raw,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    forged = module.AttemptEvidenceSetEncoding(
        canonical=forged_canonical,
        sha256=hashlib.sha256(forged_canonical.encode("ascii")).hexdigest(),
        observed_command_attempt_count=1,
        attempt_count=1,
        event_count=1,
    )
    object.__setattr__(forged, "_owned_token", legitimate.__dict__["_owned_token"])
    with pytest.raises(PersistenceValueError):
        release_proof(attempts=forged)


def test_release_handoff_rejects_bool_outer_product_that_equals_integer_one():
    module = evidence()
    payload, digest = encode_evidence(
        "intent",
        "PLACE_ORDER",
        1,
        {
            "product_id": 1,
            "requested_contracts": 2,
            "side": "buy",
            "order_type": "market",
            "reduce_only": False,
        },
    )
    legitimate = module.encode_attempt_evidence_set(
        1,
        attempt_set_values(product_id=1),
        [],
        current_intent=current_intent(
            command_evidence_payload=payload,
            command_evidence_sha256=digest,
            order_product_id=1,
        ),
        observed_command_attempt_count=0,
        locked_attempt_count=0,
        locked_event_count=0,
    )
    raw = json.loads(legitimate.canonical)
    raw["payload"]["product_id"] = True
    forged_canonical = json.dumps(
        raw,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    forged = module.AttemptEvidenceSetEncoding(
        canonical=forged_canonical,
        sha256=hashlib.sha256(forged_canonical.encode("ascii")).hexdigest(),
        observed_command_attempt_count=0,
        attempt_count=0,
        event_count=0,
    )
    object.__setattr__(forged, "_owned_token", legitimate.__dict__["_owned_token"])
    key_values = reservation_key_values(product_id=1)
    current = current_intent(
        command_evidence_payload=payload,
        command_evidence_sha256=digest,
        order_product_id=1,
    )
    with pytest.raises(PersistenceValueError):
        module.encode_risk_reservation_release_proof(
            1,
            release_values(
                product_id=1,
                reservation_key=module.derive_reservation_key(1, key_values),
            ),
            forged,
            current_intent=current,
        )


def test_release_decoder_rejects_impossible_attempt_event_count_mismatch():
    module = evidence()
    proof = release_proof()
    raw = json.loads(proof.payload)
    raw["order_attempt_event_evidence_count"] = 0
    corrupt = json.dumps(
        raw,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    with pytest.raises(PersistenceValueError):
        module.decode_risk_reservation_release_proof(corrupt)


def test_canonical_source_field_sets_are_requalified_by_literal_fixture_shapes():
    module = evidence()
    assert set(attempt()) == module.ATTEMPT_FIELDS
    assert set(event()) == module.ATTEMPT_EVENT_FIELDS
    outer = json.loads(attempt_set().canonical)["payload"]
    assert set(outer) == module.ATTEMPT_SET_PAYLOAD_FIELDS


def test_attempt_set_orders_caller_rows_and_binds_independent_counts():
    first = attempt()
    second_id = uuid4()
    second = attempt(
        id=second_id,
        attempt_number=3,
        correlation_id=uuid4(),
        events=[
            event(
                id=uuid4(),
                attempt_id=second_id,
                event_key="definite-not-sent-3",
            )
        ],
    )
    encoded = attempt_set(
        [second, first],
        observed_command_attempt_count=9,
        locked_attempt_count=2,
        locked_event_count=2,
    )
    assert encoded.attempt_count == 2
    assert encoded.event_count == 2
    assert encoded.observed_command_attempt_count == 9
    assert encoded.canonical.index(str(first["id"])) < encoded.canonical.index(str(second["id"]))
    assert (
        attempt_set(
            [first, second],
            observed_command_attempt_count=9,
            locked_attempt_count=2,
            locked_event_count=2,
        ).sha256
        == encoded.sha256
    )


@pytest.mark.parametrize(
    "event_changes",
    [
        {"sequence": 2},
        {"event_kind": "DISPATCH_STARTED", "evidence_payload": '{"outcome":"UNKNOWN"}'},
        {"event_kind": "RESPONSE_REJECTED", "evidence_payload": '{"outcome":"REJECTED"}'},
        {"recorded_at": PROOF_OBSERVED_AT + timedelta(microseconds=1)},
    ],
)
def test_only_one_definite_not_sent_event_qualifies(event_changes):
    item = attempt(events=[event(**event_changes)])
    with pytest.raises(PersistenceValueError):
        attempt_set([item])
    with pytest.raises(PersistenceValueError):
        attempt_set([attempt(events=[event(), event(id=uuid4(), event_key="extra")])])


@pytest.mark.parametrize(
    "event_kind,outcome",
    [
        ("DISPATCH_STARTED", "UNKNOWN"),
        ("RESPONSE_ACCEPTED", "ACCEPTED"),
        ("RESPONSE_REJECTED", "REJECTED"),
        ("UNCERTAIN", "UNKNOWN"),
        ("RECONCILED", "RESOLVED"),
        ("SIMULATED_ACCEPTED", "ACCEPTED"),
        ("SIMULATED_REJECTED", "REJECTED"),
    ],
)
def test_every_other_valid_event_evidence_kind_fails_release(event_kind, outcome):
    payload, digest = encode_evidence("event", event_kind, 1, {"outcome": outcome})
    source = event(
        event_kind=event_kind,
        evidence_payload=payload,
        evidence_sha256=digest,
    )
    with pytest.raises(PersistenceValueError):
        attempt_set([attempt(events=[source])])


def test_venue_rejection_is_dispatch_evidence_and_never_qualifies_for_release():
    payload, digest = encode_evidence("event", "RESPONSE_REJECTED", 1, {"outcome": "REJECTED"})
    owner = uuid4()
    leader = uuid4()
    source_attempt = attempt(
        mode="LIVE",
        backend="VENUE",
        simulator_kind=None,
        credential_version=2,
        original_claim_owner=owner,
        original_claim_generation=3,
        original_claim_expires_at=PROOF_OBSERVED_AT + timedelta(seconds=1),
        original_leader_ref=leader,
        original_leader_generation=3,
        events=[
            event(
                mode="LIVE",
                backend="VENUE",
                event_kind="RESPONSE_REJECTED",
                evidence_payload=payload,
                evidence_sha256=digest,
            )
        ],
    )
    with pytest.raises(PersistenceValueError):
        evidence().encode_attempt_evidence_set(
            1,
            attempt_set_values(mode="LIVE"),
            [source_attempt],
            current_intent=current_intent(),
            observed_command_attempt_count=1,
            locked_attempt_count=1,
            locked_event_count=1,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"locked_attempt_count": 2},
        {"locked_event_count": 2},
        {"observed_command_attempt_count": 65},
    ],
)
def test_count_first_mismatch_and_command_limit_fail_closed(changes):
    with pytest.raises(PersistenceValueError):
        attempt_set(**changes)


@pytest.mark.parametrize(
    "command_count,attempt_count,event_count,row_count",
    [
        (65, 1, 1, 1),
        (64, 65, 64, 65),
        (64, 64, 65, 64),
    ],
)
def test_each_65_count_boundary_aborts_before_retrieved_rows_are_touched(
    monkeypatch, command_count, attempt_count, event_count, row_count
):
    import hmt_crypto.persistence.risk_reservation_attempt_codec as codec

    touched = False

    def mark_touched(*args, **kwargs):
        nonlocal touched
        touched = True
        raise AssertionError("retrieved row was touched")

    monkeypatch.setattr(codec, "_owned_attempt", mark_touched)
    with pytest.raises(PersistenceValueError):
        evidence().encode_attempt_evidence_set(
            1,
            attempt_set_values(),
            [object()] * row_count,
            current_intent=current_intent(),
            observed_command_attempt_count=command_count,
            locked_attempt_count=attempt_count,
            locked_event_count=event_count,
        )
    assert not touched


def test_oversized_nested_event_sequence_rejects_before_materializing_elements(
    monkeypatch,
):
    import hmt_crypto.persistence.risk_reservation_attempt_codec as codec

    touched = 0

    def reject_if_touched(*args, **kwargs):
        nonlocal touched
        touched += 1
        raise AssertionError("oversized nested evidence was materialized")

    monkeypatch.setattr(codec.AttemptEventEvidenceV1, "model_validate", reject_if_touched)
    with pytest.raises(PersistenceValueError):
        attempt_set([attempt(events=[object()] * 65)], locked_event_count=1)
    assert touched == 0


def test_attempt_and_event_boundaries_are_independent_and_noncontiguous_is_valid():
    attempts = []
    for index in range(64):
        attempt_id = uuid4()
        attempts.append(
            attempt(
                id=attempt_id,
                attempt_number=index * 2 + 1,
                correlation_id=uuid4(),
                events=[
                    event(
                        id=uuid4(),
                        attempt_id=attempt_id,
                        event_key=f"event-{index}",
                    )
                ],
            )
        )
    assert (
        attempt_set(
            attempts,
            observed_command_attempt_count=64,
            locked_attempt_count=64,
            locked_event_count=64,
        ).attempt_count
        == 64
    )
    with pytest.raises(PersistenceValueError):
        attempt_set(
            attempts + [attempt(id=uuid4(), attempt_number=999, correlation_id=uuid4())],
            observed_command_attempt_count=64,
            locked_attempt_count=65,
            locked_event_count=65,
        )
    too_many_events = deepcopy(attempts)
    too_many_events[0]["events"] = [
        event(id=uuid4(), event_key=f"event-extra-{index}") for index in range(2)
    ]
    with pytest.raises(PersistenceValueError):
        attempt_set(
            too_many_events,
            observed_command_attempt_count=64,
            locked_attempt_count=64,
            locked_event_count=65,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"account_id": uuid4()},
        {"region": "global"},
        {"environment": "prod"},
        {"command_id": uuid4()},
        {"order_id": uuid4()},
        {"operation_kind": "MODIFY_ORDER"},
        {"prepared_at": PROOF_OBSERVED_AT + timedelta(microseconds=1)},
        {"recorded_at": PROOF_OBSERVED_AT + timedelta(microseconds=1)},
    ],
)
def test_cross_scope_parent_operation_or_future_attempt_is_rejected(changes):
    with pytest.raises(PersistenceValueError):
        attempt_set([attempt(**changes)])


@pytest.mark.parametrize("duplicate", ["id", "attempt_number", "event_id", "event_key"])
def test_duplicate_attempt_or_event_identities_are_rejected(duplicate):
    second_id = uuid4()
    second_event_id = uuid4()
    first = attempt()
    second = attempt(
        id=second_id,
        attempt_number=2,
        correlation_id=uuid4(),
        events=[
            event(
                id=second_event_id,
                attempt_id=second_id,
                event_key="definite-not-sent-2",
            )
        ],
    )
    if duplicate == "id":
        second["id"] = first["id"]
        second["events"][0]["attempt_id"] = first["id"]
    elif duplicate == "attempt_number":
        second["attempt_number"] = first["attempt_number"]
    elif duplicate == "event_id":
        second["events"][0]["id"] = first["events"][0]["id"]
    else:
        second["events"][0]["event_key"] = first["events"][0]["event_key"]
    with pytest.raises(PersistenceValueError):
        attempt_set(
            [first, second],
            observed_command_attempt_count=2,
            locked_attempt_count=2,
            locked_event_count=2,
        )


def test_raw_attempt_set_byte_guard_has_exact_boundary():
    module = evidence()
    assert len(module.guard_attempt_set_canonical_bytes(b"x" * 524_288)) == 524_288
    with pytest.raises(PersistenceValueError):
        module.guard_attempt_set_canonical_bytes(b"x" * 524_289)
    with pytest.raises(PersistenceValueError):
        module.guard_attempt_set_canonical_bytes("x" * 524_288)


def test_incremental_encoder_rejects_first_crossing_fragment_without_partial_append():
    import hmt_crypto.persistence.risk_reservation_attempt_codec as codec

    buffer = bytearray(b"x" * 524_287)
    with pytest.raises(PersistenceValueError):
        codec._append_bounded(buffer, "yz")
    assert buffer == bytearray(b"x" * 524_287)
    codec._append_bounded(buffer, "y")
    assert len(buffer) == 524_288


def test_stored_release_proof_raw_byte_guard_has_exact_boundary():
    module = evidence()
    assert len(module.guard_release_proof_payload_bytes(b"x" * 16_384)) == 16_384
    with pytest.raises(PersistenceValueError):
        module.guard_release_proof_payload_bytes(b"x" * 16_385)
    with pytest.raises(PersistenceValueError):
        module.guard_release_proof_payload_bytes("x" * 16_384)


@pytest.mark.parametrize("boundary", ["bind", "result"])
def test_release_payload_adapter_rejects_hostile_str_subclass_without_callback(boundary):
    from sqlalchemy.dialects import sqlite

    from hmt_crypto.persistence.risk_reservation_release_codec import (
        RiskReservationReleaseProofPayload,
    )

    touched = False

    class HostileText(str):
        def encode(self, *args, **kwargs):
            nonlocal touched
            touched = True
            raise AssertionError("hostile text callback executed")

    adapter = RiskReservationReleaseProofPayload()
    method = adapter.process_bind_param if boundary == "bind" else adapter.process_result_value
    with pytest.raises(PersistenceValueError):
        method(HostileText(attempt_set().canonical), sqlite.dialect())
    assert not touched


@pytest.mark.parametrize("boundary", ["bind", "result"])
def test_release_payload_adapter_redacts_non_ascii_failures(boundary):
    from sqlalchemy.dialects import sqlite

    from hmt_crypto.persistence.risk_reservation_release_codec import (
        RiskReservationReleaseProofPayload,
    )

    adapter = RiskReservationReleaseProofPayload()
    method = adapter.process_bind_param if boundary == "bind" else adapter.process_result_value
    with pytest.raises(PersistenceValueError) as error:
        method("private-marker-☃", sqlite.dialect())
    assert "private-marker" not in str(error.value) + repr(error.value)


def test_largest_valid_64_by_64_semantic_vector_stays_within_reviewed_byte_cap():
    module = evidence()
    maximum_integer = 2**63 - 1
    command_payload, command_sha = encode_evidence(
        "intent",
        "PLACE_ORDER",
        1,
        {
            "product_id": maximum_integer,
            "requested_contracts": maximum_integer,
            "side": "sell",
            "order_type": "limit",
            "reduce_only": True,
            "limit_price": Decimal("99999999999999999999.999999999999999999"),
        },
    )
    claim_owner = UUID(int=2**128 - 1)
    leader_ref = UUID(int=2**128 - 2)
    recovery_ref = UUID(int=2**128 - 3)
    attempts = []
    for index in range(64):
        attempt_id = UUID(int=2**128 - 100 - index)
        event_id = UUID(int=2**128 - 1000 - index)
        key = chr(0x10000 + index) + ("\U0001f600" * 127)
        attempts.append(
            attempt(
                id=attempt_id,
                mode="LIVE",
                backend="VENUE",
                simulator_kind=None,
                credential_version=maximum_integer,
                attempt_number=maximum_integer - index,
                correlation_id=UUID(int=2**128 - 2000 - index),
                original_claim_owner=claim_owner,
                original_claim_generation=maximum_integer,
                original_claim_expires_at=PROOF_OBSERVED_AT + timedelta(seconds=1),
                original_leader_ref=leader_ref,
                original_leader_generation=maximum_integer,
                original_recovery_ref=recovery_ref,
                original_recovery_generation=maximum_integer,
                evidence_payload=command_payload,
                evidence_sha256=command_sha,
                events=[
                    event(
                        id=event_id,
                        mode="LIVE",
                        backend="VENUE",
                        attempt_id=attempt_id,
                        event_key=key,
                        observed_at=EVENT_RECORDED_AT,
                        ingester_id=UUID(int=2**128 - 3000 - index),
                        ingester_generation=maximum_integer,
                        reconciliation_ref=UUID(int=2**128 - 4000 - index),
                        ingester_leader_ref=leader_ref,
                        ingester_leader_generation=maximum_integer,
                        ingester_recovery_ref=recovery_ref,
                        ingester_recovery_generation=maximum_integer,
                    )
                ],
            )
        )
    context = attempt_set_values(mode="LIVE", product_id=maximum_integer)
    current = current_intent(
        command_evidence_payload=command_payload,
        command_evidence_sha256=command_sha,
        decision_approved_contracts=maximum_integer,
        decision_approved_side="sell",
        decision_risk_effect="REDUCE_ONLY",
        order_product_id=maximum_integer,
        order_requested_contracts=maximum_integer,
        order_requested_side="sell",
        order_requested_order_type="limit",
        order_requested_limit_price=Decimal("99999999999999999999.999999999999999999"),
        order_requested_reduce_only=True,
    )
    encoded = module.encode_attempt_evidence_set(
        1,
        context,
        attempts,
        current_intent=current,
        observed_command_attempt_count=64,
        locked_attempt_count=64,
        locked_event_count=64,
    )
    assert encoded.attempt_count == encoded.event_count == 64
    assert len(encoded.canonical.encode("ascii")) <= 524_288
    assert "\\ud800\\udc00" in encoded.canonical
    assert "\U0001f600" not in encoded.canonical
    stored_attempts = json.loads(encoded.canonical)["payload"]["attempts"]
    assert all(item["events"][0]["observed_at"] is not None for item in stored_attempts)
    assert (
        module.encode_attempt_evidence_set(
            1,
            context,
            list(reversed(attempts)),
            current_intent=current,
            observed_command_attempt_count=64,
            locked_attempt_count=64,
            locked_event_count=64,
        ).canonical
        == encoded.canonical
    )


def test_release_proof_derives_key_counts_payload_and_digest_and_rejects_overrides():
    module = evidence()
    attempts = attempt_set()
    encoded = release_proof(attempts=attempts)
    assert encoded.attempt_evidence_set_sha256 == attempts.sha256
    assert encoded.order_attempt_evidence_count == 1
    assert encoded.order_attempt_event_evidence_count == 1
    assert encoded.observed_command_attempt_count == 1
    assert encoded.release_key == module.derive_release_key(
        1,
        {
            key: value
            for key, value in release_values().items()
            if key
            in {
                "account_id",
                "command_id",
                "command_operation_kind",
                "decision_id",
                "environment",
                "mode",
                "observed_terminal_command_status",
                "order_id",
                "product_id",
                "region",
                "reservation_id",
                "reservation_key",
            }
        },
    )
    module.validate_risk_reservation_release_proof(1, encoded.payload, encoded.sha256)
    assert module.decode_risk_reservation_release_proof(encoded.payload) == encoded
    for forbidden in (
        "release_key",
        "release_reason_code",
        "release_proof_version",
        "release_proof_payload",
        "release_proof_sha256",
        "observed_command_attempt_count",
    ):
        with pytest.raises(PersistenceValueError):
            release_proof(values=release_values(**{forbidden: "caller-owned"}), attempts=attempts)


def test_valid_modify_reduce_only_initial_plan_and_complete_release_vector():
    module = evidence()
    payload, digest = encode_evidence(
        "intent",
        "MODIFY_ORDER",
        1,
        {
            "product_id": 1101,
            "target_order_id": ORDER_ID,
            "requested_contracts": 2,
        },
    )
    current = current_intent(
        command_evidence_payload=payload,
        command_evidence_sha256=digest,
        decision_risk_effect="REDUCE_ONLY",
        order_origin_command_id=uuid4(),
        order_requested_reduce_only=True,
    )
    complete_set = module.encode_attempt_evidence_set(
        1,
        attempt_set_values(command_operation_kind="MODIFY_ORDER"),
        [
            attempt(
                operation_kind="MODIFY_ORDER",
                evidence_payload=payload,
                evidence_sha256=digest,
            )
        ],
        current_intent=current,
        observed_command_attempt_count=1,
        locked_attempt_count=1,
        locked_event_count=1,
    )
    key_values = reservation_key_values(
        command_operation_kind="MODIFY_ORDER",
        risk_effect="REDUCE_ONLY",
    )
    initial = release_values(
        command_operation_kind="MODIFY_ORDER",
        risk_effect="REDUCE_ONLY",
        reservation_key=module.derive_reservation_key(1, key_values),
        planned_incremental_gross_exposure=Decimal("0"),
        planned_worst_case_loss=Decimal("0"),
        planned_collateral=Decimal("0"),
        planned_open_order_slots=0,
        remaining_incremental_gross_exposure=Decimal("0"),
        remaining_worst_case_loss=Decimal("0"),
        remaining_collateral=Decimal("0"),
        remaining_open_order_slots=0,
    )
    proof = release_proof(values=initial, attempts=complete_set, current=current)
    raw = json.loads(proof.payload)
    assert raw["command_operation_kind"] == "MODIFY_ORDER"
    assert raw["risk_effect"] == "REDUCE_ONLY"
    assert raw["planned_open_order_slots"] == raw["released_open_order_slots"] == 0
    assert raw["planned_incremental_gross_exposure"] == "0"
    assert module.decode_risk_reservation_release_proof(proof.payload) == proof


def test_release_proof_requires_full_capacity_and_canonical_size_boundary():
    module = evidence()
    attempts = attempt_set()
    for field, value in (
        ("remaining_contracts", 1),
        ("remaining_collateral", release_values()["remaining_collateral"] / 2),
        ("pending_rate_units", 0),
        ("observed_terminal_command_status", "COMPLETED"),
    ):
        with pytest.raises(PersistenceValueError):
            release_proof(values=release_values(**{field: value}), attempts=attempts)
    encoded = release_proof(attempts=attempts)
    oversized = encoded.payload + (" " * (16_385 - len(encoded.payload.encode("ascii"))))
    with pytest.raises(PersistenceValueError):
        module.decode_risk_reservation_release_proof(oversized)


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision_recorded_at", release_values()["decision_evaluated_at"] - timedelta(seconds=1)),
        ("reservation_created_at", release_values()["decision_recorded_at"] - timedelta(seconds=1)),
        ("proof_observed_at", release_values()["reservation_created_at"] - timedelta(seconds=1)),
        ("reservation_created_at", release_values()["rate_window_end_at"]),
    ],
)
def test_release_proof_enforces_the_complete_decision_creation_cutoff_time_chain(field, value):
    with pytest.raises(PersistenceValueError):
        release_proof(values=release_values(**{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision_outcome", "DENIED"),
        ("risk_effect", "REDUCE_ONLY"),
        ("approved_side", "BUY"),
        ("product_id", 0),
        ("reference_asset_symbol", ""),
        ("planned_open_order_slots", 0),
        ("reservation_key", "e" * 64),
        ("released_contracts", 1),
    ],
)
def test_release_decoder_rejects_canonical_but_semantically_corrupt_payload(field, value):
    module = evidence()
    encoded = release_proof()
    raw = json.loads(encoded.payload)
    raw[field] = value
    corrupt = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    with pytest.raises(PersistenceValueError):
        module.decode_risk_reservation_release_proof(corrupt)


@pytest.mark.parametrize(
    "operation",
    [
        lambda: evidence().derive_reservation_key(1, reservation_key_values(product_id=True)),
        lambda: evidence().derive_reservation_key(
            1, reservation_key_values(account_id=str(uuid4()))
        ),
        lambda: evidence().AttemptEventEvidenceV1.model_validate(event(sequence=True)),
        lambda: evidence().AttemptEventEvidenceV1.model_validate(event(id=str(uuid4()))),
        lambda: evidence().encode_risk_reservation_release_proof(
            1,
            release_values(observed_command_version=True),
            attempt_set(),
            current_intent=current_intent(),
        ),
    ],
)
def test_hostile_or_coercive_types_are_rejected_without_normalization(operation):
    with pytest.raises(PersistenceValueError):
        operation()


def test_exact_maximum_bigints_and_decimals_are_admitted_without_rounding():
    module = evidence()
    maximum_integer = 2**63 - 1
    assert module.derive_reservation_key(1, reservation_key_values(product_id=maximum_integer))
    maximal_attempt = attempt(attempt_number=maximum_integer)
    assert attempt_set([maximal_attempt]).attempt_count == 1
    maximum_decimal = Decimal("99999999999999999999.999999999999999999")
    maximal_values = release_values(
        planned_incremental_gross_exposure=maximum_decimal,
        planned_worst_case_loss=maximum_decimal,
        planned_collateral=maximum_decimal,
        remaining_incremental_gross_exposure=maximum_decimal,
        remaining_worst_case_loss=maximum_decimal,
        remaining_collateral=maximum_decimal,
        observed_command_version=maximum_integer,
        observed_order_version=maximum_integer,
    )
    proof = release_proof(values=maximal_values)
    assert module.decode_risk_reservation_release_proof(proof.payload) == proof
    with pytest.raises(PersistenceValueError):
        module.derive_reservation_key(1, reservation_key_values(product_id=maximum_integer + 1))
    with pytest.raises(PersistenceValueError):
        module.encode_risk_reservation_release_proof(
            1,
            release_values(
                planned_collateral=Decimal("100000000000000000000"),
                remaining_collateral=Decimal("100000000000000000000"),
            ),
            attempt_set(),
            current_intent=current_intent(),
        )


def test_contracts_are_frozen_strict_and_revalidate_unsafe_models():
    module = evidence()
    contract = module.AttemptEventEvidenceV1.model_validate(event())
    with pytest.raises(ValidationError):
        contract.sequence = 2
    invalid = contract.model_copy(update={"sequence": "1"})
    with pytest.raises(PersistenceValueError):
        module.AttemptEventEvidenceV1.model_validate(invalid)
