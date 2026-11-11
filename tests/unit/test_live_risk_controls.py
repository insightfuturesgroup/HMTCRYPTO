from decimal import Decimal

import pytest
from fastapi import HTTPException

from hmt_crypto.api.platform_api import _positive_decimal


@pytest.mark.parametrize("value", ("1", "0.000000000000000001", "999999.25"))
def test_positive_risk_limit_accepts_exact_decimal(value: str) -> None:
    assert _positive_decimal(value) == Decimal(value)


@pytest.mark.parametrize("value", ("0", "-1", "NaN", "Infinity", "0.0000000000000000001"))
def test_positive_risk_limit_fails_closed(value: str) -> None:
    with pytest.raises(HTTPException) as error:
        _positive_decimal(value)
    assert error.value.status_code == 400
