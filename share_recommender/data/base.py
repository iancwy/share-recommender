"""The interface every market data provider implements."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class Quote:
    ticker: str
    exchange: str
    price: Decimal
    currency: str
    as_of: datetime


@dataclass(frozen=True)
class PriceBar:
    day: date
    close: Decimal


@dataclass(frozen=True)
class Fundamentals:
    """Company or fund figures. Every field is optional: providers fill what they have.

    Fractions are fractions (0.03 = 3%); amounts are in the exchange currency (pounds, not
    pence, on the LSE)."""

    dividend_yield: Decimal | None = None  # trailing 12 months
    pe_ratio: Decimal | None = None        # trailing
    market_cap: Decimal | None = None
    sector: str | None = None
    name: str | None = None
    quote_type: str | None = None          # "EQUITY" or "ETF"
    fund_category: str | None = None       # funds only, e.g. "Intermediate Core Bond"
    industry: str | None = None
    forward_pe: Decimal | None = None
    revenue_growth: Decimal | None = None  # year on year
    earnings_growth: Decimal | None = None
    return_on_equity: Decimal | None = None
    profit_margin: Decimal | None = None
    debt_to_equity: Decimal | None = None  # a ratio: 1.5 means debt is 1.5x equity
    beta: Decimal | None = None
    avg_volume: Decimal | None = None      # units a day, over 3 months
    high_52w: Decimal | None = None
    low_52w: Decimal | None = None
    expense_ratio: Decimal | None = None   # funds, a yearly fraction (0.0003 = 0.03%)
    summary: str | None = None             # first 300 characters of the description

    @property
    def is_fund(self) -> bool:
        return (self.quote_type or "").upper() in {"ETF", "MUTUALFUND"}

    def to_json(self) -> dict:
        """For the shared cache: decimals as strings, empty fields left out."""
        return {f.name: str(getattr(self, f.name)) if isinstance(getattr(self, f.name), Decimal)
                else getattr(self, f.name)
                for f in fields(self) if getattr(self, f.name) is not None}

    @classmethod
    def from_json(cls, data: dict) -> "Fundamentals":
        known = {f.name for f in fields(cls)}
        values = {}
        for name, value in data.items():
            if name not in known or value is None:
                continue  # fields added or dropped since this was cached
            values[name] = value if name in _TEXT_FIELDS else Decimal(str(value))
        return cls(**values)


_TEXT_FIELDS = {"sector", "name", "quote_type", "fund_category", "industry", "summary"}


@dataclass(frozen=True)
class Candidate:
    """A share found by a provider's screener, with what the screener returns in one go."""

    ticker: str
    exchange: str
    name: str | None
    kind: str                              # "share" or "etf"
    price: Decimal | None                  # exchange currency
    market_cap: Decimal | None             # exchange currency
    pe_ratio: Decimal | None = None
    dividend_yield: Decimal | None = None  # fraction
    change_52w: Decimal | None = None      # fraction
    avg_volume: Decimal | None = None
    above_200d: bool | None = None         # price above its 200-day average

    @property
    def key(self) -> tuple[str, str]:
        return (self.ticker, self.exchange)


class MarketDataProvider(ABC):
    """Fetches prices for a ticker on one of the exchanges in ``core.exchanges``.

    Implementations translate (ticker, exchange) into their own symbol format.
    """

    name: str

    @abstractmethod
    def get_quote(self, ticker: str, exchange: str) -> Quote | None:
        """Latest available price, or None if the symbol is not found."""

    @abstractmethod
    def get_history(self, ticker: str, exchange: str, start: date, end: date) -> list[PriceBar]:
        """Daily closing prices between start and end, oldest first."""

    @abstractmethod
    def get_fx_rate(self, base: str, quote: str) -> Decimal | None:
        """Units of ``quote`` currency per 1 unit of ``base``, e.g. AUD->USD 0.65, or None."""

    def get_fx_history(self, base: str, quote: str, start: date, end: date) -> list[PriceBar]:
        """Daily ``quote`` per 1 ``base`` between start and end, oldest first. Optional."""
        return []

    def get_fundamentals(self, ticker: str, exchange: str) -> Fundamentals | None:
        """Company or fund figures. Providers without them return None."""
        return None

    def discover(self, exchange: str, limit: int = 150) -> list[Candidate]:
        """The largest listed shares on an exchange, biggest first. Optional; [] if unsupported."""
        return []

    def get_quotes(self, symbols: list[tuple[str, str]]) -> dict[tuple[str, str], Quote | None]:
        """Quotes for many (ticker, exchange) pairs. Override when the vendor supports batching."""
        return {s: self.get_quote(*s) for s in symbols}

    def get_fx_rates(self, base: str, currencies: list[str]) -> dict[str, Decimal | None]:
        return {c: Decimal(1) if c == base else self.get_fx_rate(base, c) for c in currencies}

    def get_many_fundamentals(self, symbols: list[tuple[str, str]]
                              ) -> dict[tuple[str, str], Fundamentals | None]:
        return {s: self.get_fundamentals(*s) for s in symbols}
