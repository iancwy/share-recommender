"""The projection service for each scope, with fake prices, history and fundamentals."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from share_recommender.core.models import (
    CashAccount,
    Category,
    Contribution,
    Holding,
    RiskLevel,
)
from share_recommender.data.base import Fundamentals, PriceBar
from share_recommender.projection_service import Scope, default_years, run_projection
from share_recommender.recommendations import provider_fns
from tests.fakes import USER_ID, FakeProvider

D = Decimal
TODAY = date(2026, 11, 14)


class History:
    """Ten-plus years of month-end closes doubling over 10 years, and a record of calls."""

    def __init__(self):
        self.calls = []

    def __call__(self, symbols, currencies, base, start, end):
        self.calls.append((symbols, start))
        bars = []
        for i in range(125):
            n = 2014 * 12 + 9 + i
            bars.append(PriceBar(date(n // 12, n % 12 + 1, 28), D(str(100 * 2 ** (i / 120)))))
        return {s: bars for s in symbols}, {}


@pytest.fixture
def world():
    kids = Category(id=uuid4(), user_id=USER_ID, name="Kids school fund",
                    risk_level=RiskLevel.MEDIUM, target_amount=D(80000),
                    target_date=date(2031, 1, 31))
    empty = Category(id=uuid4(), user_id=USER_ID, name="Boat", risk_level=RiskLevel.HIGH)

    def lot(ticker, units, cat=kids, exchange="ASX"):
        return Holding(id=uuid4(), user_id=USER_ID, ticker=ticker, exchange=exchange,
                       units=D(units), buy_price=D(90), buy_date=date(2024, 1, 2),
                       category_id=cat.id)
    saver = CashAccount(id=uuid4(), user_id=USER_ID, institution="ING", balance=D(5000),
                        interest_rate=D(5), as_of=TODAY, category_id=kids.id)
    return {"kids": kids, "empty": empty, "saver": saver,
            "holdings": [lot("VAS", 100), lot("VAS", 50), lot("CBA", 10)],
            "provider": FakeProvider(price_everything=True,
                                     fundamentals={("VAS", "ASX"): Fundamentals(
                                         dividend_yield=D("0.035"))})}


def _run(world, contributions=(), history=None, fundamentals_log=None, **kw):
    quotes_fn, fx_fn, fundamentals_fn = provider_fns(world["provider"])

    def fundamentals(symbols):
        if fundamentals_log is not None:
            fundamentals_log.append(symbols)
        return fundamentals_fn(symbols)
    args = {"scope": Scope.PORTFOLIO, "holdings": world["holdings"],
            "cash_accounts": [world["saver"]], "categories": [world["kids"], world["empty"]],
            "contributions": list(contributions), "goal_prefs": {}, "inv": None, "base": "AUD",
            "quotes_fn": quotes_fn, "fx_fn": fx_fn, "history_fn": history or History(),
            "fundamentals_fn": fundamentals, "years": 5, "basis": "average", "real": False,
            "today": TODAY} | kw
    return run_projection(**args)


def _c(world, amount=500, frequency="fortnightly", **kw):
    return Contribution(id=uuid4(), user_id=USER_ID, amount=D(amount), frequency=frequency,
                        start_date=TODAY, **kw)


def test_whole_portfolio_groups_lots(world):
    p = _run(world)
    keys = [a.key for a in p.assets]
    assert keys == ["CBA:ASX", "VAS:ASX", f"cash:{world['saver'].id}"]
    vas = next(a for a in p.assets if a.key == "VAS:ASX")
    assert vas.value == D(15000)                    # two lots of VAS, at the fake 100
    assert p.start_value == D(15000 + 1000 + 5000)
    assert p.chance_of_target is None


def test_a_goal_gets_its_target_and_chance(world):
    p = _run(world, scope=Scope.GOAL, category_id=world["kids"].id)
    assert p.chance_of_target is not None and D(5) <= p.chance_of_target <= D(95)
    assert default_years(world["kids"], TODAY) == 5          # Jan 2031, rounded up
    assert default_years(world["empty"], TODAY) == 5         # no target date


def test_goal_contributions_and_whole_portfolio_ones_left_out(world):
    into_goal = _c(world, category_id=world["kids"].id, label="Top-up")
    into_saver = _c(world, 200, "monthly", cash_account_id=world["saver"].id)
    whole = _c(world, 100, "monthly")
    p = _run(world, [into_goal, into_saver, whole], scope=Scope.GOAL,
             category_id=world["kids"].id)
    # 5 years: 500 a fortnight (13,000 a year) and 200 a month (2,400 a year).
    assert p.contributed == D(5 * (13000 + 2400))
    assert any("whole portfolio aren't included" in n for n in p.notes)
    everything = _run(world, [into_goal, into_saver, whole])
    assert everything.contributed == D(5 * (13000 + 2400 + 1200))


def test_an_empty_goal_with_contributions_uses_its_target_mix(world):
    p = _run(world, [_c(world, category_id=world["empty"].id)], scope=Scope.GOAL,
             category_id=world["empty"].id)
    assert p.start_value == 0 and p.contributed == D(5 * 13000)
    assert "Nothing in this goal yet" in p.assets[0].source and "90% shares" in p.assets[0].source
    assert p.final.worst < p.final.base < p.final.best


def test_one_share_held_or_typed(world):
    held = _run(world, scope=Scope.SHARE, ticker="VAS", exchange="ASX")
    assert held.start_value == D(15000) and held.contributed == 0
    from share_recommender.core.models import Frequency
    from share_recommender.core.projection import ContributionFlow

    typed = _run(world, scope=Scope.SHARE, ticker="NVDA", exchange="NASDAQ", amount=D(1000),
                 extra=ContributionFlow(D(100), Frequency.MONTHLY, TODAY))
    assert typed.start_value == D(1000) and typed.contributed == D(6000)


def test_an_unpriced_holding_counts_at_cost(world):
    world["provider"] = FakeProvider(prices={("VAS", "ASX"): "100"})  # CBA has no price
    p = _run(world)
    cba = next(a for a in p.assets if a.key == "CBA:ASX")
    assert cba.value == D(900) and any("CBA has no current price" in n for n in p.notes)


def test_past_performance_fetches_ten_years_and_dividends(world):
    history, fundamentals = History(), []
    p = _run(world, history=history, fundamentals_log=fundamentals, basis="past")
    (_, start), = history.calls
    assert start <= TODAY - timedelta(days=3650) and fundamentals
    vas = next(a for a in p.assets if a.key == "VAS:ASX")
    assert "3.5% dividends" in vas.source

    history, fundamentals = History(), []
    _run(world, history=history, fundamentals_log=fundamentals)
    (_, start), = history.calls
    assert TODAY - timedelta(days=4 * 365) < start < TODAY - timedelta(days=3 * 365)
    assert fundamentals == []
