"""The shared daily price cache in Supabase (tables price_history and price_sync)."""

from datetime import date, datetime
from decimal import Decimal

from share_recommender.data.base import PriceBar
from supabase import Client

_PAGE = 1000  # Supabase returns at most this many rows per request
_BATCH = 500


class SupabasePriceStore:
    def __init__(self, client: Client):
        self._db = client

    def get_sync(self, exchange: str, ticker: str) -> tuple[date, date, datetime] | None:
        rows = (self._db.table("price_sync").select("first_day, last_day, synced_at")
                .eq("exchange", exchange).eq("ticker", ticker).execute().data)
        if not rows:
            return None
        r = rows[0]
        return (date.fromisoformat(r["first_day"]), date.fromisoformat(r["last_day"]),
                datetime.fromisoformat(r["synced_at"]))

    def get_bars(self, exchange: str, ticker: str, start: date, end: date) -> list[PriceBar]:
        bars, offset = [], 0
        while True:
            rows = (self._db.table("price_history").select("day, close")
                    .eq("exchange", exchange).eq("ticker", ticker)
                    .gte("day", start.isoformat()).lte("day", end.isoformat())
                    .order("day").range(offset, offset + _PAGE - 1).execute().data)
            bars += [PriceBar(date.fromisoformat(r["day"]), Decimal(str(r["close"])))
                     for r in rows]
            if len(rows) < _PAGE:
                return bars
            offset += _PAGE

    def save(self, exchange: str, ticker: str, bars: list[PriceBar], first_day: date,
             last_day: date) -> None:
        rows = [{"exchange": exchange, "ticker": ticker, "day": b.day.isoformat(),
                 "close": str(b.close)} for b in bars if b.close and b.close > 0]
        for i in range(0, len(rows), _BATCH):
            self._db.table("price_history").upsert(rows[i:i + _BATCH]).execute()
        self._db.table("price_sync").upsert({
            "exchange": exchange, "ticker": ticker, "first_day": first_day.isoformat(),
            "last_day": last_day.isoformat(), "synced_at": datetime.now().astimezone().isoformat(),
        }).execute()
