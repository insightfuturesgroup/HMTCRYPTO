"""Adversarial contract tests for pure operator sessions and CSRF."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone
from types import FrameType, ModuleType, TracebackType
from typing import Any
from uuid import UUID

import pytest

from hmt_crypto.api import operator_sessions as sessions_module
from hmt_crypto.api.operator_sessions import (
    OperatorSessionRecord,
    SessionDecisionReason,
    SessionInputError,
    authenticate_session,
    authorize_csrf,
    issue_session,
    revoke_session,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
SESSION_ID = UUID("10000000-0000-0000-0000-000000000001")
TOKEN_BYTES = bytes(range(32))
CSRF_BYTES = bytes(range(32, 64))
TOKEN_DOMAIN = b"hmt-crypto:operator-session-token:v1\x00"
CSRF_DOMAIN = b"hmt-crypto:operator-session-csrf:v1\x00"


class _FatalProbe(BaseException):
    pass


def _token(entropy: bytes) -> str:
    return base64.urlsafe_b64encode(entropy).rstrip(b"=").decode("ascii")


def _issued() -> Any:
    return issue_session(
        session_id=SESSION_ID,
        token_entropy=TOKEN_BYTES,
        csrf_entropy=CSRF_BYTES,
        now=NOW,
        idle_timeout=timedelta(minutes=15),
        absolute_timeout=timedelta(hours=2),
    )


def _production_locals(error: BaseException) -> list[dict[str, Any]]:
    """Collect locals from every reachable public production traceback frame."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    result: list[dict[str, Any]] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        traceback = current.__traceback__
        while traceback is not None:
            filename = traceback.tb_frame.f_code.co_filename
            if "/src/hmt_crypto/api/" in filename:
                result.append(dict(traceback.tb_frame.f_locals))
            traceback = traceback.tb_next
        cause = current.__cause__
        context = current.__context__
        if cause is not None:
            pending.append(cause)
        if context is not None:
            pending.append(context)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
    return result


def _contains_raw(value: object, secrets: tuple[object, ...], *, depth: int = 12) -> bool:
    """Conservatively search a retained object graph for exact secret values."""
    if depth < 0:
        return False
    for secret in secrets:
        if type(value) is type(secret) and value == secret:
            return True
    if isinstance(value, (ModuleType, FrameType, TracebackType)) or callable(value):
        return False
    if isinstance(value, dict):
        return any(
            _contains_raw(item, secrets, depth=depth - 1) for pair in value.items() for item in pair
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_raw(item, secrets, depth=depth - 1) for item in value)
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict) and _contains_raw(attributes, secrets, depth=depth - 1):
        return True
    slots = getattr(type(value), "__slots__", ())
    if isinstance(slots, str):
        slots = (slots,)
    for slot in slots:
        if slot in {"__weakref__", "__dict__"}:
            continue
        attribute = slot
        if slot.startswith("__") and not slot.endswith("__"):
            attribute = f"_{type(value).__name__.lstrip('_')}{slot}"
        try:
            child = getattr(value, attribute)
        except (AttributeError, TypeError):
            continue
        if _contains_raw(child, secrets, depth=depth - 1):
            return True
    return False


def _assert_secret_free_failure(error: BaseException, *secrets: object) -> None:
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = f"{error!s}\n{error!r}"
    for secret in secrets:
        secret_rendered = secret if isinstance(secret, str) else repr(secret)
        assert secret_rendered not in rendered
    for local_values in _production_locals(error):
        assert not _contains_raw(local_values, tuple(secrets))


def test_issue_returns_raw_values_once_but_persists_only_domain_hashes() -> None:
    result = _issued()

    assert result.session_token == _token(TOKEN_BYTES)
    assert result.csrf_token == _token(CSRF_BYTES)
    assert result.session_token != result.csrf_token
    assert result.record.token_sha256 == hashlib.sha256(TOKEN_DOMAIN + TOKEN_BYTES).hexdigest()
    assert result.record.csrf_sha256 == hashlib.sha256(CSRF_DOMAIN + CSRF_BYTES).hexdigest()
    assert result.record.token_sha256 != hashlib.sha256(TOKEN_BYTES).hexdigest()
    assert result.record.csrf_sha256 != hashlib.sha256(CSRF_BYTES).hexdigest()
    persisted_names = {field.name for field in fields(result.record)}
    assert persisted_names.isdisjoint(
        {"session_token", "csrf_token", "token_entropy", "csrf_entropy"}
    )
    rendered = f"{result!r} {result!s} {result.record!r}"
    assert result.session_token not in rendered
    assert result.csrf_token not in rendered
    assert repr(TOKEN_BYTES) not in rendered
    assert repr(CSRF_BYTES) not in rendered
    assert result.record.issued_at == NOW
    assert result.record.last_seen_at == NOW
    assert result.record.idle_expires_at == NOW + timedelta(minutes=15)
    assert result.record.absolute_expires_at == NOW + timedelta(hours=2)
    assert result.record.revision == 1


