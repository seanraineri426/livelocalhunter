from __future__ import annotations

import pytest

from lla.market_rents import validate_market_rent_source


def test_validate_market_rent_source_normalizes_numbers():
    source = validate_market_rent_source(
        {
            "source_type": "broker",
            "bedroom_count": "2",
            "market_rent_monthly": "3100.50",
            "vacancy_rate": "0.055",
            "confidence": "medium",
        }
    )
    assert source["bedroom_count"] == 2
    assert source["market_rent_monthly"] == 3100.5
    assert source["vacancy_rate"] == 0.055


def test_validate_market_rent_source_rejects_unknown_source_type():
    with pytest.raises(ValueError):
        validate_market_rent_source({"source_type": "spreadsheet", "market_rent_monthly": 3000})


def test_validate_market_rent_source_rejects_bad_vacancy_rate():
    with pytest.raises(ValueError):
        validate_market_rent_source({"source_type": "manual", "market_rent_monthly": 3000, "vacancy_rate": 1.2})
