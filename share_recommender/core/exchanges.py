"""Exchanges the app knows about, and how each maps to data provider symbols."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Exchange:
    code: str
    name: str
    currency: str
    yahoo_suffix: str  # appended to the ticker for Yahoo Finance, e.g. "BHP" -> "BHP.AX"


EXCHANGES: dict[str, Exchange] = {
    e.code: e
    for e in [
        Exchange("ASX", "Australian Securities Exchange", "AUD", ".AX"),
        Exchange("NYSE", "New York Stock Exchange", "USD", ""),
        Exchange("NASDAQ", "Nasdaq", "USD", ""),
        Exchange("LSE", "London Stock Exchange", "GBP", ".L"),
        Exchange("TSX", "Toronto Stock Exchange", "CAD", ".TO"),
        Exchange("SGX", "Singapore Exchange", "SGD", ".SI"),
        Exchange("HKEX", "Hong Kong Stock Exchange", "HKD", ".HK"),
        Exchange("NZX", "New Zealand Exchange", "NZD", ".NZ"),
    ]
}


def get_exchange(code: str) -> Exchange:
    try:
        return EXCHANGES[code.upper()]
    except KeyError:
        raise ValueError(f"Unknown exchange '{code}'. Known: {', '.join(EXCHANGES)}") from None
