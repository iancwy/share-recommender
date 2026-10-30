"""The interface every market data provider implements."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
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
    dividend_yield: Decimal | None = None  # trailing 12 months, as a fraction (0.03 = 3%)
    pe_ratio: Decimal | None = None
    market_cap: Decimal | None = None      # in the exchange currency
    sector: str | None = None


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
        """Basic ratios used to rank shares. Providers without them return None."""
        return None

    def get_quotes(self, symbols: list[tuple[str, str]]) -> dict[tuple[str, str], Quote | None]:
        """Quotes for many (ticker, exchange) pairs. Override when the vendor supports batching."""
        return {s: self.get_quote(*s) for s in symbols}

    def get_fx_rates(self, base: str, currencies: list[str]) -> dict[str, Decimal | None]:
        return {c: Decimal(1) if c == base else self.get_fx_rate(base, c) for c in currencies}

    def get_many_fundamentals(self, symbols: list[tuple[str, str]]
                              ) -> dict[tuple[str, str], Fundamentals | None]:
        return {s: self.get_fundamentals(*s) for s in symbols}
