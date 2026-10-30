from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from share_recommender.data.base import PriceBar
from share_recommender.data.history import CachedHistory
from tests.fakes import FakePriceStore, FakeProvider

TODAY = date(2026, 10, 30)


class CountingProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.calls = []

    def get_history(self, ticker, exchange, start, end):
        self.calls.append((ticker, start, end))
        return [PriceBar(start + timedelta(days=i), Decimal(100 + i))
                for i in range((end - start).days + 1)]

    def get_fx_history(self, base, quote, start, end):
        self.calls.append((f"{base}{quote}", start, end))
        return [PriceBar(start, Decimal("0.65"))]


def test_fetches_once_then_serves_from_cache():
    provider, store = CountingProvider(), FakePriceStore()
    history = CachedHistory(provider, store, today=TODAY)
    first = history.history("VAS", "ASX", date(2026, 10, 1), TODAY)
    again = history.history("VAS", "ASX", date(2026, 10, 10), TODAY)
    assert len(first) == 30 and again == first[9:]
    assert provider.calls == [("VAS", date(2026, 10, 1), TODAY)]  # second call: no fetch


def test_extends_backwards_and_refreshes_the_last_day_on_a_new_day():
    provider, store = CountingProvider(), FakePriceStore()
    store.save("ASX", "VAS", [PriceBar(date(2026, 10, 20), Decimal(1))],
               date(2026, 10, 20), date(2026, 10, 27))
    store.sync[("ASX", "VAS")] = (date(2026, 10, 20), date(2026, 10, 27),
                                  datetime(2026, 10, 27, tzinfo=UTC))
    bars = CachedHistory(provider, store, today=TODAY).history(
        "VAS", "ASX", date(2026, 10, 15), date(2026, 11, 5))  # end is capped at today
    assert provider.calls == [("VAS", date(2026, 10, 15), date(2026, 10, 19)),
                              ("VAS", date(2026, 10, 27), TODAY)]
    assert store.sync[("ASX", "VAS")][:2] == (date(2026, 10, 15), TODAY)
    assert bars[0].day == date(2026, 10, 15) and bars[-1].day == TODAY


def test_fx_history_is_cached_under_fx():
    provider, store = CountingProvider(), FakePriceStore()
    CachedHistory(provider, store, today=TODAY).fx_history("AUD", "USD", date(2026, 1, 1), TODAY)
    assert ("FX", "AUDUSD") in store.sync


def test_broken_store_falls_back_to_provider():
    class Broken(FakePriceStore):
        def get_sync(self, exchange, ticker):
            raise RuntimeError("relation price_sync does not exist")

    provider = CountingProvider()
    bars = CachedHistory(provider, Broken(), today=TODAY).history(
        "VAS", "ASX", date(2026, 10, 29), TODAY)
    assert len(bars) == 2 and provider.calls
