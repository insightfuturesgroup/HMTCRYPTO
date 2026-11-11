"""AWS secret lifecycle recovery tests without external AWS calls."""

from __future__ import annotations

import importlib
from uuid import UUID

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from hmt_crypto.api.secret_store import AwsDeltaSecretStore

pytestmark = pytest.mark.unit


class _ScheduledDeletionClient:
    def __init__(self) -> None:
        self.restored: list[str] = []
        self.replaced: list[str] = []

    def create_secret(self, **_: object) -> object:
        raise ClientError(
            {
                "Error": {
                    "Code": "InvalidRequestException",
                    "Message": (
                        "You can't create this secret because a secret with this name is "
                        "already scheduled for deletion."
                    ),
                }
            },
            "CreateSecret",
        )

    def restore_secret(self, *, SecretId: str) -> dict[str, str]:  # noqa: N803
        self.restored.append(SecretId)
        return {"ARN": "arn:aws:secretsmanager:region:account:secret:restored"}

    def put_secret_value(self, *, SecretId: str, SecretString: str) -> dict[str, str]:  # noqa: N803
        self.replaced.append(SecretId)
        assert "api-key-value" in SecretString
        assert "api-secret-value" in SecretString
        return {
            "ARN": "arn:aws:secretsmanager:region:account:secret:restored",
            "VersionId": "replacement-version",
        }


@pytest.mark.asyncio
async def test_scheduled_deletion_is_restored_and_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ScheduledDeletionClient()

    class _Boto3:
        @staticmethod
        def client(*_: object, **__: object) -> _ScheduledDeletionClient:
            return client

    original = importlib.import_module
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: _Boto3 if name == "boto3" else original(name),
    )
    store = AwsDeltaSecretStore(region="ap-southeast-2", prefix="hmt-crypto/delta")

    receipt = await store.put(
        account_id=UUID("00000000-0000-4000-8000-000000000123"),
        version=1,
        api_key="api-key-value",
        api_secret="api-secret-value",
    )

    expected = "hmt-crypto/delta/00000000-0000-4000-8000-000000000123/v1"
    assert client.restored == [expected]
    assert client.replaced == [expected]
    assert receipt.version_id == "replacement-version"
