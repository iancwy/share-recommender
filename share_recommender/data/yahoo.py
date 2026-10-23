"""Yahoo Finance via the unofficial ``yfinance`` package. Free, no API key, global coverage."""

import logging
from datetime import UTC, date, timedelta
from decimal import Decimal

from share_recommender.core.exchanges import get_exchange
from share_recommender.data.base import MarketDataProvider, PriceBar, Quote

log = logging.getLogger(__name__)


def yahoo_symbol(ticker: str, exchange: str) -> str:
    return f"{ticker.upper()}{get_exchange(exchange).yahoo_suffix}"


class YahooFinanceProvider(MarketDataProvider):
    name = "yahoo"

    def get_quote(self, ticker: str, exchange: str) -> Quote | None:
        import yfinance as yf

        symbol = yahoo_symbol(ticker, exchange)
        try:
            hist = yf.Ticker(symbol).history(period="5d", auto_adjust=False)
        except Exception:  # yfinance raises a variety of errors on bad symbols / rate limits
            log.warning("Yahoo quote failed for %s", symbol, exc_info=True)
            return None
        if hist.empty:
            return None
        last = hist.iloc[-1]
        as_of = hist.index[-1].to_pydatetime()
        return Quote(
            ticker=ticker.upper(),
            exchange=exchange.upper(),
            price=Decimal(str(round(float(last["Close"]), 4))),
            currency=get_exchange(exchange).currency,
            as_of=as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC),
        )

    def get_history(self, ticker: str, exchange: str, start: date, end: date) -> list[PriceBar]:
        import yfinance as yf

        symbol = yahoo_symbol(ticker, exchange)
        # yfinance treats `end` as exclusive.
        hist = yf.Ticker(symbol).history(
            start=start, end=end + timedelta(days=1), auto_adjust=False
        )
        return [
            PriceBar(day=idx.date(), close=Decimal(str(round(float(row["Close"]), 4))))
            for idx, row in hist.iterrows()
        ]
