"""Look up market data providers by name (set MARKET_DATA_PROVIDER in .env)."""

from collections.abc import Callable

from share_recommender.data.base import MarketDataProvider
from share_recommender.data.yahoo import YahooFinanceProvider

_PROVIDERS: dict[str, Callable[[], MarketDataProvider]] = {
    "yahoo": YahooFinanceProvider,
}


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)


def register_provider(name: str, factory: Callable[[], MarketDataProvider]) -> None:
    _PROVIDERS[name] = factory


def get_provider(name: str | None = None) -> MarketDataProvider:
    if name is None:
        from share_recommender.config import get_settings

        name = get_settings().market_data_provider
    try:
        return _PROVIDERS[name]()
    except KeyError:
        raise ValueError(f"Unknown market data provider '{name}'. "
                         f"Available: {', '.join(available_providers())}") from None
