"""Exact fail-closed selection of one Delta India credential source."""

from __future__ import annotations

from pydantic import SecretStr

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode, Settings
from hmt_crypto.exchange import _credential_validation as _cv
from hmt_crypto.exchange import credentials
from hmt_crypto.exchange._credential_file import _load_selected_credential_file

_MAX_API_SECRET_BYTES = 512


def _validated_secret(value: object, *, maximum: int) -> SecretStr | None:
    try:
        if type(value) is not SecretStr:
            return None
        raw = value.get_secret_value()
        if type(raw) is not str:
            return None
        encoded = raw.encode("ascii")
    except Exception:
        return None
    if not 1 <= len(encoded) <= maximum or any(byte <= 0x20 or byte >= 0x7F for byte in encoded):
        return None
    return SecretStr(raw)


def _selected_india_source(
    value: object,
) -> (
    tuple[
        bool,
        credentials.IndiaCredentialSlot,
        DeltaEnvironment,
        ExecutionMode,
        SecretStr | None,
        SecretStr,
        SecretStr,
    ]
    | None
):
    try:
        if type(value) is not Settings:
            return None
        restored = Settings.model_validate(value.model_dump())
        if (
            type(restored.delta_region) is not DeltaRegion
            or restored.delta_region is not DeltaRegion.INDIA
            or type(restored.delta_env) is not DeltaEnvironment
            or type(restored.execution_mode) is not ExecutionMode
        ):
            return None
        environment = restored.delta_env
        mode = _cv._exact_mode(restored.execution_mode, environment)
        slot = (
            credentials.IndiaCredentialSlot.INDIA_TESTNET
            if environment is DeltaEnvironment.TESTNET
            else credentials.IndiaCredentialSlot.INDIA_PROD
        )
        key = restored.selected_api_key
        secret = restored.selected_api_secret
        credential_file = restored.selected_credential_file
        if credential_file is not None:
            if key is not None or secret is not None:
                return None
            loaded = _load_selected_credential_file(credential_file)
            if loaded is None:
                return None
            api_key, api_secret = loaded
            return (
                True,
                slot,
                environment,
                mode,
                SecretStr(credential_file.get_secret_value()),
                api_key,
                api_secret,
            )
        if key is None or secret is None:
            return None
        checked_key = _validated_secret(key, maximum=256)
        checked_secret = _validated_secret(secret, maximum=_MAX_API_SECRET_BYTES)
        if checked_key is None or checked_secret is None:
            return None
        return False, slot, environment, mode, None, checked_key, checked_secret
    except Exception:
        return None


__all__: list[str] = []
