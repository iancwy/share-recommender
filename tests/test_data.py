import pytest

from share_recommender.data import available_providers, get_provider
from share_recommender.data.yahoo import YahooFinanceProvider, yahoo_symbol


@pytest.mark.parametrize("ticker, exchange, expected", [
    ("vas", "ASX", "VAS.AX"), ("AAPL", "NASDAQ", "AAPL"), ("VUSA", "LSE", "VUSA.L"),
    ("D05", "SGX", "D05.SI"),
])
def test_yahoo_symbol(ticker, exchange, expected):
    assert yahoo_symbol(ticker, exchange) == expected


def test_registry():
    assert "yahoo" in available_providers()
    assert isinstance(get_provider("yahoo"), YahooFinanceProvider)
    with pytest.raises(ValueError):
        get_provider("nope")
