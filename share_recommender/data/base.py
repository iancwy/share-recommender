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

    def get_quotes(self, symbols: list[tuple[str, str]]) -> dict[tuple[str, str], Quote | None]:
        """Quotes for many (ticker, exchange) pairs. Override when the vendor supports batching."""
        return {s: self.get_quote(*s) for s in symbols}
