"""AWS Secrets Manager adapter; secret plaintext never crosses its return boundary."""

import asyncio
import importlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from botocore.exceptions import ClientError  # type: ignore[import-untyped]


@dataclass(frozen=True, slots=True)
class StoredSecret:
    arn: str
    version_id: str


class AwsDeltaSecretStore:
    def __init__(self, *, region: str, prefix: str) -> None:
        boto3 = importlib.import_module("boto3")
        self._client: Any = boto3.client("secretsmanager", region_name=region)
        self._prefix = prefix.rstrip("/")

    async def put(
        self, *, account_id: UUID, version: int, api_key: str, api_secret: str
    ) -> StoredSecret:
        name = f"{self._prefix}/{account_id}/v{version}"
        value = json.dumps(
            {"api_key": api_key, "api_secret": api_secret},
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

        def create() -> dict[str, Any]:
            result: object = self._client.create_secret(
                Name=name,
                Description="HMT Crypto Delta credential; managed by the control API",
                SecretString=value,
                Tags=[
                    {"Key": "Application", "Value": "hmt-crypto"},
                    {"Key": "AccountId", "Value": str(account_id)},
                ],
            )
            if not isinstance(result, dict):
                raise RuntimeError("secret store returned an invalid receipt")
            return {str(key): value for key, value in result.items()}

        try:
            result = await asyncio.to_thread(create)
        except ClientError as error:
            detail = error.response.get("Error", {})
            code = detail.get("Code")
            message = detail.get("Message")
            if (
                code != "InvalidRequestException"
                or type(message) is not str
                or "scheduled for deletion" not in message.casefold()
            ):
                raise

            def restore_and_replace() -> dict[str, Any]:
                restored = self._client.restore_secret(SecretId=name)
                replaced = self._client.put_secret_value(SecretId=name, SecretString=value)
                arn = replaced.get("ARN") or restored.get("ARN")
                return {"ARN": arn, "VersionId": replaced.get("VersionId")}

            result = await asyncio.to_thread(restore_and_replace)
        arn, version_id = result.get("ARN"), result.get("VersionId")
        if type(arn) is not str or type(version_id) is not str:
            raise RuntimeError("secret store returned an invalid receipt")
        return StoredSecret(arn, version_id)

    async def quarantine(self, arn: str) -> None:
        await asyncio.to_thread(
            self._client.delete_secret,
            SecretId=arn,
            RecoveryWindowInDays=7,
        )

    async def read(self, arn: str) -> tuple[str, str]:
        """Return one credential pair solely to an immediate trusted transport boundary."""

        def fetch() -> object:
            return self._client.get_secret_value(SecretId=arn, VersionStage="AWSCURRENT")

        result = await asyncio.to_thread(fetch)
        if not isinstance(result, dict) or type(result.get("SecretString")) is not str:
            raise RuntimeError("credential secret is unavailable")
        try:
            value = json.loads(result["SecretString"])
        except (TypeError, ValueError):
            raise RuntimeError("credential secret is invalid") from None
        if not isinstance(value, dict) or set(value) != {"api_key", "api_secret"}:
            raise RuntimeError("credential secret is invalid")
        api_key, api_secret = value["api_key"], value["api_secret"]
        if type(api_key) is not str or type(api_secret) is not str or not api_key or not api_secret:
            raise RuntimeError("credential secret is invalid")
        return api_key, api_secret


__all__ = ["AwsDeltaSecretStore", "StoredSecret"]