def test_record_is_immutable_and_all_persisted_times_are_canonical_utc() -> None:
    record = _issued().record

    for value in (
        record.issued_at,
        record.last_seen_at,
        record.idle_expires_at,
        record.absolute_expires_at,
    ):
        assert value.tzinfo is UTC
    with pytest.raises(FrozenInstanceError):
        record.revision = 2


def test_invalid_record_construction_retains_no_raw_value_in_public_failure() -> None:
    result = _issued()
    record = result.record

    with pytest.raises(SessionInputError) as caught:
        OperatorSessionRecord(
            session_id=record.session_id,
            token_sha256=result.session_token,
            csrf_sha256=result.csrf_token,
            issued_at=record.issued_at,
            last_seen_at=record.last_seen_at,
            idle_expires_at=record.idle_expires_at,
            absolute_expires_at=record.absolute_expires_at,
            revoked_at=record.revoked_at,
            idle_timeout_seconds=record.idle_timeout_seconds,
            revision=record.revision,
        )

    _assert_secret_free_failure(caught.value, result.session_token, result.csrf_token)


def test_valid_authentication_touches_idle_without_extending_absolute_expiry() -> None:
    result = _issued()

    first = authenticate_session(
        result.record,
        session_token=result.session_token,
        now=NOW + timedelta(minutes=10),
        idle_timeout=timedelta(minutes=15),
    )
    assert first.accepted
    assert first.reason is SessionDecisionReason.ACCEPTED
    assert first.record is not None
    assert first.record.last_seen_at == NOW + timedelta(minutes=10)
    assert first.record.idle_expires_at == NOW + timedelta(minutes=25)
    assert first.record.absolute_expires_at == result.record.absolute_expires_at
    assert first.record.revision == result.record.revision + 1

    current = first
    for minute in range(20, 111, 10):
        assert current.record is not None
        current = authenticate_session(
            current.record,
            session_token=result.session_token,
            now=NOW + timedelta(minutes=minute),
            idle_timeout=timedelta(minutes=15),
        )
        assert current.accepted

    assert current.record is not None
    second = authenticate_session(
        current.record,
        session_token=result.session_token,
        now=NOW + timedelta(hours=1, minutes=55),
        idle_timeout=timedelta(minutes=15),
    )
    assert second.record is not None
    assert second.record.idle_expires_at == result.record.absolute_expires_at
    assert second.record.revision == current.record.revision + 1


@pytest.mark.parametrize(
    ("minutes", "reason"),
    [
        (15, SessionDecisionReason.IDLE_EXPIRED),
        (120, SessionDecisionReason.ABSOLUTE_EXPIRED),
    ],
)
def test_authentication_fails_closed_at_exact_expiry_boundaries(
    minutes: int,
    reason: SessionDecisionReason,
) -> None:
    result = _issued()

    decision = authenticate_session(
        result.record,
        session_token=result.session_token,
        now=NOW + timedelta(minutes=minutes),
        idle_timeout=timedelta(minutes=15),
    )

    assert not decision.accepted
    assert decision.reason is reason
    assert decision.record is None
    assert result.record.revision == 1


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "x",
        _token(TOKEN_BYTES)[:-1] + "A",
        "é" * 43,
        _token(TOKEN_BYTES) + "=",
        7,
    ],
)
def test_wrong_or_noncanonical_cookie_is_an_opaque_authentication_miss(candidate: object) -> None:
    result = _issued()

    decision = authenticate_session(
        result.record,
        session_token=candidate,
        now=NOW + timedelta(minutes=1),
        idle_timeout=timedelta(minutes=15),
    )

    assert not decision.accepted
    assert decision.reason is SessionDecisionReason.INVALID_TOKEN
    assert decision.record is None
    assert result.record.revision == 1


