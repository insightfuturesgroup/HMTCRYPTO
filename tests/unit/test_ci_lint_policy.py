"""Qualify built-in tuple rewrites and preserve non-neutral alias evaluation."""

import functools
import operator
import tomllib
from pathlib import Path
from typing import TypeAliasType

import pytest

pytestmark = pytest.mark.safety
GROUPS = (
    (bool, int, float, str),
    (bytes, bytearray, memoryview),
    (int, float),
    (list, tuple),
    (list, tuple, set, frozenset),
    (str, bytes, bytearray, memoryview),
    (tuple, list),
)
VALUES = (
    None,
    True,
    False,
    0,
    1,
    1.5,
    "",
    "value",
    b"value",
    bytearray(),
    memoryview(b"value"),
    [],
    (),
    set(),
    frozenset(),
    {},
    object(),
    ValueError(),
)


@pytest.mark.parametrize("members", GROUPS)
@pytest.mark.parametrize("value", VALUES)
def test_builtin_union_isinstance_matches_tuple(members, value):
    union = functools.reduce(operator.or_, members)
    assert isinstance(value, union) == isinstance(value, members)


def test_custom_metaclass_or_is_not_a_neutral_rewrite():
    class Meta(type):
        def __or__(cls, other):
            raise RuntimeError("custom union evaluation")

    class Custom(metaclass=Meta):
        pass

    assert isinstance(Custom(), (Custom, int))
    with pytest.raises(RuntimeError, match="custom union evaluation"):
        _ = Custom | int


def test_pep695_alias_changes_runtime_identity_and_evaluation():
    namespace = {}
    exec("type LazyAlias = MissingReference", namespace)
    alias = namespace["LazyAlias"]
    assert isinstance(alias, TypeAliasType)
    with pytest.raises(NameError):
        _ = alias.__value__
    with pytest.raises(NameError):
        exec("EagerAlias = MissingReference", {})


def test_only_reviewed_lint_rules_are_excluded():
    root = Path(__file__).parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    policy = config["tool"]["ruff"]["lint"]
    assert set(policy["ignore"]) == {"UP038", "UP040"}
    assert {"E", "F", "I", "UP", "B", "ASYNC"} <= set(policy["select"])
    assert config["tool"]["ruff"]["target-version"] == "py313"
