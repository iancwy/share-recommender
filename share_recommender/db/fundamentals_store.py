"""The shared fundamentals cache in Supabase (table fundamentals_cache)."""

from datetime import datetime

from share_recommender.data.base import Fundamentals
from supabase import Client

_BATCH = 200


class SupabaseFundamentalsStore:
    def __init__(self, client: Client):
        self._db = client

    def get_many(self, keys: list[tuple[str, str]]
                 ) -> dict[tuple[str, str], tuple[Fundamentals, datetime]]:
        """Cached fundamentals and when they were fetched, for the keys that are cached."""
        out = {}
        by_exchange: dict[str, list[str]] = {}
        for ticker, exchange in keys:
            by_exchange.setdefault(exchange, []).append(ticker)
        for exchange, tickers in by_exchange.items():
            for i in range(0, len(tickers), _BATCH):
                rows = (self._db.table("fundamentals_cache").select("ticker, data, fetched_at")
                        .eq("exchange", exchange).in_("ticker", tickers[i:i + _BATCH])
                        .execute().data)
                for r in rows:
                    out[(r["ticker"], exchange)] = (Fundamentals.from_json(r["data"]),
                                                    datetime.fromisoformat(r["fetched_at"]))
        return out

    def save_many(self, items: dict[tuple[str, str], Fundamentals]) -> None:
        now = datetime.now().astimezone().isoformat()
        rows = [{"ticker": t, "exchange": e, "data": f.to_json(), "fetched_at": now}
                for (t, e), f in items.items()]
        for i in range(0, len(rows), _BATCH):
            self._db.table("fundamentals_cache").upsert(rows[i:i + _BATCH]).execute()
