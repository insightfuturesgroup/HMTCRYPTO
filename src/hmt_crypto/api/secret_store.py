"""AWS Secrets Manager adapter; secret plaintext never crosses its return boundary."""

import asyncio
import importlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID


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

        result = await asyncio.to_thread(create)
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


__all__ = ["AwsDeltaSecretStore", "StoredSecret"]
