"""Sealed one-use bounded-operation handles for the public stream actor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import CoroutineType
from typing import NoReturn, SupportsIndex
from weakref import WeakKeyDictionary

_OPERATION_TOKEN = object()


class _OperationKind(StrEnum):
    SEND = "send"
    RECEIPT = "receipt"
    REFRESH = "refresh"
    INVALIDATE = "invalidate"
    SLEEP = "sleep"


class _BoundedOperation:
    __slots__ = ("__weakref__",)

    def __new__(cls, _token: object) -> _BoundedOperation:
        if cls is not _BoundedOperation or _token is not _OPERATION_TOKEN:
            raise TypeError("bounded operations are module-owned")
        return super().__new__(cls)

    def __copy__(self) -> NoReturn:
        raise TypeError("bounded operations cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("bounded operations cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("bounded operations cannot be serialized")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("bounded operations cannot be serialized")


@dataclass(frozen=True, slots=True)
class _OperationRecord:
    session: object
    kind: _OperationKind
    payload: object


_OWNED_OPERATIONS: WeakKeyDictionary[_BoundedOperation, _OperationRecord] = WeakKeyDictionary()


def _issue_operation(
    session: object,
    kind: _OperationKind,
    payload: object,
) -> _BoundedOperation:
    operation = _BoundedOperation(_OPERATION_TOKEN)
    _OWNED_OPERATIONS[operation] = _OperationRecord(session, kind, payload)
    return operation


def _take_operation(value: object, session: object) -> _OperationRecord | None:
    if type(value) is not _BoundedOperation:
        return None
    record = _OWNED_OPERATIONS.pop(value, None)
    return record if record is not None and record.session is session else None


def _close_unowned_coroutine(value: object) -> None:
    if type(value) is CoroutineType:
        value.close()


__all__: list[str] = []
