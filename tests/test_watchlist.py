"""The watchlist: validation, adding (look-up and refusals), figures, and the repository."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from share_recommender.core.models import WatchlistItem, WatchlistItemIn
from share_recommender.core.universe import Kind
from share_recommender.core.watchlist import short_money, watch_rows
from share_recommender.data.base import Fundamentals, PriceBar
from share_recommender.recommendations import provider_fns
from share_recommender.watchlist_service import BONDS_REFUSED, prepare_item
from tests.fakes import USER_ID, FakeProvider, FakeRepository

TODAY = date(2026, 11, 9)


def test_validation():
    w = WatchlistItemIn(ticker=" cba ", exchange="asx", alert_below="100", alert_above="120",
                        notes=" ")
    assert (w.ticker, w.exchange, w.notes, w.key, w.currency) == \
        ("CBA", "ASX", None, ("CBA", "ASX"), "AUD")
    for bad in [{"alert_below": "130", "alert_above": "120"},
                {"alert_below": "120", "alert_above": "120"}, {"exchange": "MOON"},
                {"ticker": " "}, {"alert_below": "0"}, {"source": "robot"}]:
        with pytest.raises(ValidationError):
            WatchlistItemIn(**({"ticker": "CBA", "exchange": "ASX"} | bad))


def _prepare(ticker, exchange, provider):
    quotes_fn, _, fundamentals_fn = provider_fns(provider)
    return prepare_item(WatchlistItemIn(ticker=ticker, exchange=exchange), quotes_fn,
                        fundamentals_fn)


def test_adding_looks_it_up():
    provider = FakeProvider(prices={("CBA", "ASX"): "160.50", ("XRO", "ASX"): "150",
                                    ("TPW", "ASX"): "5.28", ("VAF", "ASX"): "45",
                                    ("BND", "NASDAQ"): "72"},
                            fundamentals={
                                ("TPW", "ASX"): Fundamentals(name="Temple & Webster Group Ltd",
                                                             quote_type="EQUITY"),
                                ("VAF", "ASX"): Fundamentals(
                                    name="Vanguard Australian Fixed Interest Index ETF",
                                    quote_type="ETF"),
                                ("BND", "NASDAQ"): Fundamentals(
                                    name="Vanguard Total Bond Market ETF", quote_type="ETF",
                                    fund_category="Intermediate Core Bond")})
    cba = _prepare("cba", "ASX", provider)  # on the curated list
    assert (cba.name, cba.kind, cba.added_price) == \
        ("Commonwealth Bank of Australia", Kind.SHARE, Decimal("160.50"))
    tpw = _prepare("TPW", "ASX", provider)  # not on the list: from the provider
    assert (tpw.name, tpw.kind) == ("Temple & Webster Group Ltd", Kind.SHARE)
    for ticker, exchange in [("VAF", "ASX"), ("BND", "NASDAQ")]:
        with pytest.raises(ValueError, match=BONDS_REFUSED):
            _prepare(ticker, exchange, provider)
    with pytest.raises(ValueError, match="Couldn't find VAS on NYSE"):
        _prepare("VAS", "NYSE", provider)


def test_a_company_with_credit_in_its_name_is_not_a_bond_fund():
    provider = FakeProvider(prices={("CCP", "ASX"): "10"}, fundamentals={
        ("CCP", "ASX"): Fundamentals(name="Credit Corp Group Limited", quote_type="EQUITY")})
    assert _prepare("CCP", "ASX", provider).kind is Kind.SHARE


def test_duplicates_are_refused_and_goal_deletion_keeps_items():
    repo = FakeRepository()
    (goal,) = repo.categories.values()
    repo.add_to_watchlist(WatchlistItemIn(ticker="CBA", exchange="ASX", category_id=goal.id))
    with pytest.raises(ValueError, match="Already on your watchlist"):
        repo.add_to_watchlist(WatchlistItemIn(ticker="cba", exchange="asx"))
    repo.add_to_watchlist(WatchlistItemIn(ticker="CBA", exchange="NYSE"))  # a different listing
    repo.delete_category(goal.id)
    assert all(w.category_id is None for w in repo.list_watchlist())


def _item(ticker="CBA", exchange="ASX", added="100", below=None, above=None):
    return WatchlistItem(id=uuid4(), user_id=USER_ID, ticker=ticker, exchange=exchange,
                         added_price=added, alert_below=below, alert_above=above)


def test_watch_rows():
    cba, nvda, ghost = _item(), _item("NVDA", "NASDAQ", added="200", above="250"), _item("ZZZ")
    year = [PriceBar(TODAY - timedelta(days=400), Decimal(80)),
            PriceBar(TODAY - timedelta(days=366), Decimal(90)),
            PriceBar(TODAY - timedelta(days=200), Decimal(70)),
            PriceBar(TODAY - timedelta(days=100), Decimal(130)),
            PriceBar(TODAY - timedelta(days=1), Decimal(110)),
            PriceBar(TODAY, Decimal(115.5))]
    quotes = FakeProvider(prices={("CBA", "ASX"): "115.5", ("NVDA", "NASDAQ"): "210"}).get_quotes(
        [cba.key, nvda.key, ghost.key])
    funds = {cba.key: Fundamentals(pe_ratio=Decimal("25.1"), dividend_yield=Decimal("0.031"),
                                   market_cap=Decimal("279.6e9")),
             nvda.key: Fundamentals(market_cap=Decimal("4.9e12"))}
    rows = watch_rows([cba, nvda, ghost], quotes, funds, {cba.key: year}, "AUD",
                      {"USD": Decimal("0.5")}, held={nvda.key}, today=TODAY)
    r = {x.item.ticker: x for x in rows}
    assert r["CBA"].price == Decimal("115.5") and r["CBA"].day_pct == Decimal("5.0")
    assert r["CBA"].since_added_pct == Decimal("15.5")
    assert r["CBA"].year_pct == Decimal("28.3")        # from 90, a year ago
    assert r["CBA"].range_pos == 76                    # between 70 and 130
    assert r["CBA"].dividend_yield_pct == Decimal("3.10")
    assert r["CBA"].market_cap == "A$279.6bn"
    assert r["NVDA"].market_cap == "A$9.8tn" and r["NVDA"].held  # 4.9tn USD at 0.5
    assert r["NVDA"].year_pct is None and r["NVDA"].alerts == "≥ 250.00"
    assert r["ZZZ"].price is None and r["ZZZ"].market_cap is None  # missing data: blanks


def test_short_money():
    assert short_money(Decimal("12.34e9"), "AUD") == "A$12.3bn"
    assert short_money(Decimal("450e6"), "USD") == "US$450.0m"
    assert short_money(Decimal("5000"), "EUR") == "EUR 5,000"
    assert short_money(None, "AUD") is None
