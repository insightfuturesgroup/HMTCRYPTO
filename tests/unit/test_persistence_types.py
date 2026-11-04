"""The persistence boundary rejects loss before either dialect sees values."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest


def test_decimal_is_exact_independently_of_ambient_context():
    from sqlalchemy.dialects import postgresql, sqlite

    from hmt_crypto.persistence.types import ExactDecimal

    adapter = ExactDecimal(precision=38, scale=18)
    value = Decimal("12345678901234567890.123456789012345678")
    with localcontext() as context:
        context.prec = 4
        encoded = adapter.process_bind_param(value, sqlite.dialect())
        assert encoded == "12345678901234567890.123456789012345678"
        assert adapter.process_result_value(encoded, sqlite.dialect()) == value
        assert adapter.process_bind_param(value, postgresql.dialect()) == value
    assert adapter.process_bind_param(Decimal("-0.000"), sqlite.dialect()) == "0"
    assert adapter.process_bind_param(Decimal("1.23000"), sqlite.dialect()) == "1.23"


@pytest.mark.parametrize(
    "value",
    [
        True,
        1.2,
        1,
        "1.2",
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("0.0001"),
        Decimal("1000"),
        Decimal("999.9991"),
        Decimal("1e1000000"),
        Decimal("1e-1000000"),
    ],
)
def test_decimal_boundary_rejects_inexact_or_out_of_range_values(value):
    from sqlalchemy.dialects import postgresql, sqlite

    from hmt_crypto.persistence.errors import PersistenceValueError
    from hmt_crypto.persistence.types import ExactDecimal

    for dialect in [sqlite.dialect(), postgresql.dialect()]:
        with pytest.raises(PersistenceValueError):
            ExactDecimal(precision=6, scale=3).process_bind_param(value, dialect)


def test_hostile_decimal_and_datetime_subclasses_are_not_evaluated():
    from sqlalchemy.dialects import sqlite

    from hmt_crypto.persistence.errors import PersistenceValueError
    from hmt_crypto.persistence.types import ExactDecimal, UTCDateTime

    class HostileDecimal(Decimal):
        def is_finite(self):
            raise AssertionError("private-subclass-payload")

    class HostileDateTime(datetime):
        def utcoffset(self):
            raise AssertionError("private-subclass-payload")

    for adapter, value in [
        (ExactDecimal(precision=8, scale=4), HostileDecimal("1.2")),
        (UTCDateTime(), HostileDateTime(2026, 1, 1, tzinfo=UTC)),
    ]:
        with pytest.raises(PersistenceValueError) as error:
            adapter.process_bind_param(value, sqlite.dialect())
        assert "private-subclass-payload" not in str(error.value)


@pytest.mark.parametrize(
    "precision,scale", [(0, 0), (1001, 0), (4, -1), (4, 5), (True, 0), (4, False), (4.0, 2)]
)
def test_invalid_decimal_storage_contract_is_rejected(precision, scale):
    from hmt_crypto.persistence.errors import PersistenceValueError
    from hmt_crypto.persistence.types import ExactDecimal

    with pytest.raises(PersistenceValueError):
        ExactDecimal(precision=precision, scale=scale)


def test_utc_adapter_normalizes_offset_without_losing_microseconds():
    from sqlalchemy.dialects import postgresql, sqlite

    from hmt_crypto.persistence.types import UTCDateTime

    adapter = UTCDateTime()
    value = datetime(2026, 11, 4, 12, 30, 15, 123456, tzinfo=timezone(timedelta(hours=5.5)))
    expected = datetime(2026, 11, 4, 7, 0, 15, 123456, tzinfo=UTC)
    encoded = adapter.process_bind_param(value, sqlite.dialect())
    assert encoded == "2026-11-04T07:00:15.123456+00:00"
    assert adapter.process_result_value(encoded, sqlite.dialect()) == expected
    assert adapter.process_bind_param(value, postgresql.dialect()) == expected


@pytest.mark.parametrize("value", [True, 1, "2026-01-01", datetime(2026, 1, 1)])
def test_utc_adapter_rejects_non_aware_values(value):
    from sqlalchemy.dialects import sqlite

    from hmt_crypto.persistence.errors import PersistenceValueError
    from hmt_crypto.persistence.types import UTCDateTime

    with pytest.raises(PersistenceValueError):
        UTCDateTime().process_bind_param(value, sqlite.dialect())


def test_dialect_ddl_uses_exact_postgres_types_and_sqlite_text():
    from sqlalchemy import Column, MetaData, Table
    from sqlalchemy.dialects import postgresql, sqlite
    from sqlalchemy.schema import CreateTable

    from hmt_crypto.persistence.types import ExactDecimal, UTCDateTime

    table = Table(
        "probe",
        MetaData(),
        Column("amount", ExactDecimal(precision=20, scale=8)),
        Column("observed_at", UTCDateTime()),
    )
    pg = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    local = str(CreateTable(table).compile(dialect=sqlite.dialect()))
    assert "NUMERIC(20, 8)" in pg
    assert "TIMESTAMP WITH TIME ZONE" in pg
    assert local.count("TEXT") == 2


@pytest.mark.parametrize("text", ["NaN", "1e0", "01", "1.0000", "-0", "1000", "0.0001"])
def test_decimal_result_boundary_rejects_corrupt_or_noncanonical_text(text):
    from sqlalchemy.dialects import sqlite

    from hmt_crypto.persistence.errors import PersistenceValueError
    from hmt_crypto.persistence.types import ExactDecimal

    with pytest.raises(PersistenceValueError):
        ExactDecimal(precision=6, scale=3).process_result_value(text, sqlite.dialect())


@pytest.mark.parametrize(
    "text", ["2026-11-04T07:00:15.123456", "2026-11-04T08:00:15.123456+01:00", "secret"]
)
def test_datetime_result_boundary_rejects_corrupt_or_noncanonical_text(text):
    from sqlalchemy.dialects import sqlite

    from hmt_crypto.persistence.errors import PersistenceValueError
    from hmt_crypto.persistence.types import UTCDateTime

    with pytest.raises(PersistenceValueError):
        UTCDateTime().process_result_value(text, sqlite.dialect())


def test_nullable_values_and_exact_numeric_boundaries():
    from sqlalchemy.dialects import postgresql, sqlite

    from hmt_crypto.persistence.types import ExactDecimal, UTCDateTime

    for dialect in [sqlite.dialect(), postgresql.dialect()]:
        for adapter in [ExactDecimal(precision=6, scale=3), UTCDateTime()]:
            assert adapter.process_bind_param(None, dialect) is None
            assert adapter.process_result_value(None, dialect) is None
    adapter = ExactDecimal(precision=6, scale=3)
    assert adapter.process_bind_param(Decimal("-999.999"), sqlite.dialect()) == "-999.999"
    assert adapter.process_bind_param(Decimal("0.001000"), sqlite.dialect()) == "0.001"
    assert (
        ExactDecimal(precision=3, scale=3).process_bind_param(Decimal("0.999"), sqlite.dialect())
        == "0.999"
    )


def test_hostile_timezone_callbacks_are_not_evaluated():
    from datetime import tzinfo

    from sqlalchemy.dialects import sqlite

    from hmt_crypto.persistence.errors import PersistenceValueError
    from hmt_crypto.persistence.types import UTCDateTime

    class HostileZone(tzinfo):
        def utcoffset(self, value):
            raise AssertionError("private-tz-payload")

    value = datetime(2026, 1, 1, tzinfo=HostileZone())
    with pytest.raises(PersistenceValueError) as error:
        UTCDateTime().process_bind_param(value, sqlite.dialect())
    assert "private-tz-payload" not in str(error.value)
