"""SQLite/PostgreSQL DDL and pure factory acceptance for immutable safe copies."""

from __future__ import annotations

from inspect import signature

import pytest
from api_audit_payload_schema_support import public_payload, public_source
from sqlalchemy import ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from hmt_crypto.persistence.api_audit_models import ApiAuditEvent
from hmt_crypto.persistence.api_audit_payload_models import ApiAuditPayload
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.models import metadata

pytestmark = pytest.mark.integration


def _foreign_key(name: str) -> ForeignKeyConstraint:
    for constraint in ApiAuditPayload.__table__.constraints:
        if isinstance(constraint, ForeignKeyConstraint) and constraint.name == name:
            return constraint
    raise AssertionError(name)


def _unique(model: type[object], name: str) -> tuple[str, ...]:
    for constraint in model.__table__.constraints:  # type: ignore[attr-defined]
        if isinstance(constraint, UniqueConstraint) and constraint.name == name:
            return tuple(column.name for column in constraint.columns)
    raise AssertionError(name)


def test_payload_table_has_exact_columns_nullability_and_keys() -> None:
    expected = (
        "owning_event_id",
        "context_kind",
        "region",
        "environment",
        "account_id",
        "mode",
        "payload_slot",
        "payload_schema_version",
        "payload_text",
        "payload_sha256",
        "payload_byte_length",
        "payload_expires_at",
        "payload_ttl_seconds",
        "retention_policy_version",
        "copy_key",
    )
    assert tuple(ApiAuditPayload.__table__.columns.keys()) == expected
    assert {column.name for column in ApiAuditPayload.__table__.columns if column.nullable} == {
        "account_id",
        "mode",
    }
    primary = next(
        constraint
        for constraint in ApiAuditPayload.__table__.constraints
        if isinstance(constraint, PrimaryKeyConstraint)
    )
    assert tuple(column.name for column in primary.columns) == (
        "owning_event_id",
        "payload_slot",
    )
    assert _unique(ApiAuditPayload, "uq_api_audit_payloads_copy_key") == ("copy_key",)
    assert not {
        "id",
        "created_at",
        "updated_at",
        "version",
        "raw_body",
        "headers",
        "actor_role_oid",
    } & set(ApiAuditPayload.__table__.columns)


def test_payload_table_has_exact_named_checks_and_no_delete_cascade() -> None:
    names = {constraint.name for constraint in ApiAuditPayload.__table__.constraints}
    assert {
        "ck_api_audit_payloads_context_shape",
        "ck_api_audit_payloads_payload_slot",
        "ck_api_audit_payloads_payload_schema_version",
        "ck_api_audit_payloads_payload_text_bounded",
        "ck_api_audit_payloads_payload_text_ascii",
        "ck_api_audit_payloads_payload_sha256",
        "ck_api_audit_payloads_payload_byte_length",
        "ck_api_audit_payloads_payload_ttl_seconds",
        "ck_api_audit_payloads_retention_policy_version",
        "ck_api_audit_payloads_copy_key_sha256",
    } <= names
    for foreign_key in (
        constraint
        for constraint in ApiAuditPayload.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ):
        assert foreign_key.ondelete == "RESTRICT"
        assert foreign_key.onupdate == "RESTRICT"


def test_public_and_account_descriptor_foreign_keys_are_exact_unfiltered_candidates() -> None:
    descriptor = (
        "payload_slot",
        "payload_schema_version",
        "payload_sha256",
        "payload_byte_length",
        "payload_expires_at",
        "payload_ttl_seconds",
        "retention_policy_version",
    )
    public = ("context_kind", "region", "environment")
    account = ("context_kind", "account_id", "region", "environment", "mode")
    for name, context in (
        ("fk_api_audit_payloads_descriptor_public", public),
        ("fk_api_audit_payloads_descriptor_account", account),
    ):
        foreign_key = _foreign_key(name)
        assert tuple(column.name for column in foreign_key.columns) == (
            "owning_event_id",
            *context,
            *descriptor,
        )
        assert tuple(element.target_fullname for element in foreign_key.elements) == tuple(
            f"hmt_crypto.api_audit_events.{column}" for column in ("id", *context, *descriptor)
        )
    account_scope = _foreign_key("fk_api_audit_payloads_account_scope")
    assert tuple(column.name for column in account_scope.columns) == (
        "account_id",
        "region",
        "environment",
        "mode",
    )
    assert _unique(ApiAuditEvent, "uq_api_audit_events_descriptor_public") == (
        "id",
        *public,
        *descriptor,
    )
    assert _unique(ApiAuditEvent, "uq_api_audit_events_descriptor_account") == (
        "id",
        *account,
        *descriptor,
    )


def test_payload_table_compiles_for_sqlite_and_postgresql_and_is_exported() -> None:
    table = ApiAuditPayload.__table__
    for dialect in (sqlite.dialect(), postgresql.dialect()):
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "api_audit_payloads" in ddl
        assert "context_shape" in ddl
        assert "payload_text_ascii" in ddl
        assert "ON DELETE RESTRICT" in ddl
        assert "ON UPDATE RESTRICT" in ddl
    assert metadata.tables["hmt_crypto.api_audit_payloads"] is table


def test_factory_derives_every_copy_field_and_accepts_no_overrides() -> None:
    row = public_payload()
    assert row.owning_event_id == public_source().owning_event_id
    assert row.context_kind == "PUBLIC"
    assert row.account_id is None and row.mode is None
    assert row.payload_slot == "REQUEST"
    assert row.payload_schema_version == 1
    assert row.payload_text.encode("ascii") == (
        b'{"method":"GET","path_template":"/v2/products",'
        b'"request_evidence_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"route_id":"products","route_policy":"delta-docs-2026-11-03",'
        b'"schema_version":1,"summary_kind":"VENUE_REQUEST","weight":3}'
    )
    assert row.payload_sha256 == (
        "7bf4a73e1c3b298cc12df0b28c63c9cc3ff44e619bdd612095180835f217e68e"
    )
    assert row.payload_byte_length == 262
    assert row.copy_key == ("912542ff7c8ea8ac8a38df0ca54d504830d5a9b01a9d983b249be2fd4fac9ec2")
    assert tuple(signature(ApiAuditPayload.from_source).parameters) == ("source",)
    with pytest.raises(TypeError):
        ApiAuditPayload.from_source(public_source(), payload_text="secret")  # type: ignore[call-arg]
    with pytest.raises(PersistenceValueError):
        ApiAuditPayload.from_source(public_source().model_dump())  # type: ignore[arg-type]
