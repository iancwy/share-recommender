"""Company and fund fundamentals, cached for a week in a shared store.

Fundamentals are one slow request per symbol, and they change slowly, so the picker reads
them from the shared Supabase cache and only fetches what's missing or more than a week old.
If the store fails (for example the migration hasn't been run), they come straight from the
provider, uncached.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol

from share_recommender.data.base import Fundamentals, MarketDataProvider

log = logging.getLogger(__name__)

Key = tuple[str, str]


class FundamentalsStore(Protocol):
    def get_many(self, keys: list[Key]) -> dict[Key, tuple[Fundamentals, datetime]]: ...

    def save_many(self, items: dict[Key, Fundamentals]) -> None: ...


class CachedFundamentals:
    def __init__(self, provider: MarketDataProvider, store: FundamentalsStore | None,
                 max_age_days: int = 7, now: datetime | None = None):
        self.provider = provider
        self.store = store
        self.max_age = timedelta(days=max_age_days)
        self.now = now or datetime.now(UTC)

    def get_many(self, keys: list[Key]) -> dict[Key, Fundamentals | None]:
        keys = list(dict.fromkeys(keys))
        cached: dict[Key, tuple[Fundamentals, datetime]] = {}
        if self.store is not None:
            try:
                cached = self.store.get_many(keys)
            except Exception:
                log.warning("Fundamentals cache unavailable; fetching directly", exc_info=True)
        fresh = {k: f for k, (f, at) in cached.items() if self.now - at <= self.max_age}
        missing = [k for k in keys if k not in fresh]
        fetched = self.provider.get_many_fundamentals(missing) if missing else {}
        found = {k: f for k, f in fetched.items() if f is not None}
        if found and self.store is not None:
            try:
                self.store.save_many(found)
            except Exception:
                log.warning("Couldn't save fundamentals to the cache", exc_info=True)
        # A failed refresh keeps the stale copy rather than losing the figures.
        stale = {k: f for k, (f, _) in cached.items() if k not in found}
        return {k: found.get(k) or fresh.get(k) or stale.get(k) for k in keys}
