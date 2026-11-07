"""Import lifecycle, identity, schema, and source-policy tests for A2.1."""

from __future__ import annotations

import ast
import base64
import pickle
import sys
from pathlib import Path
from types import ModuleType

import pytest
from delta_private_model_test_support import (
    account_api,
    isolated_python,
    reachable_exceptions,
    valid_account,
)
from pydantic import TypeAdapter, ValidationError

pytestmark = pytest.mark.unit


def test_helper_first_lifecycle_preserves_facade_identity_schema_and_pickle() -> None:
    """Private leaves stay acyclic and the facade binds their original identities."""
    result = isolated_python(
        """
import importlib
import pickle
import sys

contracts = importlib.import_module('hmt_crypto.domain._account_contracts')
manifest = importlib.import_module('hmt_crypto.domain._private_generic_manifest')
registry = importlib.import_module('hmt_crypto.domain._private_generic_registry')
core = importlib.import_module('hmt_crypto.domain._private_generic_core')
assert 'hmt_crypto.domain.account' not in sys.modules
assert core.ValueField.__pydantic_complete__ is False
account = importlib.import_module('hmt_crypto.domain.account')
assert account.AccountRef is contracts.AccountRef
assert account.ValueField is core.ValueField
assert account._ReviewedGenericSpecialization is registry._ReviewedGenericSpecialization
assert account.OpaqueCursorValueField.__pydantic_complete__ is True
for value in (
    account.AccountRef,
    account.ObservationContext,
    account.PrivatePaginationProvenance,
    account.CollectionMode,
    account._ExactStringLiteral,
    account._ReviewedGenericSpecialization,
    account.ValueField,
    account.OpaqueCursorValueField,
    account.NullableOpaqueCursor,
):
    assert value.__module__ == 'hmt_crypto.domain.account'
    assert value.__name__ == value.__qualname__
    assert pickle.loads(pickle.dumps(value)) is value
instance = account.OpaqueCursorValueField(kind='VALUE', value='next')
restored = pickle.loads(pickle.dumps(instance))
assert type(restored) is account.OpaqueCursorValueField
assert restored.model_dump() == {'kind': 'VALUE', 'value': 'next'}
metadata = account.OpaqueCursorValueField.__pydantic_generic_metadata__
assert metadata['origin'] is account.ValueField
assert metadata['args'] == (account.OpaqueCursor,)
assert metadata['parameters'] == ()
schema = account.OpaqueCursorValueField.model_json_schema()
assert schema['additionalProperties'] is False
assert 'kind' in schema['properties'] and 'value' in schema['properties']
assert tuple(
    len(owner.rows) for owner in manifest._reviewed_vocabulary_for_verification()
) == (2, 4, 2, 43)
"""
    )
    assert result.returncode == 0, result.stderr


def test_rebuild_failure_aborts_import_without_reusable_partial_authority() -> None:
    """A failed force-build leaves no account module, cache, or accepting marker."""
    result = isolated_python(
        """
import importlib
import sys
from pydantic import TypeAdapter, ValidationError
from hmt_crypto.domain.private_primitives import OpaqueCursor

core = importlib.import_module('hmt_crypto.domain._private_generic_core')
original = core._rebuild_reviewed_model_candidate
def fail(_model):
    raise RuntimeError('injected account rebuild failure')
core._rebuild_reviewed_model_candidate = fail
try:
    importlib.import_module('hmt_crypto.domain.account')
except RuntimeError as error:
    assert str(error) == 'injected account rebuild failure'
else:
    raise AssertionError('account import unexpectedly succeeded')
assert 'hmt_crypto.domain.account' not in sys.modules
marker = core.ValueField[OpaqueCursor]
assert marker is core._RejectedPrivateGenericSpecialization
core._rebuild_reviewed_model_candidate = original
account = importlib.import_module('hmt_crypto.domain.account')
assert core.ValueField[OpaqueCursor] is account.OpaqueCursorValueField
assert marker is not account.OpaqueCursorValueField
try:
    TypeAdapter(marker).validate_python({'kind': 'VALUE', 'value': 'next'})
except ValidationError as error:
    assert error.errors(include_url=False)[0]['type'] == 'private_generic_specialization'
else:
    raise AssertionError('old marker unexpectedly accepted')
"""
    )
    assert result.returncode == 0, result.stderr


