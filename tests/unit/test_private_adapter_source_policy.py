"""Source policy for unsupported private DTO adapter validation modes."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_FORBIDDEN_ADAPTER_VALIDATION = {"validate_json", "validate_strings"}


def _unsupported_private_adapter_validation_routes(source: str, *, filename: str) -> list[str]:
    tree = ast.parse(source, filename=filename)
    violations: list[str] = []
    operator_modules = {"operator"}
    builtins_modules = {"builtins"}
    operator_functions: dict[str, str] = {}
    getattr_names = {"getattr"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "operator":
                    operator_modules.add(alias.asname or alias.name)
                elif alias.name == "builtins":
                    builtins_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "operator":
                for alias in node.names:
                    if alias.name in {"attrgetter", "methodcaller"}:
                        operator_functions[alias.asname or alias.name] = alias.name
            elif node.module == "builtins":
                for alias in node.names:
                    if alias.name == "getattr":
                        getattr_names.add(alias.asname or alias.name)

    def report(node: ast.AST, method: str) -> None:
        violations.append(f"{filename}:{node.lineno}:forbidden adapter {method}")

    def literal_method(argument: ast.expr) -> str | None:
        if (
            isinstance(argument, ast.Constant)
            and type(argument.value) is str
            and argument.value in _FORBIDDEN_ADAPTER_VALIDATION
        ):
            return argument.value
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ADAPTER_VALIDATION:
            report(node, node.attr)
        if not isinstance(node, ast.Call):
            continue
        reflection: str | None = None
        if isinstance(node.func, ast.Name):
            if node.func.id in getattr_names:
                reflection = "getattr"
            else:
                reflection = operator_functions.get(node.func.id)
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.attr == "getattr" and node.func.value.id in builtins_modules:
                reflection = "getattr"
            elif (
                node.func.attr in {"attrgetter", "methodcaller"}
                and node.func.value.id in operator_modules
            ):
                reflection = node.func.attr
        arguments = node.args[1:] if reflection == "getattr" else node.args
        if reflection == "methodcaller":
            arguments = arguments[:1]
        for argument in arguments:
            method = literal_method(argument)
            if method is not None:
                report(node, method)
    return violations


@pytest.mark.parametrize("method", ["validate_json", "validate_strings"])
@pytest.mark.parametrize(
    "source",
    [
        "TypeAdapter(Dto).{method}(raw)",
        "from pydantic import TypeAdapter as TA\nadapter = TA(Dto)\nadapter.{method}(raw)",
        "import pydantic as pd\npd.TypeAdapter(Dto).{method}(raw)",
        "adapter = make_adapter()\nadapter.{method}(raw)",
        "def parse(adapter, raw):\n    return adapter.{method}(raw)",
        "bound = adapter.{method}\nbound(raw)",
        "getattr(adapter, '{method}')(raw)",
        "import operator as op\nop.attrgetter('{method}')(adapter)(raw)",
        "from operator import methodcaller as call\ncall('{method}', raw)(adapter)",
        "TypeAdapter(ExactLeaf).{method}(raw)",
    ],
)
def test_adapter_validation_routes_reject_forbidden_spellings(method: str, source: str) -> None:
    violations = _unsupported_private_adapter_validation_routes(
        source.format(method=method), filename="snippet.py"
    )
    assert violations
    assert all(method in violation for violation in violations)


@pytest.mark.parametrize(
    "source",
    [
        "adapter.validate_python(value)",
        "Dto.model_validate_json(raw)",
        "Dto.model_validate_strings(values)",
        "getattr(Dto, 'model_validate_json')(raw)",
        "operator.attrgetter('model_validate_strings')(Dto)(values)",
        "getattr(adapter, method_name)(raw)",
    ],
)
def test_adapter_validation_routes_allow_owned_or_python_controls(source: str) -> None:
    assert _unsupported_private_adapter_validation_routes(source, filename="control.py") == []


def test_project_sources_forbid_private_adapter_buffer_validation() -> None:
    project_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for path in sorted((project_root / "src/hmt_crypto").rglob("*.py")):
        violations.extend(
            _unsupported_private_adapter_validation_routes(
                path.read_text(encoding="utf-8"), filename=str(path)
            )
        )
    assert violations == []
