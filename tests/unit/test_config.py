"""Configuration boundaries must fail closed and keep credentials private."""

import ast
import hashlib
import importlib
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from hmt_crypto.config import DeltaRegion, Settings

CONFIRMATION = "I_UNDERSTAND_THIS_CAN_PLACE_REAL_ORDERS"
SLOTS = (
    "DELTA_INDIA_TESTNET",
    "DELTA_INDIA_PROD",
    "DELTA_GLOBAL_TESTNET",
    "DELTA_GLOBAL_PROD",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in os.environ:
        if name.upper().startswith("DELTA_") or name.upper() == "EXECUTION_MODE":
            monkeypatch.delenv(name)


def settings(**kwargs: Any) -> Settings:
    return Settings(_env_file=None, **kwargs)


def arm(monkeypatch: pytest.MonkeyPatch, slot: str = "DELTA_INDIA_TESTNET") -> None:
    monkeypatch.setenv("EXECUTION_MODE", "LIVE")
    monkeypatch.setenv("DELTA_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("DELTA_LIVE_CONFIRMATION", CONFIRMATION)
    monkeypatch.setenv(f"{slot}_API_KEY", "example-api-key")
    monkeypatch.setenv(f"{slot}_API_SECRET", "example-api-secret")


def test_config_exact_enum_sources_are_captured_before_settings_definition() -> None:
    """Each ordinary config enum is captured immediately at its source."""
    config_module = importlib.import_module("hmt_crypto.config")
    enum_module = importlib.import_module("hmt_crypto._private_enums")
    source = Path(config_module.__file__).read_text(encoding="utf-8")
    statements = ast.parse(source).body
    expected = {
        "ExecutionMode": (("BACKTEST", "BACKTEST"), ("PAPER", "PAPER"), ("LIVE", "LIVE")),
        "DeltaRegion": (("INDIA", "india"), ("GLOBAL", "global")),
        "DeltaEnvironment": (("TESTNET", "testnet"), ("PROD", "prod")),
    }

    for class_name, manifest in expected.items():
        class_index = next(
            index
            for index, statement in enumerate(statements)
            if isinstance(statement, ast.ClassDef) and statement.name == class_name
        )
        capture = statements[class_index + 1]
        assert isinstance(capture, ast.Expr)
        assert isinstance(capture.value, ast.Call)
        assert isinstance(capture.value.func, ast.Name)
        assert capture.value.func.id == "_capture_foreign_exact_enum"
        assert ast.literal_eval(capture.value.args[1]) == manifest

        enum_type = getattr(config_module, class_name)
        field = enum_module.ExactEnumField(enum_type)
        assert field.enum_type is enum_type


def test_defaults_are_paper_india_testnet_without_write_capability() -> None:
    config = settings()
    assert config.execution_mode.value == "PAPER"
    assert config.delta_region.value == "india"
    assert config.delta_env.value == "testnet"
    assert config.live_config_ready is False
    assert config.order_write_capable is False
    assert config.selected_api_key is None
    assert config.selected_api_secret is None


@pytest.mark.parametrize(
    ("region", "environment", "url", "slot"),
    [
        ("india", "prod", "https://api.india.delta.exchange", "DELTA_INDIA_PROD"),
        ("india", "testnet", "https://cdn-ind.testnet.deltaex.org", "DELTA_INDIA_TESTNET"),
        ("global", "prod", "https://api.delta.exchange", "DELTA_GLOBAL_PROD"),
        ("global", "testnet", "https://testnet-api.delta.exchange", "DELTA_GLOBAL_TESTNET"),
    ],
)
def test_endpoint_and_credentials_follow_selected_slot(
    monkeypatch: pytest.MonkeyPatch, region: str, environment: str, url: str, slot: str
) -> None:
    monkeypatch.setenv("DELTA_REGION", region)
    monkeypatch.setenv("DELTA_ENV", environment)
    for index, candidate in enumerate(SLOTS):
        monkeypatch.setenv(f"{candidate}_API_KEY", f"key-{index}")
        monkeypatch.setenv(f"{candidate}_API_SECRET", f"secret-{index}")
    config = settings()
    assert config.rest_base_url == url
    assert config.selected_credential_slot == slot
    assert config.selected_api_key.get_secret_value() == f"key-{SLOTS.index(slot)}"
    assert config.selected_api_secret.get_secret_value() == f"secret-{SLOTS.index(slot)}"
    assert config.order_write_capable is False


@pytest.mark.parametrize(
    ("name", "value"),
    [("EXECUTION_MODE", "live"), ("DELTA_REGION", "us"), ("DELTA_ENV", "production")],
)
def test_invalid_enum_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        settings()


@pytest.mark.parametrize("name", ["DELTA_API_KEY", "DELTA_API_SECRET", "DELTA_TYPO"])
def test_generic_or_unknown_delta_environment_keys_are_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "sensitive-input")
    with pytest.raises(ValidationError) as error:
        settings()
    assert "sensitive-input" not in str(error.value)


def test_unknown_constructor_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        settings(unknown_option=True)


@pytest.mark.parametrize("suffix", ["API_KEY", "API_SECRET"])
@pytest.mark.parametrize("value", ["", "   ", "one-half"])
def test_selected_partial_or_empty_credentials_are_invalid(
    monkeypatch: pytest.MonkeyPatch, suffix: str, value: str
) -> None:
    monkeypatch.setenv(f"DELTA_INDIA_TESTNET_{suffix}", value)
    with pytest.raises(ValidationError):
        settings()


@pytest.mark.parametrize("missing", ["KEY", "SECRET"])
def test_live_cannot_borrow_credentials_from_another_slot(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    arm(monkeypatch, "DELTA_GLOBAL_PROD")
    monkeypatch.setenv(f"DELTA_INDIA_TESTNET_API_{missing}", "selected-half")
    with pytest.raises(ValidationError):
        settings()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("DELTA_LIVE_TRADING_ENABLED", None),
        ("DELTA_LIVE_TRADING_ENABLED", "false"),
        ("DELTA_LIVE_CONFIRMATION", None),
        ("DELTA_LIVE_CONFIRMATION", "yes"),
        ("DELTA_LIVE_CONFIRMATION", CONFIRMATION.lower()),
        ("DELTA_LIVE_CONFIRMATION", CONFIRMATION + " "),
        ("DELTA_INDIA_TESTNET_API_KEY", None),
        ("DELTA_INDIA_TESTNET_API_SECRET", None),
    ],
)
def test_each_live_gate_is_required(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str | None
) -> None:
    arm(monkeypatch)
    if value is None:
        monkeypatch.delenv(field)
    else:
        monkeypatch.setenv(field, value)
    with pytest.raises(ValidationError):
        settings()


