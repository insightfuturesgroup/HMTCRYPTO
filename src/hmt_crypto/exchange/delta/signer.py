"""Byte-exact Delta REST HMAC signing with non-serializable secret holders."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable
from typing import NoReturn, SupportsIndex

from pydantic import SecretStr

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.http_headers import validate_user_agent

EpochClock = Callable[[], int]

_HTTP_TOKEN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")
_HEX_BYTES = frozenset(b"0123456789abcdefABCDEF")
_MAX_EPOCH_SECONDS = 9_999_999_999
_ENCODED_AMBIGUOUS_PATH_BYTES = frozenset({0x00, 0x2E, 0x2F, 0x3F, 0x5C, 0x23, 0x7F})


def _configuration_error(operation: str) -> ConfigurationError:
    return ConfigurationError(context={"operation": operation})


class _SecretSnapshot:
    """Internally owned immutable text whose representations never reveal its value."""

    __value: str
    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        object.__setattr__(self, "_SecretSnapshot__value", value)

    def reveal(self) -> str:
        """Reveal the value only at the cryptographic or transport boundary."""
        return self.__value

    def clone(self) -> _SecretSnapshot:
        """Create a distinct immutable snapshot for a downstream auth object."""
        return _SecretSnapshot(self.__value)

    def __repr__(self) -> str:
        return "_SecretSnapshot([REDACTED])"

    __str__ = __repr__

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise AttributeError("Secret snapshots are immutable")

    def __delattr__(self, _name: str) -> NoReturn:
        raise AttributeError("Secret snapshots are immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("Secret snapshots cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("Secret snapshots cannot be copied")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("Secret snapshots cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("Secret snapshots cannot be serialized")

    def __getstate__(self) -> NoReturn:
        raise TypeError("Secret snapshots cannot be serialized")


def _validate_secret(value: object, *, operation: str) -> _SecretSnapshot:
    if not isinstance(value, SecretStr):
        raise _configuration_error(operation)
    raw = value.get_secret_value()
    if not raw or any(not character.isprintable() for character in raw):
        raise _configuration_error(operation)
    return _SecretSnapshot(raw)


def _validate_api_key(value: object) -> _SecretSnapshot:
    secret = _validate_secret(value, operation="validate_delta_api_key")
    raw = secret.reveal()
    try:
        encoded = raw.encode("ascii")
    except UnicodeEncodeError:
        raise _configuration_error("validate_delta_api_key") from None
    if any(byte <= 0x20 or byte >= 0x7F for byte in encoded):
        raise _configuration_error("validate_delta_api_key")
    return secret


def _validate_method(value: object) -> bytes:
    if not isinstance(value, str) or _HTTP_TOKEN.fullmatch(value) is None:
        raise _configuration_error("validate_delta_signing_method")
    try:
        return value.upper().encode("ascii")
    except UnicodeEncodeError:
        raise _configuration_error("validate_delta_signing_method") from None


def _validate_timestamp(value: object) -> int:
    if type(value) is not int or not 0 < value <= _MAX_EPOCH_SECONDS:
        raise _configuration_error("validate_delta_signing_timestamp")
    return value


def _percent_decoded_bytes(value: bytes, *, operation: str) -> tuple[int, ...]:
    decoded: list[int] = []
    index = 0
    while index < len(value):
        if value[index] != ord("%"):
            index += 1
            continue
        if (
            index + 2 >= len(value)
            or value[index + 1] not in _HEX_BYTES
            or value[index + 2] not in _HEX_BYTES
        ):
            raise _configuration_error(operation)
        decoded.append(int(value[index + 1 : index + 3], 16))
        index += 3
    return tuple(decoded)


def _validate_path(value: object) -> bytes:
    operation = "validate_delta_signing_path"
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        raise _configuration_error(operation)
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        raise _configuration_error(operation) from None
    if (
        value == "/"
        or b"?" in encoded
        or b"#" in encoded
        or b"\\" in encoded
        or b"//" in encoded
        or any(byte <= 0x20 or byte >= 0x7F for byte in encoded)
        or any(segment in {b".", b".."} for segment in encoded.split(b"/"))
    ):
        raise _configuration_error(operation)
    percent_decoded = _percent_decoded_bytes(encoded, operation=operation)
    if any(byte in _ENCODED_AMBIGUOUS_PATH_BYTES or byte < 0x20 for byte in percent_decoded):
        raise _configuration_error(operation)
    return encoded


def _validate_query(value: object) -> bytes:
    operation = "validate_delta_signing_query"
    if type(value) is not bytes:
        raise _configuration_error(operation)
    if not value:
        return value
    if value == b"?" or not value.startswith(b"?"):
        raise _configuration_error(operation)
    if b"#" in value or any(byte <= 0x20 or byte >= 0x7F for byte in value):
        raise _configuration_error(operation)
    _percent_decoded_bytes(value, operation=operation)
    return value


def _validate_payload(value: object) -> bytes:
    if type(value) is not bytes:
        raise _configuration_error("validate_delta_signing_payload")
    return value


class SignedAuthHeaders:
    """Immutable signed header material with one explicit raw transport boundary."""

    __api_key: _SecretSnapshot
    __timestamp: int
    __signature: _SecretSnapshot
    __user_agent: str
    __slots__ = ("__api_key", "__signature", "__timestamp", "__user_agent")

    def __init__(
        self,
        *,
        api_key: _SecretSnapshot,
        timestamp: int,
        signature: _SecretSnapshot,
        user_agent: str,
    ) -> None:
        object.__setattr__(self, "_SignedAuthHeaders__api_key", api_key.clone())
        object.__setattr__(self, "_SignedAuthHeaders__timestamp", timestamp)
        object.__setattr__(self, "_SignedAuthHeaders__signature", signature.clone())
        object.__setattr__(self, "_SignedAuthHeaders__user_agent", user_agent)

    @property
    def timestamp(self) -> int:
        """Return the non-secret epoch-second timestamp used for this signature."""
        return self.__timestamp

    def as_headers(self) -> dict[str, str]:
        """Return a fresh raw header mapping solely for immediate HTTP dispatch."""
        return {
            "api-key": self.__api_key.reveal(),
            "timestamp": str(self.__timestamp),
            "signature": self.__signature.reveal(),
            "User-Agent": self.__user_agent,
        }

    def __repr__(self) -> str:
        return f"SignedAuthHeaders(timestamp={self.__timestamp}, credentials=[REDACTED])"

    __str__ = __repr__

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise AttributeError("Signed authentication headers are immutable")

    def __delattr__(self, _name: str) -> NoReturn:
        raise AttributeError("Signed authentication headers are immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("Signed authentication headers cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("Signed authentication headers cannot be copied")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("Signed authentication headers cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("Signed authentication headers cannot be serialized")

    def __getstate__(self) -> NoReturn:
        raise TypeError("Signed authentication headers cannot be serialized")


class DeltaSigner:
    """Hold Delta credentials safely and sign exact finalized request bytes."""

    __api_key: _SecretSnapshot
    __api_secret: _SecretSnapshot
    __clock: EpochClock
    __user_agent: str
    __slots__ = ("__api_key", "__api_secret", "__clock", "__user_agent")

    def __init__(
        self,
        *,
        api_key: SecretStr,
        api_secret: SecretStr,
        user_agent: str,
        clock: EpochClock,
    ) -> None:
        if not callable(clock):
            raise _configuration_error("validate_delta_signing_clock")
        object.__setattr__(self, "_DeltaSigner__api_key", _validate_api_key(api_key))
        object.__setattr__(
            self,
            "_DeltaSigner__api_secret",
            _validate_secret(api_secret, operation="validate_delta_api_secret"),
        )
        object.__setattr__(self, "_DeltaSigner__user_agent", validate_user_agent(user_agent))
        object.__setattr__(self, "_DeltaSigner__clock", clock)

    def sign_at(
        self,
        method: str,
        timestamp: int,
        request_path: str,
        *,
        query_string: bytes = b"",
        payload: bytes = b"",
    ) -> SignedAuthHeaders:
        """Sign exact request components using an explicit epoch-second timestamp."""
        method_bytes = _validate_method(method)
        path_bytes = _validate_path(request_path)
        query_bytes = _validate_query(query_string)
        payload_bytes = _validate_payload(payload)
        checked_timestamp = _validate_timestamp(timestamp)
        return self._sign_validated(
            method_bytes,
            checked_timestamp,
            path_bytes,
            query_bytes,
            payload_bytes,
        )

    def sign_now(
        self,
        method: str,
        request_path: str,
        *,
        query_string: bytes = b"",
        payload: bytes = b"",
    ) -> SignedAuthHeaders:
        """Validate finalized wire bytes, then timestamp and sign immediately."""
        method_bytes = _validate_method(method)
        path_bytes = _validate_path(request_path)
        query_bytes = _validate_query(query_string)
        payload_bytes = _validate_payload(payload)
        try:
            timestamp = self.__clock()
        except Exception:
            raise _configuration_error("read_delta_signing_clock") from None
        checked_timestamp = _validate_timestamp(timestamp)
        return self._sign_validated(
            method_bytes,
            checked_timestamp,
            path_bytes,
            query_bytes,
            payload_bytes,
        )

    def _sign_validated(
        self,
        method: bytes,
        timestamp: int,
        request_path: bytes,
        query_string: bytes,
        payload: bytes,
    ) -> SignedAuthHeaders:
        timestamp_bytes = str(timestamp).encode("ascii")
        prehash = method + timestamp_bytes + request_path + query_string + payload
        secret = self.__api_secret.reveal().encode("utf-8")
        signature = hmac.new(secret, prehash, hashlib.sha256).hexdigest()
        return SignedAuthHeaders(
            api_key=self.__api_key,
            timestamp=timestamp,
            signature=_SecretSnapshot(signature),
            user_agent=self.__user_agent,
        )

    def __repr__(self) -> str:
        return "DeltaSigner(credentials=[REDACTED])"

    __str__ = __repr__

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise AttributeError("Delta signer is immutable")

    def __delattr__(self, _name: str) -> NoReturn:
        raise AttributeError("Delta signer is immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("Delta signer cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("Delta signer cannot be copied")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("Delta signer cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("Delta signer cannot be serialized")

    def __getstate__(self) -> NoReturn:
        raise TypeError("Delta signer cannot be serialized")


__all__ = ["DeltaSigner", "EpochClock", "SignedAuthHeaders"]
