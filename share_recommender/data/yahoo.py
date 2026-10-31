"""Yahoo Finance via the unofficial ``yfinance`` package. Free, no API key, global coverage."""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, timedelta
from decimal import Decimal

from share_recommender.core.exchanges import get_exchange
from share_recommender.data.base import Fundamentals, MarketDataProvider, PriceBar, Quote

# Fundamentals are one slow request per symbol, so fetch a few at a time.
_PARALLEL_REQUESTS = 8

log = logging.getLogger(__name__)

# Yahoo prices most London listings in pence ("GBp") rather than pounds.
_MINOR_UNITS = {"GBp": Decimal(100), "GBX": Decimal(100), "ZAc": Decimal(100), "ILA": Decimal(100)}


def yahoo_symbol(ticker: str, exchange: str) -> str:
    return f"{ticker.upper()}{get_exchange(exchange).yahoo_suffix}"


def _decimal(value, places: int = 4) -> Decimal | None:
    try:
        return Decimal(str(round(float(value), places))) if value is not None else None
    except (TypeError, ValueError):
        return None


class YahooFinanceProvider(MarketDataProvider):
    name = "yahoo"

    def get_quote(self, ticker: str, exchange: str) -> Quote | None:
        import yfinance as yf

        symbol = yahoo_symbol(ticker, exchange)
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period="5d", auto_adjust=False)
            divisor = _MINOR_UNITS.get((t.history_metadata or {}).get("currency"), Decimal(1))
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
            price=_decimal(Decimal(str(float(last["Close"]))) / divisor),
            currency=get_exchange(exchange).currency,
            as_of=as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC),
        )

    def get_history(self, ticker: str, exchange: str, start: date, end: date) -> list[PriceBar]:
        import yfinance as yf

        symbol = yahoo_symbol(ticker, exchange)
        t = yf.Ticker(symbol)
        # yfinance treats `end` as exclusive.
        hist = t.history(start=start, end=end + timedelta(days=1), auto_adjust=False)
        divisor = _MINOR_UNITS.get((t.history_metadata or {}).get("currency"), Decimal(1))
        return [
            PriceBar(day=idx.date(), close=_decimal(Decimal(str(float(row["Close"]))) / divisor))
            for idx, row in hist.iterrows()
        ]

    def get_fx_rate(self, base: str, quote: str) -> Decimal | None:
        import yfinance as yf

        if base == quote:
            return Decimal(1)
        symbol = f"{base}{quote}=X"
        try:
            hist = yf.Ticker(symbol).history(period="5d")
        except Exception:
            log.warning("Yahoo FX rate failed for %s", symbol, exc_info=True)
            return None
        return None if hist.empty else _decimal(hist["Close"].iloc[-1], 6)

    def get_fx_history(self, base: str, quote: str, start: date, end: date) -> list[PriceBar]:
        import yfinance as yf

        symbol = f"{base}{quote}=X"
        try:
            hist = yf.Ticker(symbol).history(start=start, end=end + timedelta(days=1))
        except Exception:
            log.warning("Yahoo FX history failed for %s", symbol, exc_info=True)
            return []
        return [PriceBar(day=idx.date(), close=_decimal(row["Close"], 6))
                for idx, row in hist.iterrows()]

    def get_fundamentals(self, ticker: str, exchange: str) -> Fundamentals | None:
        import yfinance as yf

        symbol = yahoo_symbol(ticker, exchange)
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception:
            log.warning("Yahoo fundamentals failed for %s", symbol, exc_info=True)
            return None
        if not info:
            return None
        return Fundamentals(
            # "trailingAnnualDividendYield" is a fraction; "dividendYield" is now a percentage.
            dividend_yield=_decimal(info.get("trailingAnnualDividendYield"), 6),
            pe_ratio=_decimal(info.get("trailingPE"), 2),
            market_cap=_decimal(info.get("marketCap"), 0),
            sector=info.get("sector"),
        )

    def get_many_fundamentals(self, symbols: list[tuple[str, str]]
                              ) -> dict[tuple[str, str], Fundamentals | None]:
        with ThreadPoolExecutor(max_workers=_PARALLEL_REQUESTS) as pool:
            results = pool.map(lambda s: self.get_fundamentals(*s), symbols)
        return dict(zip(symbols, results, strict=True))