def test_authentication_and_csrf_use_secure_hash_equality(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _issued()
    original = sessions_module.hmac.compare_digest
    compared: list[tuple[object, object]] = []

    def recording_compare(left: object, right: object) -> bool:
        compared.append((left, right))
        return original(left, right)

    monkeypatch.setattr(sessions_module.hmac, "compare_digest", recording_compare)

    decision = authenticate_session(
        result.record,
        session_token=result.session_token,
        now=NOW + timedelta(minutes=1),
        idle_timeout=timedelta(minutes=15),
    )
    assert decision.accepted
    assert authorize_csrf(
        result.record,
        session_token=result.session_token,
        csrf_token=result.csrf_token,
        now=NOW + timedelta(minutes=1),
        idle_timeout=timedelta(minutes=15),
    )
    assert len(compared) == 3
    assert all(type(left) is str and type(right) is str for left, right in compared)
    assert all(len(left) == 64 and len(right) == 64 for left, right in compared)


def test_csrf_requires_separate_token_and_live_bearer_authentication() -> None:
    result = _issued()
    common = {
        "now": NOW + timedelta(minutes=1),
        "idle_timeout": timedelta(minutes=15),
    }

    assert authorize_csrf(
        result.record,
        session_token=result.session_token,
        csrf_token=result.csrf_token,
        **common,
    )
    assert not authorize_csrf(
        result.record,
        session_token=_token(CSRF_BYTES),
        csrf_token=result.csrf_token,
        **common,
    )
    assert not authorize_csrf(
        result.record,
        session_token=result.session_token,
        csrf_token=result.session_token,
        **common,
    )
    assert not authorize_csrf(
        result.record,
        session_token=result.session_token,
        csrf_token="bad",
        **common,
    )
    assert not authorize_csrf(
        result.record,
        session_token=result.session_token,
        csrf_token=result.csrf_token,
        now=result.record.idle_expires_at,
        idle_timeout=timedelta(minutes=15),
    )
    revoked = revoke_session(result.record, now=NOW + timedelta(seconds=1))
    assert not authorize_csrf(
        revoked,
        session_token=result.session_token,
        csrf_token=result.csrf_token,
        **common,
    )


def test_csrf_fails_closed_when_session_revision_cannot_be_touched() -> None:
    result = _issued()
    exhausted = OperatorSessionRecord(
        session_id=result.record.session_id,
        token_sha256=result.record.token_sha256,
        csrf_sha256=result.record.csrf_sha256,
        issued_at=result.record.issued_at,
        last_seen_at=result.record.last_seen_at,
        idle_expires_at=result.record.idle_expires_at,
        absolute_expires_at=result.record.absolute_expires_at,
        revoked_at=None,
        idle_timeout_seconds=result.record.idle_timeout_seconds,
        revision=(1 << 63) - 1,
    )

    with pytest.raises(SessionInputError):
        authorize_csrf(
            exhausted,
            session_token=result.session_token,
            csrf_token=result.csrf_token,
            now=NOW + timedelta(minutes=1),
            idle_timeout=timedelta(minutes=15),
        )
    assert not authorize_csrf(
        exhausted,
        session_token=_token(CSRF_BYTES),
        csrf_token=result.csrf_token,
        now=NOW + timedelta(minutes=1),
        idle_timeout=timedelta(minutes=15),
    )
    assert not authorize_csrf(
        exhausted,
        session_token=result.session_token,
        csrf_token=_token(TOKEN_BYTES),
        now=NOW + timedelta(minutes=1),
        idle_timeout=timedelta(minutes=15),
    )
    revoked = OperatorSessionRecord(
        session_id=exhausted.session_id,
        token_sha256=exhausted.token_sha256,
        csrf_sha256=exhausted.csrf_sha256,
        issued_at=exhausted.issued_at,
        last_seen_at=exhausted.last_seen_at,
        idle_expires_at=exhausted.idle_expires_at,
        absolute_expires_at=exhausted.absolute_expires_at,
        revoked_at=NOW + timedelta(seconds=1),
        idle_timeout_seconds=exhausted.idle_timeout_seconds,
        revision=exhausted.revision,
    )
    assert not authorize_csrf(
        revoked,
        session_token=result.session_token,
        csrf_token=result.csrf_token,
        now=NOW + timedelta(minutes=1),
        idle_timeout=timedelta(minutes=15),
    )


def test_revoke_is_idempotent_versioned_and_authentication_stays_denied() -> None:
    result = _issued()
    revoked_at = NOW + timedelta(minutes=1)

    revoked = revoke_session(result.record, now=revoked_at)
    assert revoked.revoked_at == revoked_at
    assert revoked.revision == result.record.revision + 1
    assert revoke_session(revoked, now=revoked_at + timedelta(minutes=1)) is revoked
    decision = authenticate_session(
        revoked,
        session_token=result.session_token,
        now=revoked_at + timedelta(minutes=1),
        idle_timeout=timedelta(minutes=15),
    )
    assert not decision.accepted
    assert decision.reason is SessionDecisionReason.REVOKED
    assert decision.record is None


def test_revoked_record_rejects_observation_times_before_revocation() -> None:
    result = _issued()
    revoked = revoke_session(result.record, now=NOW + timedelta(minutes=5))

    with pytest.raises(SessionInputError):
        revoke_session(revoked, now=NOW + timedelta(minutes=4))
    with pytest.raises(SessionInputError):
        authenticate_session(
            revoked,
            session_token=result.session_token,
            now=NOW + timedelta(minutes=4),
            idle_timeout=timedelta(minutes=15),
        )
    with pytest.raises(SessionInputError):
        authorize_csrf(
            revoked,
            session_token=result.session_token,
            csrf_token=result.csrf_token,
            now=NOW + timedelta(minutes=4),
            idle_timeout=timedelta(minutes=15),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"token_entropy": b"short"},
        {"csrf_entropy": b"short"},
        {"token_entropy": bytearray(TOKEN_BYTES)},
        {"csrf_entropy": TOKEN_BYTES},
        {"now": datetime(2026, 11, 6, 12, 0)},
        {"now": datetime(2026, 11, 6, 13, 0, tzinfo=timezone(timedelta(hours=1)))},
        {"now": datetime(1999, 12, 31, 23, 59, tzinfo=UTC)},
        {"idle_timeout": timedelta(0)},
        {"idle_timeout": timedelta(seconds=1, microseconds=1)},
        {"idle_timeout": timedelta(hours=25)},
        {"absolute_timeout": timedelta(minutes=10)},
        {"absolute_timeout": timedelta(days=31)},
        {"session_id": UUID(int=0)},
        {"session_id": str(SESSION_ID)},
    ],
)
def test_issue_rejects_nonexact_or_unbounded_trusted_inputs(overrides: dict[str, object]) -> None:
    arguments: dict[str, object] = {
        "session_id": SESSION_ID,
        "token_entropy": TOKEN_BYTES,
        "csrf_entropy": CSRF_BYTES,
        "now": NOW,
        "idle_timeout": timedelta(minutes=15),
        "absolute_timeout": timedelta(hours=2),
    }
    arguments.update(overrides)

    with pytest.raises(SessionInputError, match="invalid operator session input"):
        issue_session(**arguments)