@pytest.mark.parametrize("region", ["india", "global"])
@pytest.mark.parametrize("environment", ["testnet", "prod"])
def test_armed_live_configuration_never_enables_phase_one_order_writes(
    monkeypatch: pytest.MonkeyPatch, region: str, environment: str
) -> None:
    arm(monkeypatch, f"DELTA_{region.upper()}_{environment.upper()}")
    monkeypatch.setenv("DELTA_REGION", region)
    monkeypatch.setenv("DELTA_ENV", environment)
    config = settings()
    assert config.live_config_ready is True
    assert config.order_write_capable is False


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_non_live_modes_remain_unarmed_even_with_all_gates(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    arm(monkeypatch)
    monkeypatch.setenv("EXECUTION_MODE", mode)
    config = settings()
    assert config.live_config_ready is False
    assert config.order_write_capable is False


def test_secrets_are_redacted_and_diagnostics_only_expose_slot_and_key_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arm(monkeypatch)
    config = settings()
    assert isinstance(config.selected_api_key, SecretStr)
    assert isinstance(config.selected_api_secret, SecretStr)
    rendered = repr(config) + str(config.model_dump()) + config.model_dump_json()
    for sensitive in ["example-api-key", "example-api-secret", CONFIRMATION]:
        assert sensitive not in rendered
    assert config.safe_diagnostics() == {
        "credential_slot": "DELTA_INDIA_TESTNET",
        "api_key_fingerprint": hashlib.sha256(b"example-api-key").hexdigest()[:12],
    }


def test_diagnostics_without_credentials_have_no_fingerprint() -> None:
    assert settings().safe_diagnostics() == {
        "credential_slot": "DELTA_INDIA_TESTNET",
        "api_key_fingerprint": None,
    }


def test_live_requires_selected_credentials_even_when_other_slot_is_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arm(monkeypatch, "DELTA_GLOBAL_PROD")
    with pytest.raises(ValidationError):
        settings()


@pytest.mark.parametrize("name", ["DELTA_API_KEY", "DELTA_API_SECRET", "DELTA_UNKNOWN"])
def test_dotenv_rejects_generic_and_unknown_fields(tmp_path: Path, name: str) -> None:
    path = tmp_path / ".env"
    path.write_text(f"{name}=private-value\n")
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=path)
    assert "private-value" not in str(error.value)


