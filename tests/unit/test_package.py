"""Package availability and supported interpreter contract."""

import importlib
import sys

import pytest

pytestmark = pytest.mark.unit


def test_package_imports_on_supported_python() -> None:
    assert sys.version_info >= (3, 11)
    assert importlib.import_module("hmt_crypto") is not None
