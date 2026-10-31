"""Price and exchange-rate history, cached day by day in a ``PriceStore``.

Each instrument remembers which date range has been fetched and when. A request only goes
to the provider for days before that range, or for days after it if it wasn't already
refreshed today. The last cached day is always refetched, because a close fetched during
trading is really the price at that moment. If the store fails (for example the migration
hasn't been run), history comes straight from the provider, uncached.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Protocol

from share_recommender.data.base import MarketDataProvider, PriceBar

log = logging.getLogger(__name__)

FX_EXCHANGE = "FX"


class PriceStore(Protocol):
    def get_sync(self, exchange: str, ticker: str) -> tuple[date, date, datetime] | None:
        """(first_day, last_day, synced_at) of what has been fetched, or None."""

    def get_bars(self, exchange: str, ticker: str, start: date, end: date) -> list[PriceBar]:
        """Cached closes between start and end inclusive, oldest first."""

    def save(self, exchange: str, ticker: str, bars: list[PriceBar], first_day: date,
             last_day: date) -> None:
        """Upsert the bars and record that first_day..last_day has now been fetched."""


class CachedHistory:
    def __init__(self, provider: MarketDataProvider, store: PriceStore | None,
                 today: date | None = None):
        self.provider = provider
        self.store = store
        self.today = today or date.today()

    def history(self, ticker: str, exchange: str, start: date, end: date) -> list[PriceBar]:
        return self._cached(exchange, ticker, start, end,
                            lambda s, e: self.provider.get_history(ticker, exchange, s, e))

    def fx_history(self, base: str, quote: str, start: date, end: date) -> list[PriceBar]:
        return self._cached(FX_EXCHANGE, f"{base}{quote}", start, end,
                            lambda s, e: self.provider.get_fx_history(base, quote, s, e))

    def _cached(self, exchange, ticker, start, end, fetch) -> list[PriceBar]:
        end = min(end, self.today)
        if start > end:
            return []
        if self.store is None:
            return _safe_fetch(fetch, start, end)
        try:
            sync = self.store.get_sync(exchange, ticker)
        except Exception:
            log.warning("Price cache unavailable; fetching %s %s directly", exchange, ticker,
                        exc_info=True)
            return _safe_fetch(fetch, start, end)

        if sync is None:
            segments = [(start, end)]
            first, last = start, end
        else:
            first, last, synced_at = sync
            segments = []
            if start < first:
                segments.append((start, first - timedelta(days=1)))
            if end >= last and synced_at.date() < self.today:
                segments.append((last, end))
            first, last = min(first, start), max(last, end)

        bars = [b for s, e in segments for b in _safe_fetch(fetch, s, e)]
        if segments:
            try:
                self.store.save(exchange, ticker, bars, first, last)
            except Exception:
                log.warning("Couldn't save %s %s to the price cache", exchange, ticker,
                            exc_info=True)
        try:
            cached = self.store.get_bars(exchange, ticker, start, end)
        except Exception:
            log.warning("Couldn't read %s %s from the price cache", exchange, ticker,
                        exc_info=True)
            cached = []
        return _merge(cached, [b for b in bars if start <= b.day <= end])


def _safe_fetch(fetch, start: date, end: date) -> list[PriceBar]:
    try:
        return fetch(start, end)
    except Exception:
        log.warning("History fetch failed for %s..%s", start, end, exc_info=True)
        return []


def _merge(*lists: list[PriceBar]) -> list[PriceBar]:
    by_day = {b.day: b for bars in lists for b in bars}
    return [by_day[d] for d in sorted(by_day)]