def test_dotenv_loads_selected_credentials(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "DELTA_REGION=global\nDELTA_ENV=prod\n"
        "DELTA_GLOBAL_PROD_API_KEY=dotenv-key\n"
        "DELTA_GLOBAL_PROD_API_SECRET=dotenv-secret\n"
    )
    config = Settings(_env_file=path)
    assert config.rest_base_url == "https://api.delta.exchange"
    assert config.selected_api_key.get_secret_value() == "dotenv-key"
    assert config.selected_api_secret.get_secret_value() == "dotenv-secret"


def test_dotenv_ignores_separately_typed_api_and_alert_settings(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "DELTA_REGION=india\n"
        "HMT_API_ALLOWED_ORIGIN=https://console.example\n"
        "HMT_ALERT_TELEGRAM_CHAT_ID=12345\n"
    )
    assert Settings(_env_file=path).delta_region is DeltaRegion.INDIA


@pytest.mark.parametrize("name", ["delta_region", "Delta_Region", "DELTA_region"])
def test_environment_aliases_are_exact_case_sensitive(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "global")
    with pytest.raises(ValidationError):
        settings()


def test_dotenv_aliases_are_exact_case_sensitive(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("delta_region=global\n")
    with pytest.raises(ValidationError):
        Settings(_env_file=path)


@pytest.mark.parametrize("same_value", [True, False])
def test_constructor_rejects_alias_and_field_name_duplicates(same_value: bool) -> None:
    with pytest.raises(ValidationError):
        settings(
            DELTA_REGION="india",
            delta_region="india" if same_value else "global",
        )


def test_constructor_duplicate_rejection_observes_no_default_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_bytes(b"\xff")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError):
        Settings(DELTA_REGION="india", delta_region="global")


@pytest.mark.parametrize("credential_slot", ["selected_inline", "selected_file", "other"])
def test_development_override_rejects_every_real_credential_source(
    tmp_path: Path, credential_slot: str
) -> None:
    values: dict[str, object] = {
        "DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE": True,
        "DELTA_DEVELOPMENT_REST_BASE_URL": "http://127.0.0.1:8001",
    }
    if credential_slot == "selected_inline":
        values.update(
            DELTA_INDIA_TESTNET_API_KEY="dev-key-canary",
            DELTA_INDIA_TESTNET_API_SECRET="dev-secret-canary",
        )
    elif credential_slot == "selected_file":
        path = tmp_path / "selected.json"
        path.write_text("{}")
        values["DELTA_INDIA_TESTNET_CREDENTIAL_FILE"] = str(path)
    else:
        values.update(
            DELTA_INDIA_PROD_API_KEY="other-key-canary",
            DELTA_INDIA_PROD_API_SECRET="other-secret-canary",
        )
    with pytest.raises(ValidationError) as captured:
        settings(**values)
    rendered = f"{captured.value!s} {captured.value!r}"
    assert "dev-secret-canary" not in rendered
    assert "other-secret-canary" not in rendered


def test_settings_cannot_be_mutated_to_bypass_live_gates() -> None:
    config = settings()
    with pytest.raises(ValidationError):
        config.execution_mode = "LIVE"
    assert config.live_config_ready is False
    assert config.order_write_capable is False


@pytest.mark.parametrize("source", ["constructor", "environment", "dotenv"])
@pytest.mark.parametrize("failure", ["live_gate", "field", "unknown"])
def test_structured_validation_errors_never_expose_sensitive_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str, failure: str
) -> None:
    values = {
        "EXECUTION_MODE": "LIVE",
        "DELTA_LIVE_TRADING_ENABLED": "false",
        "DELTA_LIVE_CONFIRMATION": CONFIRMATION,
        "DELTA_INDIA_TESTNET_API_KEY": "error-test-api-key",
        "DELTA_INDIA_TESTNET_API_SECRET": "error-test-api-secret",
    }
    if failure == "field":
        values["DELTA_REGION"] = "error-test-api-key"
    elif failure == "unknown":
        values["DELTA_API_KEY"] = "error-test-api-key"
        values["DELTA_API_SECRET"] = "error-test-api-secret"
        values["DELTA_UNKNOWN"] = CONFIRMATION
    with pytest.raises(ValidationError) as error:
        if source == "constructor":
            settings(**values)
        elif source == "environment":
            for name, value in values.items():
                monkeypatch.setenv(name, value)
            settings()
        else:
            path = tmp_path / ".env"
            path.write_text("".join(f"{name}={value}\n" for name, value in values.items()))
            Settings(_env_file=path)
    for rendered in [str(error.value), str(error.value.errors()), error.value.json()]:
        for sensitive in ["error-test-api-key", "error-test-api-secret", CONFIRMATION]:
            assert sensitive not in rendered
