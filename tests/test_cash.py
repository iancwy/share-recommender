"""Cash in savings accounts: validation, valuation, gaps, buy plan and projections."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from share_recommender.core.models import CashAccount, CashAccountIn, Category, Holding
from share_recommender.core.performance import (
    Dimension,
    breakdown,
    cash_values,
    current_values,
    goal_progress,
    value_series,
)
from share_recommender.core.preferences import (
    CategoryPreferences,
    DirectShares,
    InvestorPreferences,
)
from share_recommender.data.base import PriceBar
from share_recommender.portfolio_service import health_report, total
from share_recommender.recommendations import build_recommendation, provider_fns
from tests.fakes import USER_ID, FakeProvider

TODAY = date(2026, 11, 1)
GOAL = Category(id=uuid4(), user_id=USER_ID, name="Kids fund", target_amount=Decimal(50000),
                target_date=date(2036, 11, 1))


def _cash(balance="5000", rate="5", category=GOAL, institution="ING", as_of=TODAY, **kw):
    return CashAccount(id=uuid4(), user_id=USER_ID, institution=institution,
                       account_name="Savings Maximiser", balance=balance, interest_rate=rate,
                       as_of=as_of, category_id=category.id if category else None, **kw)


def _shares(units="100", category=GOAL):  # 100 units at the fake price of 100 = 10,000
    return Holding(id=uuid4(), user_id=USER_ID, ticker="VAS", exchange="ASX", units=units,
                   buy_price="100", buy_date=date(2026, 1, 5), platform="CMC Markets",
                   category_id=category.id if category else None)


def test_validation():
    a = CashAccountIn(institution="  ING ", account_name=" ", balance="5000.5",
                      interest_rate="5.25", currency="aud")
    assert (a.institution, a.account_name, a.currency, a.label) == ("ING", None, "AUD", "ING")
    for bad in [{"institution": " "}, {"balance": "-1"}, {"interest_rate": "101"},
                {"as_of": TODAY + timedelta(days=400)}, {"currency": "AU"}]:
        with pytest.raises(ValidationError):
            CashAccountIn(**({"institution": "ING", "balance": "1"} | bad))


def test_valuation_breakdowns_and_totals():
    provider = FakeProvider(price_everything=True)
    values = current_values([_shares()], provider.get_quotes([("VAS", "ASX")]), {}, "AUD")
    usd = _cash("1000", institution="Wise", currency="USD", category=None)
    cash = cash_values([_cash(), usd], {"USD": Decimal("0.5")}, "AUD")
    assert [c.value for c in cash] == [5000, 2000]

    value, invested = total(values, cash)
    assert (value, invested) == (17000, 17000)  # cash counts in both, so the gain is 0
    types = {s.label: s.value for s in breakdown(values, [GOAL], Dimension.TYPE, cash=cash)}
    assert types == {"ETFs": 10000, "Cash": 7000}
    platforms = {s.label for s in breakdown(values, [GOAL], Dimension.PLATFORM, cash=cash)}
    assert platforms == {"CMC Markets", "ING", "Wise"}
    goals = {s.label: s.value for s in breakdown(values, [GOAL], Dimension.GOAL, cash=cash)}
    assert goals == {"Kids fund": 15000, "Uncategorised": 2000}


def test_value_over_time_adds_cash_from_its_date():
    holding = _shares()
    account = _cash(as_of=date(2026, 1, 10))
    prices = {("VAS", "ASX"): [PriceBar(date(2026, 1, 5), Decimal(100))]}
    points = {p.day: p for p in value_series([holding], prices, {}, "AUD", date(2026, 1, 5),
                                             date(2026, 1, 12), cash=[account])}
    assert points[date(2026, 1, 9)].value == 10000
    assert points[date(2026, 1, 10)].value == 15000
    assert points[date(2026, 1, 10)].invested == 15000


def test_gaps_and_buy_plan_use_the_savings_account():
    inv = InvestorPreferences(direct_shares=DirectShares.ETF_ONLY)
    provider = FakeProvider(price_everything=True)
    rec = build_recommendation(GOAL, inv, CategoryPreferences(), [_shares()], "AUD",
                               *provider_fns(provider), amount=Decimal(6000), today=TODAY,
                               cash_accounts=[_cash()])
    assert rec.gaps.total_value == 15000
    cash_gap = next(g for g in rec.gaps.slices if g.key == "cash")
    assert cash_gap.current_pct == Decimal("33.3")
    assert any("5,000 AUD in savings accounts" in n for n in rec.gaps.notes)

    plan = rec.buy_plan
    assert "AAA" not in {o.ticker for o in plan.orders}  # no cash ETF
    (topup,) = plan.cash_topups
    assert topup.institution == "ING" and topup.interest_rate == 5
    # Gaps after the 6,000: international 8,085, emerging 1,470 and cash 1,300 (30% of
    # 21,000 less the 5,000 there). Money is shared in proportion, so cash gets about 720.
    assert 650 <= topup.amount < 800
    assert sum(o.total_cost for o in plan.orders) + topup.amount + plan.leftover == 6000
    assert plan.leftover < 1  # cash takes the odd dollars
    assert any("rather than a cash ETF" in n for n in plan.notes)

    # The best-paying account gets the top-up.
    rec = build_recommendation(GOAL, inv, CategoryPreferences(), [_shares()], "AUD",
                               *provider_fns(provider), amount=Decimal(6000), today=TODAY,
                               cash_accounts=[_cash("1000", rate="4.1"),
                                              _cash("1000", rate="5.5", institution="Ubank")])
    assert rec.buy_plan.cash_topups[0].institution == "Ubank"

    # No top-up when the goal already holds more cash than its target.
    rec = build_recommendation(GOAL, inv, CategoryPreferences(), [_shares()], "AUD",
                               *provider_fns(provider), amount=Decimal(6000), today=TODAY,
                               cash_accounts=[_cash("20000")])
    assert rec.buy_plan.cash_topups == []


def test_goal_projection_uses_each_accounts_rate():
    provider = FakeProvider(price_everything=True)
    values = current_values([_shares()], provider.get_quotes([("VAS", "ASX")]), {}, "AUD")
    cash = cash_values([_cash(rate="5")], {}, "AUD")
    p = goal_progress(GOAL, values, TODAY, cash)
    shares_10y = Decimal(10000) * Decimal("1.07") ** 10
    cash_10y = Decimal(5000) * Decimal("1.05") ** 10
    assert abs(p.projected - (shares_10y + cash_10y)) < 1
    assert p.value == 15000
    # 10,000 at 7% and 5,000 at 5% blend to about 6.33%.
    assert p.assumed_return == Decimal("0.0633")


def test_health_check_with_cash():
    provider = FakeProvider(price_everything=True)
    # CBA is 10,000 of 20,000 held, but 10,000 of 30,000 counting cash: 33.3%.
    cba = Holding(id=uuid4(), user_id=USER_ID, ticker="CBA", exchange="ASX", units="100",
                  buy_price="100", buy_date=date(2026, 1, 5), platform="CommSec", fees="10")
    report = health_report([], [cba, _shares(category=None)], InvestorPreferences(), {}, "AUD",
                           *provider_fns(provider), today=TODAY,
                           cash_accounts=[_cash("10000", category=None)])
    assert any(f.title.startswith("33.3% in one company (CBA)") for f in report.findings)
    ing = next(p for p in report.platforms if p.platform == "ING")
    assert ing.value == 10000 and ing.invested == 0 and ing.pct == Decimal("33.3")
    assert not any(f.area == "ING" for f in report.findings)  # no fee check on cash
