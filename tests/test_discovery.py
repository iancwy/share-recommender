"""Richer fundamentals, the shared fundamentals cache, discovery and the candidate pool."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from share_recommender.data.base import Candidate, Fundamentals
from share_recommender.data.candidates import candidate_pool, seed_list
from share_recommender.data.fundamentals_cache import CachedFundamentals
from share_recommender.data.yahoo import our_ticker, yahoo_symbol
from tests.fakes import FakeFundamentalsStore, FakeProvider

NOW = datetime(2026, 11, 9, 18, tzinfo=UTC)
CBA, NVDA = ("CBA", "ASX"), ("NVDA", "NASDAQ")


def test_fundamentals_round_trip_through_the_cache_format():
    f = Fundamentals(name="NVIDIA Corporation", quote_type="EQUITY", industry="Semiconductors",
                     forward_pe=Decimal("12.7"), debt_to_equity=Decimal("0.17"),
                     high_52w=Decimal("243.37"), summary="x" * 300)
    data = f.to_json()
    assert data["forward_pe"] == "12.7" and "beta" not in data  # empty fields left out
    assert Fundamentals.from_json(data | {"removed_field": 1}) == f


def _cached(store, provider):
    return CachedFundamentals(provider, store, now=NOW)


def test_cache_hit_needs_no_request():
    provider = FakeProvider(fundamentals={CBA: Fundamentals(name="live")})
    store = FakeFundamentalsStore({CBA: (Fundamentals(name="cached"), NOW - timedelta(days=2))})
    assert _cached(store, provider).get_many([CBA])[CBA].name == "cached"
    assert provider.fundamentals_requests == []


def test_missing_and_stale_are_fetched_and_saved():
    provider = FakeProvider(fundamentals={CBA: Fundamentals(name="new CBA"),
                                          NVDA: Fundamentals(name="new NVDA")})
    store = FakeFundamentalsStore({CBA: (Fundamentals(name="old CBA"), NOW - timedelta(days=9))})
    got = _cached(store, provider).get_many([CBA, NVDA, CBA])
    assert (got[CBA].name, got[NVDA].name) == ("new CBA", "new NVDA")
    assert sorted(provider.fundamentals_requests) == [CBA, NVDA]
    assert set(store.saved[0]) == {CBA, NVDA}


def test_a_failed_refresh_keeps_the_stale_copy():
    provider = FakeProvider()  # knows nothing
    store = FakeFundamentalsStore({CBA: (Fundamentals(name="old CBA"), NOW - timedelta(days=9))})
    got = _cached(store, provider).get_many([CBA, NVDA])
    assert got[CBA].name == "old CBA" and got[NVDA] is None


def test_a_broken_store_falls_back_to_the_provider():
    provider = FakeProvider(fundamentals={CBA: Fundamentals(name="live")})
    got = _cached(FakeFundamentalsStore(broken=True), provider).get_many([CBA])
    assert got[CBA].name == "live"


@pytest.mark.parametrize("symbol, exchange, ticker", [
    ("BHP.AX", "ASX", "BHP"), ("AZN.L", "LSE", "AZN"), ("0700.HK", "HKEX", "0700"),
    ("BRK-B", "NYSE", "BRK-B"), ("RY.TO", "TSX", "RY"), ("D05.SI", "SGX", "D05"),
])
def test_yahoo_symbols_back_to_our_tickers(symbol, exchange, ticker):
    assert our_ticker(symbol, exchange) == ticker
    assert yahoo_symbol(ticker, exchange) == symbol


def _c(ticker, exchange="ASX", cap="1e9"):
    return Candidate(ticker, exchange, f"{ticker} Ltd", "share", Decimal(10), Decimal(cap))


def test_pool_uses_discovery_plus_the_curated_list():
    provider = FakeProvider(discovered={"ASX": [_c("BHP"), _c("NEW")]})
    pool = {c.key: c for c in candidate_pool(provider, ["ASX"], limit=10)}
    assert ("NEW", "ASX") in pool and pool[("BHP", "ASX")].name == "BHP Ltd"  # discovered wins
    assert ("CSL", "ASX") in pool                    # curated shares always join
    assert ("NXT", "ASX") in pool                    # speculative ones too
    assert all(e == "ASX" for _, e in pool)


def test_pool_falls_back_to_the_seed_list():
    provider = FakeProvider()  # discovery finds nothing
    pool = {c.key for c in candidate_pool(provider, ["NZX"], limit=20)}
    seeds = {c.key for c in seed_list("NZX")[:20]}
    assert seeds and seeds <= pool
    assert seed_list("MOON") == []


def test_seed_lists_exist_for_every_exchange():
    from share_recommender.core.exchanges import EXCHANGES

    for code in EXCHANGES:
        seeds = seed_list(code)
        assert len(seeds) >= 50, code
        assert all(s.exchange == code and s.ticker for s in seeds)