@pytest.mark.parametrize(
    ("record", "now", "idle_timeout"),
    [
        (object(), NOW, timedelta(minutes=15)),
        (_issued().record, datetime(2026, 11, 6, 12, 1), timedelta(minutes=15)),
        (_issued().record, NOW - timedelta(seconds=1), timedelta(minutes=15)),
        (_issued().record, NOW + timedelta(minutes=1), timedelta(days=31)),
        (_issued().record, NOW + timedelta(minutes=1), timedelta(minutes=16)),
    ],
)
def test_authentication_rejects_invalid_trusted_record_time_or_policy(
    record: object,
    now: datetime,
    idle_timeout: timedelta,
) -> None:
    result = _issued()
    with pytest.raises(SessionInputError, match="invalid operator session input"):
        authenticate_session(
            record,
            session_token=result.session_token,
            now=now,
            idle_timeout=idle_timeout,
        )


@pytest.mark.parametrize(
    "now",
    [datetime(2026, 11, 6, 12, 1), NOW - timedelta(seconds=1)],
)
def test_revoke_rejects_noncanonical_or_regressing_time(now: datetime) -> None:
    with pytest.raises(SessionInputError, match="invalid operator session input"):
        revoke_session(_issued().record, now=now)


@pytest.mark.parametrize("operation", ["issue", "authenticate", "csrf"])
def test_public_input_failures_retain_no_raw_secret_in_exception_graph_or_locals(
    operation: str,
) -> None:
    result = _issued()
    with pytest.raises(SessionInputError) as caught:
        if operation == "issue":
            issue_session(
                session_id=SESSION_ID,
                token_entropy=TOKEN_BYTES,
                csrf_entropy=CSRF_BYTES,
                now=datetime(2026, 11, 6, 12, 0),
                idle_timeout=timedelta(minutes=15),
                absolute_timeout=timedelta(hours=2),
            )
        elif operation == "authenticate":
            authenticate_session(
                result.record,
                session_token=result.session_token,
                now=datetime(2026, 11, 6, 12, 1),
                idle_timeout=timedelta(minutes=15),
            )
        else:
            authorize_csrf(
                result.record,
                session_token=result.session_token,
                csrf_token=result.csrf_token,
                now=datetime(2026, 11, 6, 12, 1),
                idle_timeout=timedelta(minutes=15),
            )

    _assert_secret_free_failure(
        caught.value,
        TOKEN_BYTES,
        CSRF_BYTES,
        result.session_token,
        result.csrf_token,
    )