def test_account_import_performs_no_application_io_or_later_owner_import() -> None:
    """Fresh account import builds only two inert rows without external side effects."""
    result = isolated_python(
        """
import asyncio
import builtins
import importlib
import io
import logging
import os
import random
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import sqlalchemy
from sqlalchemy.ext import asyncio as sqlalchemy_asyncio

for supplier in (
    'hmt_crypto.config',
    'hmt_crypto.domain.private_primitives',
    'hmt_crypto.exchange.delta.private_routes',
):
    importlib.import_module(supplier)
helpers = (
    'hmt_crypto.domain._account_contracts',
    'hmt_crypto.domain._private_generic_provenance',
    'hmt_crypto.domain._private_generic_manifest',
    'hmt_crypto.domain._private_generic_source',
    'hmt_crypto.domain._private_generic_registry',
    'hmt_crypto.domain._private_generic_core',
)
assert not any(name in sys.modules for name in helpers)
assert 'hmt_crypto.domain.account' not in sys.modules
forbidden_modules = (
    'hmt_crypto.exchange.delta.client',
    'hmt_crypto.exchange.delta.signer',
    'hmt_crypto.exchange.delta.rate_limiter',
    'hmt_crypto.exchange.delta.weighted_limiter',
    'hmt_crypto.persistence.db',
    'supabase',
)
assert not any(name in sys.modules for name in forbidden_modules)

events = []
def denied(label):
    def reject(*args, **kwargs):
        del args, kwargs
        events.append(label)
        raise AssertionError(label)
    return reject

real_environment = os.environ
class GuardedEnvironment:
    def get(self, key, default=None):
        if key == 'PYDANTIC_DISABLE_PLUGINS':
            return real_environment.get(key, default)
        return denied('os.environ.get')(key, default)
    def __getitem__(self, key):
        if key == 'PYDANTIC_DISABLE_PLUGINS':
            return real_environment[key]
        return denied('os.environ.index')(key)
os.environ = GuardedEnvironment()

builtins.open = denied('builtins.open')
io.open = denied('io.open')
os.open = denied('os.open')
Path.open = denied('Path.open')
socket.socket = denied('socket.socket')
socket.create_connection = denied('socket.create_connection')
socket.getaddrinfo = denied('socket.getaddrinfo')
socket.gethostbyname = denied('socket.gethostbyname')
threading.Thread.start = denied('thread.start')
asyncio.create_task = denied('asyncio.create_task')
asyncio.BaseEventLoop.create_task = denied('loop.create_task')
logging.basicConfig = denied('logging.basicConfig')
logging.Logger.addHandler = denied('logging.addHandler')
logging.FileHandler = denied('logging.FileHandler')
time.time = denied('time.time')
time.monotonic = denied('time.monotonic')
time.perf_counter = denied('time.perf_counter')
time.sleep = denied('time.sleep')
random.random = denied('random.random')
secrets.token_bytes = denied('secrets.token_bytes')
secrets.token_hex = denied('secrets.token_hex')
os.urandom = denied('os.urandom')
httpx.Client = denied('httpx.Client')
httpx.AsyncClient = denied('httpx.AsyncClient')
httpx.AsyncHTTPTransport = denied('httpx.AsyncHTTPTransport')
sqlalchemy.create_engine = denied('sqlalchemy.create_engine')
sqlalchemy_asyncio.create_async_engine = denied('sqlalchemy.create_async_engine')

config = sys.modules['hmt_crypto.config']
config.Settings.__init__ = denied('credential.Settings')
real_import = builtins.__import__
real_import_module = importlib.import_module
forbidden_imports = (
    'httpx',
    'sqlalchemy',
    'supabase',
    'hmt_crypto.persistence',
    'hmt_crypto.exchange.delta.client',
    'hmt_crypto.exchange.delta.signer',
    'hmt_crypto.exchange.delta.rate_limiter',
    'hmt_crypto.exchange.delta.weighted_limiter',
)
def is_forbidden(name):
    return any(name == root or name.startswith(root + '.') for root in forbidden_imports)
def guarded_import(name, *args, **kwargs):
    if is_forbidden(name):
        return denied('import:' + name)(name)
    return real_import(name, *args, **kwargs)
def guarded_import_module(name, *args, **kwargs):
    if is_forbidden(name):
        return denied('import_module:' + name)(name)
    return real_import_module(name, *args, **kwargs)
builtins.__import__ = guarded_import
importlib.import_module = guarded_import_module

account = importlib.import_module('hmt_crypto.domain.account')
assert events == []
owners = (
    'hmt_crypto.domain.account',
    'hmt_crypto.domain.private_wallet',
    'hmt_crypto.domain.private_positions',
    'hmt_crypto.domain.private_orders',
)
assert tuple(
    2 if name == owners[0] else int(name in sys.modules) for name in owners
) == (2, 0, 0, 0)
assert len(account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS) == 2
assert all(name in sys.modules for name in helpers)
assert not any(name in sys.modules for name in forbidden_modules)

def expect_denied(label, operation):
    before = tuple(events)
    try:
        operation()
    except AssertionError as error:
        assert str(error) == label
    else:
        raise AssertionError(label + ' mutation control did not fire')
    assert tuple(events) == (*before, label)

controls = (
    ('os.environ.get', lambda: os.environ.get('DELTA_API_KEY')),
    ('os.environ.index', lambda: os.environ['DELTA_API_SECRET']),
    ('builtins.open', lambda: builtins.open('blocked')),
    ('io.open', lambda: io.open('blocked')),
    ('os.open', lambda: os.open('blocked', 0)),
    ('Path.open', lambda: Path('blocked').open()),
    ('socket.socket', lambda: socket.socket()),
    ('socket.getaddrinfo', lambda: socket.getaddrinfo('blocked', 443)),
    ('thread.start', lambda: threading.Thread.start(object())),
    ('asyncio.create_task', lambda: asyncio.create_task(object())),
    ('logging.addHandler', lambda: logging.Logger.addHandler(object(), object())),
    ('time.time', lambda: time.time()),
    ('time.monotonic', lambda: time.monotonic()),
    ('random.random', lambda: random.random()),
    ('os.urandom', lambda: os.urandom(8)),
    ('httpx.AsyncClient', lambda: httpx.AsyncClient()),
    ('sqlalchemy.create_engine', lambda: sqlalchemy.create_engine('sqlite://')),
    ('credential.Settings', lambda: config.Settings()),
    ('import:httpx', lambda: builtins.__import__('httpx')),
    ('import:sqlalchemy', lambda: builtins.__import__('sqlalchemy')),
    ('import:supabase', lambda: builtins.__import__('supabase')),
    (
        'import_module:hmt_crypto.persistence.db',
        lambda: importlib.import_module('hmt_crypto.persistence.db'),
    ),
    (
        'import_module:hmt_crypto.exchange.delta.signer',
        lambda: importlib.import_module('hmt_crypto.exchange.delta.signer'),
    ),
    (
        'import_module:hmt_crypto.exchange.delta.weighted_limiter',
        lambda: importlib.import_module('hmt_crypto.exchange.delta.weighted_limiter'),
    ),
)
for label, operation in controls:
    expect_denied(label, operation)
assert tuple(events) == tuple(label for label, _operation in controls)
assert not any(name in sys.modules for name in forbidden_modules)
"""
    )
    assert result.returncode == 0, result.stderr


