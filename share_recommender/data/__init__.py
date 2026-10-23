"""Market data providers. Use ``get_provider`` so callers never import a vendor directly."""

from share_recommender.data.base import MarketDataProvider, PriceBar, Quote
from share_recommender.data.registry import available_providers, get_provider

__all__ = ["MarketDataProvider", "PriceBar", "Quote", "available_providers", "get_provider"]