@pytest.mark.parametrize("operation", ["issue", "authenticate", "csrf", "revoke"])
def test_public_failures_scrub_secrets_even_when_supplied_in_an_untrusted_wrong_slot(
    operation: str,
) -> None:
    result = _issued()
    with pytest.raises(SessionInputError) as caught:
        if operation == "issue":
            issue_session(
                session_id=TOKEN_BYTES,
                token_entropy=TOKEN_BYTES,
                csrf_entropy=CSRF_BYTES,
                now=NOW,
                idle_timeout=timedelta(minutes=15),
                absolute_timeout=timedelta(hours=2),
            )
        elif operation == "authenticate":
            authenticate_session(
                result.session_token,
                session_token=result.session_token,
                now=NOW,
                idle_timeout=timedelta(minutes=15),
            )
        elif operation == "csrf":
            authorize_csrf(
                result.record,
                session_token=result.session_token,
                csrf_token=result.csrf_token,
                now=result.csrf_token,
                idle_timeout=timedelta(minutes=15),
            )
        else:
            revoke_session(result.csrf_token, now=NOW)

    _assert_secret_free_failure(
        caught.value,
        TOKEN_BYTES,
        CSRF_BYTES,
        result.session_token,
        result.csrf_token,
    )


@pytest.mark.parametrize("operation", ["issue", "authenticate", "csrf", "revoke"])
def test_public_wrappers_sanitize_baseexception_before_helpers_return(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    result = _issued()

    def interrupt(*_args: object, **_kwargs: object) -> object:
        raise _FatalProbe

    helper_name = {
        "issue": "_try_issue",
        "authenticate": "_try_authenticate",
        "csrf": "_try_authorize_csrf",
        "revoke": "_try_revoke",
    }[operation]
    monkeypatch.setattr(sessions_module, helper_name, interrupt)
    with pytest.raises(SessionInputError) as caught:
        if operation == "issue":
            issue_session(
                session_id=SESSION_ID,
                token_entropy=TOKEN_BYTES,
                csrf_entropy=CSRF_BYTES,
                now=NOW,
                idle_timeout=timedelta(minutes=15),
                absolute_timeout=timedelta(hours=2),
            )
        elif operation == "authenticate":
            authenticate_session(
                result.record,
                session_token=result.session_token,
                now=NOW,
                idle_timeout=timedelta(minutes=15),
            )
        elif operation == "csrf":
            authorize_csrf(
                result.record,
                session_token=result.session_token,
                csrf_token=result.csrf_token,
                now=NOW,
                idle_timeout=timedelta(minutes=15),
            )
        else:
            revoke_session(result.session_token, now=result.csrf_token)
    _assert_secret_free_failure(
        caught.value,
        TOKEN_BYTES,
        CSRF_BYTES,
        result.session_token,
        result.csrf_token,
    )


@pytest.mark.parametrize(
    "boundary",
    [
        "issue_session_id_validation",
        "issue_entropy_validation",
        "issue_time_validation",
        "issue_duration_validation",
        "issue_encode",
        "issue_hash",
        "authenticate_record_validation",
        "authenticate_time_validation",
        "authenticate_duration_validation",
        "authenticate_decode",
        "authenticate_compare",
        "authenticate_replace",
        "csrf_record_validation",
        "csrf_time_validation",
        "csrf_duration_validation",
        "csrf_second_hash",
        "csrf_second_compare",
        "revoke_validate",
        "revoke_time_validation",
        "revoke_replace",
    ],
)
def test_internal_untrusted_boundaries_sanitize_baseexception_recursively(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    result = _issued()
    comparison_count = 0
    hash_count = 0
    original_token_hash = sessions_module._token_hash

    def interrupt(*_args: object, **_kwargs: object) -> object:
        raise _FatalProbe

    def interrupt_second_compare(_left: object, _right: object) -> bool:
        nonlocal comparison_count
        comparison_count += 1
        if comparison_count == 2:
            raise _FatalProbe
        return True

    def interrupt_second_hash(token: object, *, domain: bytes) -> str | None:
        nonlocal hash_count
        hash_count += 1
        if hash_count == 2:
            raise _FatalProbe
        return original_token_hash(token, domain=domain)

    if boundary == "issue_session_id_validation":
        monkeypatch.setattr(sessions_module, "_valid_session_id", interrupt)
    elif boundary == "issue_entropy_validation":
        monkeypatch.setattr(sessions_module, "_valid_entropy", interrupt)
    elif boundary == "issue_time_validation":
        monkeypatch.setattr(sessions_module, "_canonical_utc", interrupt)
    elif boundary == "issue_duration_validation":
        monkeypatch.setattr(sessions_module, "_duration_seconds", interrupt)
    elif boundary == "issue_encode":
        monkeypatch.setattr(sessions_module, "_encode_token", interrupt)
    elif boundary == "issue_hash":
        monkeypatch.setattr(sessions_module, "_domain_hash", interrupt)
    elif boundary == "authenticate_record_validation":
        monkeypatch.setattr(sessions_module, "_valid_record", interrupt)
    elif boundary == "authenticate_time_validation":
        monkeypatch.setattr(sessions_module, "_canonical_utc", interrupt)
    elif boundary == "authenticate_duration_validation":
        monkeypatch.setattr(sessions_module, "_duration_seconds", interrupt)
    elif boundary == "authenticate_decode":
        monkeypatch.setattr(sessions_module, "_token_hash", interrupt)
    elif boundary == "authenticate_compare":
        monkeypatch.setattr(sessions_module.hmac, "compare_digest", interrupt)
    elif boundary == "authenticate_replace":
        monkeypatch.setattr(sessions_module, "replace", interrupt)
    elif boundary == "csrf_record_validation":
        monkeypatch.setattr(sessions_module, "_valid_record", interrupt)
    elif boundary == "csrf_time_validation":
        monkeypatch.setattr(sessions_module, "_canonical_utc", interrupt)
    elif boundary == "csrf_duration_validation":
        monkeypatch.setattr(sessions_module, "_duration_seconds", interrupt)
    elif boundary == "csrf_second_hash":
        monkeypatch.setattr(sessions_module, "_token_hash", interrupt_second_hash)
    elif boundary == "csrf_second_compare":
        monkeypatch.setattr(
            sessions_module.hmac,
            "compare_digest",
            interrupt_second_compare,
        )
    elif boundary == "revoke_validate":
        monkeypatch.setattr(sessions_module, "_valid_record", interrupt)
    elif boundary == "revoke_time_validation":
        monkeypatch.setattr(sessions_module, "_canonical_utc", interrupt)
    else:
        monkeypatch.setattr(sessions_module, "replace", interrupt)

    with pytest.raises(SessionInputError) as caught:
        if boundary.startswith("issue_"):
            issue_session(
                session_id=SESSION_ID,
                token_entropy=TOKEN_BYTES,
                csrf_entropy=CSRF_BYTES,
                now=NOW,
                idle_timeout=timedelta(minutes=15),
                absolute_timeout=timedelta(hours=2),
            )
        elif boundary.startswith("authenticate_"):
            authenticate_session(
                result.record,
                session_token=result.session_token,
                now=NOW + timedelta(seconds=1),
                idle_timeout=timedelta(minutes=15),
            )
        elif boundary.startswith("csrf_"):
            authorize_csrf(
                result.record,
                session_token=result.session_token,
                csrf_token=result.csrf_token,
                now=NOW + timedelta(seconds=1),
                idle_timeout=timedelta(minutes=15),
            )
        else:
            revoke_session(result.record, now=NOW + timedelta(seconds=1))
    _assert_secret_free_failure(
        caught.value,
        TOKEN_BYTES,
        CSRF_BYTES,
        result.session_token,
        result.csrf_token,
    )


def test_record_constructor_sanitizes_baseexception_from_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _issued()

    def interrupt(**_kwargs: object) -> bool:
        raise _FatalProbe

    monkeypatch.setattr(sessions_module, "_valid_record_fields", interrupt)
    with pytest.raises(SessionInputError) as caught:
        OperatorSessionRecord(
            session_id=result.session_token,
            token_sha256=result.session_token,
            csrf_sha256=result.csrf_token,
            issued_at=NOW,
            last_seen_at=NOW,
            idle_expires_at=NOW + timedelta(minutes=15),
            absolute_expires_at=NOW + timedelta(hours=2),
            revoked_at=None,
            idle_timeout_seconds=900,
            revision=1,
        )
    _assert_secret_free_failure(caught.value, result.session_token, result.csrf_token)


@pytest.mark.parametrize("extra_microseconds", [1, 999_999])
def test_record_rejects_fractional_absolute_duration_and_maximum_overrun(
    extra_microseconds: int,
) -> None:
    result = _issued()
    absolute = (
        NOW + timedelta(hours=2, microseconds=extra_microseconds)
        if extra_microseconds == 1
        else NOW + timedelta(days=30, microseconds=extra_microseconds)
    )
    with pytest.raises(SessionInputError):
        OperatorSessionRecord(
            session_id=result.record.session_id,
            token_sha256=result.record.token_sha256,
            csrf_sha256=result.record.csrf_sha256,
            issued_at=NOW,
            last_seen_at=NOW,
            idle_expires_at=NOW + timedelta(minutes=15),
            absolute_expires_at=absolute,
            revoked_at=None,
            idle_timeout_seconds=900,
            revision=1,
        )


@pytest.mark.parametrize(
    ("last_seen", "revoked_at", "revision"),
    [
        (NOW + timedelta(seconds=1), None, 1),
        (NOW, NOW + timedelta(seconds=1), 1),
        (NOW + timedelta(seconds=1), NOW + timedelta(seconds=2), 2),
    ],
)
def test_record_rejects_revision_counts_below_reachable_state(
    last_seen: datetime,
    revoked_at: datetime | None,
    revision: int,
) -> None:
    result = _issued()
    with pytest.raises(SessionInputError):
        OperatorSessionRecord(
            session_id=result.record.session_id,
            token_sha256=result.record.token_sha256,
            csrf_sha256=result.record.csrf_sha256,
            issued_at=NOW,
            last_seen_at=last_seen,
            idle_expires_at=min(
                last_seen + timedelta(minutes=15),
                NOW + timedelta(hours=2),
            ),
            absolute_expires_at=NOW + timedelta(hours=2),
            revoked_at=revoked_at,
            idle_timeout_seconds=900,
            revision=revision,
        )


@pytest.mark.parametrize("operation", ["issue", "authenticate", "csrf", "revoke", "record"])
def test_fold_one_utc_is_rejected_as_noncanonical(operation: str) -> None:
    result = _issued()
    folded = NOW.replace(fold=1)
    with pytest.raises(SessionInputError):
        if operation == "issue":
            issue_session(
                session_id=SESSION_ID,
                token_entropy=TOKEN_BYTES,
                csrf_entropy=CSRF_BYTES,
                now=folded,
                idle_timeout=timedelta(minutes=15),
                absolute_timeout=timedelta(hours=2),
            )
        elif operation == "authenticate":
            authenticate_session(
                result.record,
                session_token=result.session_token,
                now=folded,
                idle_timeout=timedelta(minutes=15),
            )
        elif operation == "csrf":
            authorize_csrf(
                result.record,
                session_token=result.session_token,
                csrf_token=result.csrf_token,
                now=folded,
                idle_timeout=timedelta(minutes=15),
            )
        elif operation == "revoke":
            revoke_session(result.record, now=folded)
        else:
            OperatorSessionRecord(
                session_id=result.record.session_id,
                token_sha256=result.record.token_sha256,
                csrf_sha256=result.record.csrf_sha256,
                issued_at=folded,
                last_seen_at=folded,
                idle_expires_at=folded + timedelta(minutes=15),
                absolute_expires_at=folded + timedelta(hours=2),
                revoked_at=None,
                idle_timeout_seconds=900,
                revision=1,
            )