def test_account_split_files_remain_focused_and_within_reviewed_ceilings() -> None:
    project_root = Path(__file__).resolve().parents[2]
    limits = {
        "account.py": 180,
        "_account_contracts.py": 300,
        "_private_generic_provenance.py": 420,
        "_private_generic_manifest.py": 260,
        "_private_generic_source.py": 420,
        "_private_generic_registry.py": 420,
        "_private_generic_core.py": 540,
    }
    domain = project_root / "src/hmt_crypto/domain"
    for name, limit in limits.items():
        assert len((domain / name).read_text(encoding="utf-8").splitlines()) <= limit


def test_future_order_family_declaration_requires_frozen_construction_sources() -> None:
    """A facade-only lookalike cannot open the A2.3 declaration seam."""
    from typing import Generic, TypeVar

    from hmt_crypto.domain.private_primitives import PrivateBoundaryModel

    api = account_api()
    account = api.module
    module_name = "hmt_crypto.domain.private_orders"
    sentinel = object()
    previous = sys.modules.get(module_name, sentinel)
    fake = ModuleType(module_name)
    parameter = TypeVar("parameter")

    class LookupFound(account._ClosedPrivateGenericModel, Generic[parameter]):
        __module__ = module_name

        @classmethod
        def __class_getitem__(cls, parameters: object) -> object:
            return account._closed_model_subscription(cls, parameters)

    class LatestPosition(PrivateBoundaryModel):
        __module__ = module_name
        product_id: int

    for value in (LookupFound, LatestPosition):
        value.__qualname__ = value.__name__
        setattr(fake, value.__name__, value)
    try:
        sys.modules[module_name] = fake
        with pytest.raises(TypeError, match="invalid reviewed generic model declaration"):
            account._declare_reviewed_model_candidate(
                LookupFound,
                LatestPosition,
                canonical_name="LatestPositionLookupFound",
                owner_module=module_name,
            )
        assert account._REVIEWED_GENERIC_OWNER_TABLES[-1][2] == 43
    finally:
        if previous is sentinel:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous


