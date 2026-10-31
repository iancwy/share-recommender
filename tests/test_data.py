from decimal import Decimal

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


def test_yahoo_converts_pence_to_pounds(monkeypatch):
    import pandas as pd
    import yfinance as yf

    class FakeTicker:
        history_metadata = {"currency": "GBp"}

        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **_):
            return pd.DataFrame({"Close": [12824.0]}, index=pd.to_datetime(["2026-10-23"]))

    monkeypatch.setattr(yf, "Ticker", FakeTicker)
    quote = YahooFinanceProvider().get_quote("AZN", "LSE")
    assert quote.price == Decimal("128.24") and quote.currency == "GBP"
