"""Exhaustive strict-time boundaries for D9c pure helpers."""

from datetime import UTC, datetime, timedelta

import pytest
from risk_rate_consumption_test_support import (
    ATTEMPT_RECORDED,
    WINDOW_END,
    WINDOW_START,
)

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.risk_rate_consumption_evidence import (
    same_sample_strictly_before_expiries,
    windows_overlap,
)


class _HostileDateTime(datetime):
    callback_executed = False

    def astimezone(self, *args, **kwargs):
        type(self).callback_executed = True
        raise RuntimeError("private-time-marker")


HOSTILE_TIME_KINDS = ("text", "integer", "none", "naive", "subclass")
EXPIRY_HOSTILE_TIME_KINDS = tuple(kind for kind in HOSTILE_TIME_KINDS if kind != "none")


def _hostile_time(kind, position, *, end_positions):
    boundary = WINDOW_END if position in end_positions else WINDOW_START
    if kind == "text":
        return boundary.isoformat()
    if kind == "integer":
        return 1
    if kind == "none":
        return None
    if kind == "naive":
        return boundary.replace(tzinfo=None)
    assert kind == "subclass"
    return _HostileDateTime(
        boundary.year,
        boundary.month,
        boundary.day,
        boundary.hour,
        boundary.minute,
        boundary.second,
        boundary.microsecond,
        tzinfo=UTC,
    )


@pytest.mark.parametrize(
    "sample,claim,lease,expected",
    [
        (
            ATTEMPT_RECORDED,
            ATTEMPT_RECORDED + timedelta(microseconds=1),
            WINDOW_END,
            True,
        ),
        (ATTEMPT_RECORDED, ATTEMPT_RECORDED, WINDOW_END, False),
        (
            ATTEMPT_RECORDED,
            ATTEMPT_RECORDED - timedelta(microseconds=1),
            WINDOW_END,
            False,
        ),
        (
            ATTEMPT_RECORDED,
            WINDOW_END,
            ATTEMPT_RECORDED + timedelta(microseconds=1),
            True,
        ),
        (ATTEMPT_RECORDED, WINDOW_END, ATTEMPT_RECORDED, False),
        (
            ATTEMPT_RECORDED,
            WINDOW_END,
            ATTEMPT_RECORDED - timedelta(microseconds=1),
            False,
        ),
    ],
    ids=(
        "claim-sample-before",
        "claim-sample-equal",
        "claim-sample-after",
        "lease-sample-before",
        "lease-sample-equal",
        "lease-sample-after",
    ),
)
def test_claim_and_lease_expiry_boundaries_are_independently_strict(sample, claim, lease, expected):
    assert same_sample_strictly_before_expiries(sample, claim, lease) is expected


@pytest.mark.parametrize("position", range(4))
@pytest.mark.parametrize("kind", HOSTILE_TIME_KINDS)
def test_windows_overlap_strictly_rejects_hostile_value_in_every_argument(position, kind):
    _HostileDateTime.callback_executed = False
    arguments = [WINDOW_START, WINDOW_END, WINDOW_START, WINDOW_END]
    arguments[position] = _hostile_time(kind, position, end_positions={1, 3})

    with pytest.raises(PersistenceValueError) as error:
        windows_overlap(*arguments)

    assert not _HostileDateTime.callback_executed
    assert "private-time-marker" not in str(error.value) + repr(error.value)


@pytest.mark.parametrize("position", range(3))
@pytest.mark.parametrize("kind", EXPIRY_HOSTILE_TIME_KINDS)
def test_expiry_helper_strictly_rejects_hostile_value_in_every_argument(position, kind):
    _HostileDateTime.callback_executed = False
    arguments = [ATTEMPT_RECORDED, WINDOW_END, WINDOW_END]
    arguments[position] = _hostile_time(kind, position, end_positions={1, 2})

    with pytest.raises(PersistenceValueError) as error:
        same_sample_strictly_before_expiries(*arguments)

    assert not _HostileDateTime.callback_executed
    assert "private-time-marker" not in str(error.value) + repr(error.value)


@pytest.mark.parametrize("position", [0, 1])
def test_expiry_helper_rejects_none_in_each_required_argument(position):
    arguments = [ATTEMPT_RECORDED, WINDOW_END, WINDOW_END]
    arguments[position] = None

    with pytest.raises(PersistenceValueError):
        same_sample_strictly_before_expiries(*arguments)
