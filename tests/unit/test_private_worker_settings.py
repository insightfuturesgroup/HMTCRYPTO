from uuid import UUID

import pytest
from pydantic import ValidationError

from hmt_crypto.worker.private_runner import PrivateWorkerSettings


def test_private_worker_requires_lease_longer_than_two_cycles() -> None:
    settings = PrivateWorkerSettings(
        HMT_PRIVATE_WORKER_ID=UUID(int=7),
        HMT_AWS_REGION="ap-southeast-2",
        HMT_DELTA_SECRET_PREFIX="hmt-crypto/delta",
        HMT_PRIVATE_INTERVAL_SECONDS=15,
        HMT_PRIVATE_LEASE_SECONDS=45,
    )
    assert settings.owner_id == UUID(int=7)

    with pytest.raises(ValidationError):
        PrivateWorkerSettings(
            HMT_PRIVATE_WORKER_ID=UUID(int=7),
            HMT_AWS_REGION="ap-southeast-2",
            HMT_DELTA_SECRET_PREFIX="hmt-crypto/delta",
            HMT_PRIVATE_INTERVAL_SECONDS=15,
            HMT_PRIVATE_LEASE_SECONDS=20,
        )


def test_private_worker_rejects_foreign_secret_prefix() -> None:
    with pytest.raises(ValidationError):
        PrivateWorkerSettings(
            HMT_PRIVATE_WORKER_ID=UUID(int=7),
            HMT_AWS_REGION="ap-southeast-2",
            HMT_DELTA_SECRET_PREFIX="other-app/delta",
            HMT_PRIVATE_INTERVAL_SECONDS=15,
            HMT_PRIVATE_LEASE_SECONDS=45,
        )