def test_account_class_and_instance_pickle_cross_a_fresh_process() -> None:
    api = account_api()
    value = api.OpaqueCursorValueField(kind="VALUE", value="cross-process")
    encoded = base64.b64encode(pickle.dumps((api.OpaqueCursorValueField, value))).decode()
    result = isolated_python(
        f"""
import base64
import pickle
import hmt_crypto.domain.account as account
model, value = pickle.loads(base64.b64decode({encoded!r}))
assert model is account.OpaqueCursorValueField
assert type(value) is model
assert value.model_dump() == {{'kind': 'VALUE', 'value': 'cross-process'}}
"""
    )
    assert result.returncode == 0, result.stderr


def _assert_input_free_private_error(error: ValidationError, canary: str) -> None:
    assert all(detail.get("input") is None for detail in error.errors(include_url=False))
    for linked in reachable_exceptions(error):
        rendered = f"{linked!s} {linked!r} {linked.args!r}"
        if isinstance(linked, ValidationError):
            rendered += f" {linked.errors(include_url=False)!r}"
        assert canary not in rendered


def test_private_dto_adapter_supports_only_exact_python_validation() -> None:
    api = account_api()
    account_value = valid_account(api)
    account_result = TypeAdapter(api.AccountRef).validate_python(account_value.model_dump())
    assert type(account_result) is api.AccountRef
    assert account_result.model_dump() == account_value.model_dump()

    value_adapter = TypeAdapter(api.OpaqueCursorValueField)
    value_result = value_adapter.validate_python({"kind": "VALUE", "value": "next"})
    assert type(value_result) is api.OpaqueCursorValueField
    assert value_result.model_dump() == {"kind": "VALUE", "value": "next"}

    alias_adapter = TypeAdapter(api.NullableOpaqueCursor)
    null_result = alias_adapter.validate_python({"kind": "NULL"})
    alias_value_result = alias_adapter.validate_python({"kind": "VALUE", "value": "next"})
    assert type(null_result) is api.NullField
    assert type(alias_value_result) is api.OpaqueCursorValueField

    canary = "fixture-secret-adapter-python"
    for adapter in (TypeAdapter(api.AccountRef), value_adapter, alias_adapter):
        with pytest.raises(ValidationError) as captured:
            adapter.validate_python(canary)
        _assert_input_free_private_error(captured.value, canary)


