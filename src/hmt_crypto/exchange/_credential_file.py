"""Descriptor-walked, bounded loading for one selected credential file."""

from __future__ import annotations

import json
import os
import stat
from typing import NoReturn, cast

from pydantic import SecretStr

_MAX_FILE_BYTES = 4096
_MAX_PATH_BYTES = 4096
_MAX_KEY_BYTES = 256
_MAX_SECRET_BYTES = 512


class _Rejected(Exception):
    pass


def _reject() -> NoReturn:
    raise _Rejected


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_mode == right.st_mode
        and left.st_uid == right.st_uid
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _secret(value: object, *, maximum: int) -> SecretStr:
    if type(value) is not str:
        _reject()
    try:
        encoded = value.encode("ascii")
    except Exception:
        _reject()
    if not 1 <= len(encoded) <= maximum or any(byte <= 0x20 or byte >= 0x7F for byte in encoded):
        _reject()
    return SecretStr(value)


def _components(value: object) -> tuple[str, ...]:
    if type(value) is not SecretStr:
        _reject()
    path = value.get_secret_value()
    try:
        encoded = os.fsencode(path)
    except Exception:
        _reject()
    if (
        type(path) is not str
        or not path
        or not 1 <= len(encoded) <= _MAX_PATH_BYTES
        or "\x00" in path
        or not path.startswith(os.sep)
        or path.startswith(os.sep * 2)
        or os.path.normpath(path) != path
    ):
        _reject()
    parts = tuple(path.split(os.sep)[1:])
    if not parts or any(not part or part in {".", ".."} for part in parts):
        _reject()
    return parts


def _flags() -> tuple[int, int]:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    close_on_exec = getattr(os, "O_CLOEXEC", None)
    directory = getattr(os, "O_DIRECTORY", None)
    nonblocking = getattr(os, "O_NONBLOCK", None)
    if any(type(value) is not int for value in (no_follow, close_on_exec, directory, nonblocking)):
        _reject()
    directory_flags = (
        os.O_RDONLY | cast(int, no_follow) | cast(int, close_on_exec) | cast(int, directory)
    )
    file_flags = (
        os.O_RDONLY | cast(int, no_follow) | cast(int, close_on_exec) | cast(int, nonblocking)
    )
    return directory_flags, file_flags


def _exact_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len({name for name, _item in pairs}) != len(pairs):
        _reject()
    return dict(pairs)


def _decode(payload: bytes) -> tuple[SecretStr, SecretStr]:
    decoded = json.loads(payload.decode("ascii"), object_pairs_hook=_exact_object)
    if type(decoded) is not dict or set(decoded) != {"api_key", "api_secret"}:
        _reject()
    return (
        _secret(decoded["api_key"], maximum=_MAX_KEY_BYTES),
        _secret(decoded["api_secret"], maximum=_MAX_SECRET_BYTES),
    )


def _load_selected_credential_file(value: object) -> tuple[SecretStr, SecretStr] | None:
    """Return exact credentials or a secret-free failure sentinel.

    Every ancestor remains open while the final file is checked and read. Names
    are verified again through their retained parent descriptors before success.
    No exception from a frame holding file bytes or credentials escapes.
    """
    descriptors: list[int] = []
    links: list[tuple[int, str, os.stat_result]] = []
    try:
        parts = _components(value)
        directory_flags, file_flags = _flags()
        root = os.open(os.sep, directory_flags)
        descriptors.append(root)
        if not stat.S_ISDIR(os.fstat(root).st_mode):
            _reject()
        parent = root
        for component in parts[:-1]:
            before = os.stat(component, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                _reject()
            opened = os.open(component, directory_flags, dir_fd=parent)
            descriptors.append(opened)
            opened_stat = os.fstat(opened)
            if not stat.S_ISDIR(opened_stat.st_mode) or not _same_file(before, opened_stat):
                _reject()
            links.append((parent, component, opened_stat))
            parent = opened

        filename = parts[-1]
        before_file = os.stat(filename, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before_file.st_mode):
            _reject()
        credential_fd = os.open(filename, file_flags, dir_fd=parent)
        descriptors.append(credential_fd)
        opened_file = os.fstat(credential_fd)
        mode = stat.S_IMODE(opened_file.st_mode)
        if (
            not stat.S_ISREG(opened_file.st_mode)
            or opened_file.st_uid != os.geteuid()
            or opened_file.st_nlink != 1
            or mode not in {0o400, 0o600}
            or not 1 <= opened_file.st_size <= _MAX_FILE_BYTES
            or not _same_file(before_file, opened_file)
        ):
            _reject()

        payload = os.read(credential_fd, _MAX_FILE_BYTES + 1)
        after_file = os.fstat(credential_fd)
        current_file = os.stat(filename, dir_fd=parent, follow_symlinks=False)
        if (
            len(payload) != opened_file.st_size
            or len(payload) > _MAX_FILE_BYTES
            or not _same_file(opened_file, after_file)
            or not _same_file(after_file, current_file)
        ):
            _reject()
        for parent_fd, component, opened_stat in links:
            current = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            if not _same_file(opened_stat, current):
                _reject()
        return _decode(payload)
    except Exception:
        return None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except Exception:
                pass


__all__: list[str] = []
