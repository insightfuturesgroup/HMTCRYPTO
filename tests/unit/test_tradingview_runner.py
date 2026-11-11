from typing import cast
from uuid import UUID, uuid4

import pytest

from hmt_crypto.persistence.db import Database
from hmt_crypto.worker.tradingview_runner import (
    _payload_rows,
    admit_one_tradingview_signal,
)


def test_payload_rows_accepts_only_json_arrays() -> None:
    assert _payload_rows('[{"product_id": 27}]') == [{"product_id": 27}]

    for value in (None, "", "{}", '"row"'):
        with pytest.raises(ValueError, match="invalid reconciliation payload"):
            _payload_rows(value)


@pytest.mark.asyncio
async def test_tradingview_admission_rejects_invalid_worker_scope() -> None:
    with pytest.raises(ValueError, match="invalid TradingView worker scope"):
        await admit_one_tradingview_signal(
            cast(Database, object()),
            account_id=UUID(int=0),
            owner_id=uuid4(),
        )