@pytest.mark.parametrize(
    "raw",
    [
        '{"value":"fixture-secret-owned-json',
        b'{"value":"fixture-secret-owned-json',
        bytearray(b'{"value":"fixture-secret-owned-json'),
    ],
)
def test_private_dto_owned_json_entry_discards_raw_buffers(raw: object) -> None:
    api = account_api()
    for model in (api.AccountRef, api.OpaqueCursorValueField):
        with pytest.raises(ValidationError) as captured:
            model.model_validate_json(raw)
        assert captured.value.errors(include_url=False) == [
            {
                "type": "private_model_value",
                "loc": (),
                "msg": "invalid private model value",
                "input": None,
            }
        ]
        assert captured.value.__context__ is None
        assert captured.value.__cause__ is None
        _assert_input_free_private_error(captured.value, "fixture-secret-owned-json")
        traceback = captured.value.__traceback__
        while traceback is not None:
            if traceback.tb_frame.f_code.co_filename.endswith("/domain/_private_model.py"):
                assert "fixture-secret-owned-json" not in repr(traceback.tb_frame.f_locals)
            traceback = traceback.tb_next


def test_private_dto_json_output_is_not_coercive_input() -> None:
    api = account_api()
    values = (
        valid_account(api),
        api.OpaqueCursorValueField(kind="VALUE", value="fixture-secret-output-only"),
    )
    for value in values:
        raw = value.model_dump_json()
        with pytest.raises(ValidationError) as captured:
            type(value).model_validate_json(raw)
        assert captured.value.errors(include_url=False) == [
            {
                "type": "private_model_value",
                "loc": (),
                "msg": "invalid private model value",
                "input": None,
            }
        ]
        _assert_input_free_private_error(captured.value, raw)


def test_account_source_and_runtime_policy_forbid_serializer_or_config_escape_hatches() -> None:
    """Keep A1d policy intact across every extracted account helper."""
    api = account_api()
    project_root = Path(__file__).resolve().parents[2]
    domain = project_root / "src/hmt_crypto/domain"
    paths = tuple(
        domain / name
        for name in (
            "account.py",
            "_account_contracts.py",
            "_private_generic_provenance.py",
            "_private_generic_manifest.py",
            "_private_generic_source.py",
            "_private_generic_registry.py",
            "_private_generic_core.py",
        )
    )
    forbidden = (
        "json_encoders",
        "field_serializer",
        "model_serializer",
        "PlainSerializer",
        "WrapSerializer",
        "SerializeAsAny",
    )
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert not any(token in source for token in forbidden)
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and path.name.startswith("_"):
                assert node.module != "hmt_crypto.domain.account"
                assert node.module not in {
                    "hmt_crypto.domain.private_wallet",
                    "hmt_crypto.domain.private_positions",
                    "hmt_crypto.domain.private_orders",
                }

    critical = {
        "extra": "forbid",
        "frozen": True,
        "hide_input_in_errors": True,
        "revalidate_instances": "always",
        "strict": True,
        "validate_default": True,
    }
    models = (
        api.MissingField,
        api.NullField,
        api.AccountRef,
        api.ObservationContext,
        api.PrivatePaginationProvenance,
        api.OpaqueCursorValueField,
    )
    for model in models:
        for key, expected in critical.items():
            assert model.model_config[key] == expected
        assert not any(callable(value) for value in model.model_config.values())
        assert not model.__pydantic_decorators__.field_serializers
        assert not model.__pydantic_decorators__.model_serializers
    assert api.OpaqueCursorValueField.model_config["defer_build"] is True
    assert not hasattr(api.module, "ExactPageCorrelationIds")

    class LeftMixin:
        pass

    class RejectedLeftMixin(LeftMixin, api.OpaqueCursorValueField):
        pass

    with pytest.raises(ValidationError, match="private generic specialization unavailable"):
        RejectedLeftMixin(kind="VALUE", value="next")
