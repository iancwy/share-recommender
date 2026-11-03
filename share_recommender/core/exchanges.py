"""Exchanges the app knows about, and how each maps to data provider symbols."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Exchange:
    code: str
    name: str
    currency: str
    yahoo_suffix: str  # appended to the ticker for Yahoo Finance, e.g. "BHP" -> "BHP.AX"
    # Yahoo's own codes for the exchange, used by its share screener (checked Nov 2026).
    yahoo_exchanges: tuple[str, ...] = ()


EXCHANGES: dict[str, Exchange] = {
    e.code: e
    for e in [
        Exchange("ASX", "Australian Securities Exchange", "AUD", ".AX", ("ASX",)),
        Exchange("NYSE", "New York Stock Exchange", "USD", "", ("NYQ", "ASE")),
        Exchange("NASDAQ", "Nasdaq", "USD", "", ("NMS", "NGM", "NCM")),
        Exchange("LSE", "London Stock Exchange", "GBP", ".L", ("LSE",)),
        Exchange("TSX", "Toronto Stock Exchange", "CAD", ".TO", ("TOR",)),
        Exchange("SGX", "Singapore Exchange", "SGD", ".SI", ("SES",)),
        Exchange("HKEX", "Hong Kong Stock Exchange", "HKD", ".HK", ("HKG",)),
        Exchange("NZX", "New Zealand Exchange", "NZD", ".NZ", ("NZE",)),
    ]
}


def get_exchange(code: str) -> Exchange:
    try:
        return EXCHANGES[code.upper()]
    except KeyError:
        raise ValueError(f"Unknown exchange '{code}'. Known: {', '.join(EXCHANGES)}") from None
