"""Yahoo Finance via the unofficial ``yfinance`` package. Free, no API key, global coverage."""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, timedelta
from decimal import Decimal

from share_recommender.core.exchanges import get_exchange
from share_recommender.data.base import (
    Candidate,
    Fundamentals,
    MarketDataProvider,
    PriceBar,
    Quote,
)

# Fundamentals are one slow request per symbol, so fetch a few at a time.
_PARALLEL_REQUESTS = 8

log = logging.getLogger(__name__)

# Yahoo prices most London listings in pence ("GBp") rather than pounds.
_MINOR_UNITS = {"GBp": Decimal(100), "GBX": Decimal(100), "ZAc": Decimal(100), "ILA": Decimal(100)}
_MAJOR_CURRENCY = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}


def yahoo_symbol(ticker: str, exchange: str) -> str:
    return f"{ticker.upper()}{get_exchange(exchange).yahoo_suffix}"


def our_ticker(symbol: str, exchange: str) -> str:
    """'BHP.AX' -> 'BHP', '0700.HK' -> '0700', 'BRK-B' -> 'BRK-B'."""
    suffix = get_exchange(exchange).yahoo_suffix
    return symbol[:-len(suffix)] if suffix and symbol.endswith(suffix) else symbol


_SCREEN_PAGE = 100            # Yahoo's screener returns at most this many per request
_SCREEN_MIN_MARKET_CAP = 1e8  # in the exchange currency; smaller companies aren't useful here


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
        # Prices (and the 52-week range) can be in pence; market cap is always in pounds.
        divisor = _MINOR_UNITS.get(info.get("currency"), Decimal(1))

        def price(key):
            value = _decimal(info.get(key))
            return (value / divisor).quantize(Decimal("0.0001")) if value is not None else None

        def ratio(key, scale=1):
            value = _decimal(info.get(key), 6)
            return value / scale if value is not None else None

        summary = info.get("longBusinessSummary")
        return Fundamentals(
            # "trailingAnnualDividendYield" is a fraction; "dividendYield" is now a percentage.
            dividend_yield=ratio("trailingAnnualDividendYield"),
            pe_ratio=_decimal(info.get("trailingPE"), 2),
            market_cap=_decimal(info.get("marketCap"), 0),
            sector=info.get("sector"),
            name=info.get("longName") or info.get("shortName"),
            quote_type=info.get("quoteType"),
            fund_category=info.get("category"),
            industry=info.get("industry"),
            forward_pe=_decimal(info.get("forwardPE"), 2),
            revenue_growth=ratio("revenueGrowth"),
            earnings_growth=ratio("earningsGrowth"),
            return_on_equity=ratio("returnOnEquity"),
            profit_margin=ratio("profitMargins"),
            debt_to_equity=ratio("debtToEquity", 100),        # Yahoo gives 150 for 1.5x
            beta=_decimal(info.get("beta"), 3),
            avg_volume=_decimal(info.get("averageVolume"), 0),
            high_52w=price("fiftyTwoWeekHigh"),
            low_52w=price("fiftyTwoWeekLow"),
            # A percentage (0.03 = 0.03%), US funds only so far.
            expense_ratio=ratio("netExpenseRatio", 100) or ratio("annualReportExpenseRatio", 100),
            summary=summary[:300] if summary else None,
        )

    def get_many_fundamentals(self, symbols: list[tuple[str, str]]
                              ) -> dict[tuple[str, str], Fundamentals | None]:
        with ThreadPoolExecutor(max_workers=_PARALLEL_REQUESTS) as pool:
            results = pool.map(lambda s: self.get_fundamentals(*s), symbols)
        return dict(zip(symbols, results, strict=True))

    def discover(self, exchange: str, limit: int = 150) -> list[Candidate]:
        """The largest shares on an exchange from Yahoo's screener, biggest first.

        One request per 100 shares, with name, price, size, P/E, yield, 52-week change, volume
        and the 200-day average already included. Depositary receipts of foreign companies
        (Toronto's "CDR"s) and the less-traded class of companies with two (GOOG and GOOGL)
        are left out. Returns [] if the screener fails, so callers fall back to the seed lists.
        """
        import yfinance as yf
        from yfinance import EquityQuery as Q

        codes = get_exchange(exchange).yahoo_exchanges
        if not codes:
            return []
        on_exchange = Q("eq", ["exchange", codes[0]]) if len(codes) == 1 else \
            Q("or", [Q("eq", ["exchange", c]) for c in codes])
        query = Q("and", [on_exchange, Q("gt", ["intradaymarketcap", _SCREEN_MIN_MARKET_CAP])])
        currency = get_exchange(exchange).currency
        best: dict[str, Candidate] = {}  # one per company, by name
        traded: dict[str, Decimal] = {}
        offset = 0
        try:
            # Page until there are enough real companies: Toronto's largest listings, for one,
            # are mostly CDRs. Stop after a few pages either way.
            while len(best) < limit and offset < limit * 4:
                page = yf.screen(query, offset=offset, size=_SCREEN_PAGE,
                                 sortField="intradaymarketcap", sortAsc=False)
                got = page.get("quotes", [])
                offset += len(got)
                for q in got:
                    self._keep_best(q, exchange, currency, best, traded)
                if len(got) < _SCREEN_PAGE:
                    break
        except Exception:
            log.warning("Yahoo screener failed for %s", exchange, exc_info=True)
            if not best:
                return []
        return sorted(best.values(), key=lambda c: c.market_cap or 0, reverse=True)[:limit]

    @staticmethod
    def _keep_best(q: dict, exchange: str, currency: str, best: dict, traded: dict) -> None:
        """Add one screener quote to ``best`` unless it's not wanted or a busier class exists."""
        name = q.get("longName") or q.get("shortName")
        quoted_in = q.get("currency") or ""
        divisor = _MINOR_UNITS.get(quoted_in, Decimal(1))
        if (q.get("quoteType") != "EQUITY"
                or "CDR" in (q.get("shortName") or "").upper()  # e.g. "APPLE CDR (CAD HEDGED)"
                or _MAJOR_CURRENCY.get(quoted_in, quoted_in.upper()) != currency):
            return  # a foreign-currency line, e.g. Toyota in yen on the LSE
        price = _decimal(q.get("regularMarketPrice"))
        price = price / divisor if price is not None else None
        avg_200 = _decimal(q.get("twoHundredDayAverage"))
        change = _decimal(q.get("fiftyTwoWeekChangePercent"), 6)
        volume = _decimal(q.get("averageDailyVolume3Month"), 0)
        c = Candidate(
            ticker=our_ticker(q["symbol"], exchange), exchange=exchange, name=name,
            kind="share", price=price, market_cap=_decimal(q.get("marketCap"), 0),
            pe_ratio=_decimal(q.get("trailingPE"), 2),
            dividend_yield=_decimal(q.get("trailingAnnualDividendYield"), 6),
            change_52w=change / 100 if change is not None else None,
            avg_volume=volume,
            above_200d=(price > avg_200 / divisor) if price is not None and avg_200 else None)
        value = (volume or 0) * (price or 0)
        company = (name or c.ticker).lower()
        if company not in best or value > traded[company]:
            best[company], traded[company] = c, value

