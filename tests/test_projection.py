"""Projections: assumptions per holding, combining them, and the Monte Carlo engine."""

import math
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from share_recommender.core.models import CashAccount, Contribution, ContributionIn
from share_recommender.core.performance import ASSUMED_RETURN, CashValue
from share_recommender.core.projection import (
    CORRELATION,
    Asset,
    ContributionFlow,
    Frequency,
    cash_asset,
    combine,
    contribution_goal,
    holding_asset,
    month_end_closes,
    monthly_equivalent,
    paid_in_month,
    project,
    target_mix_assets,
    to_flows,
    volatility,
)
from share_recommender.core.recommender import BASE_GROWTH
from share_recommender.data.base import PriceBar
from tests.fakes import USER_ID

D = Decimal


def _monthly(closes, start=date(2016, 1, 28)):
    """One bar a month (plus a mid-month one that must be ignored), oldest first."""
    bars = []
    for i, c in enumerate(closes):
        y, m = start.year + (start.month - 1 + i) // 12, (start.month - 1 + i) % 12 + 1
        bars += [PriceBar(date(y, m, 10), D(1)), PriceBar(date(y, m, 28), D(str(c)))]
    return bars


def _zigzag(a, months):
    """Monthly log returns alternating +a, -a: a known volatility."""
    return [100 * math.exp(a if i % 2 else 0) for i in range(months + 1)]


def test_month_end_closes_keeps_the_last_close_each_month():
    bars = _monthly([100, 110])
    assert [b.close for b in month_end_closes(bars)] == [D(100), D(110)]


def test_monthly_volatility_has_the_known_answer():
    a = 0.05
    vol = volatility(_monthly(_zigzag(a, 36)))
    assert float(vol) == pytest.approx(a * math.sqrt(36 / 35) * math.sqrt(12), abs=1e-4)
    assert volatility(_monthly(_zigzag(a, 11))) is None  # under 12 monthly returns


@pytest.mark.parametrize("ticker, ret, vol, words", [
    ("VAS", "0.07", "0.15", "Australian shares"),
    ("VGS", "0.07", "0.15", "international shares"),
    ("VGE", "0.07", "0.20", "emerging markets"),
    ("AAA", "0.035", "0.01", "cash"),
])
def test_long_run_averages_per_class(ticker, ret, vol, words):
    a = holding_asset(ticker, "ASX", D(1000), [])
    assert (a.expected_return, a.volatility) == (D(ret), D(vol))
    assert words in a.source and not a.is_cash
    assert a.is_cash_fund is (ticker == "AAA")


def test_a_multi_class_fund_takes_the_weighted_average():
    a = holding_asset("VDHG", "ASX", D(1000), [])
    assert ASSUMED_RETURN["cash"] < a.expected_return < ASSUMED_RETURN["growth"]
    assert D("0.01") < a.volatility < D("0.20") and "its mix" in a.source


def test_individual_shares_use_their_own_volatility_within_limits():
    new = holding_asset("CBA", "ASX", D(1000), [])
    assert new.volatility == D("0.30") and "less than a year" in new.source
    calm = holding_asset("CBA", "ASX", D(1000), _monthly(_zigzag(0.01, 36)))
    assert calm.volatility == D("0.15") and "kept to 15.0%" in calm.source
    wild = holding_asset("ZIP", "ASX", D(1000), _monthly(_zigzag(0.3, 36)))  # not curated
    assert wild.volatility == D("0.60") and wild.expected_return == D("0.07")
    normal = holding_asset("CBA", "ASX", D(1000), _monthly(_zigzag(0.07, 36)))
    assert D("0.15") < normal.volatility < D("0.60") and "kept" not in normal.source


def test_speculative_shares_and_theme_etfs():
    assert holding_asset("AMD", "NASDAQ", D(1000), []).volatility == D("0.40")
    assert holding_asset("SEMI", "ASX", D(1000), _monthly(_zigzag(0.01, 36))).volatility \
        == D("0.20")
    assert holding_asset("SEMI", "ASX", D(1000), _monthly(_zigzag(0.5, 36))).volatility \
        == D("0.80")


def test_a_savings_account_grows_at_its_rate():
    acct = CashAccount(id=uuid4(), user_id=USER_ID, institution="ING", account_name="Saver",
                       balance=D(5000), interest_rate=D("5.5"), as_of=date(2026, 11, 1))
    a = cash_asset(CashValue(acct, D(5000)))
    assert (a.label, a.expected_return, a.volatility, a.is_cash) == (
        "ING Saver", D("0.055"), D(0), True)


# Past performance -------------------------------------------------------------------------

def _growing(total_growth, years):
    months = years * 12
    return _monthly([100 * total_growth ** (i / months) for i in range(months + 1)])


def test_past_performance_adds_dividends():
    a = holding_asset("VHY", "ASX", D(1000), _growing(2, 10), "past", D("0.04"))
    assert float(a.expected_return) == pytest.approx(2 ** 0.1 - 1 + 0.04, abs=1e-4)
    assert "Past 10 years" in a.source and "4.0% dividends" in a.source


def test_past_performance_is_capped_and_floored():
    hot = holding_asset("CBA", "ASX", D(1000), _growing(4, 5), "past")
    assert hot.expected_return == D("0.12")
    assert "Past 5 years: 32.0% a year, capped at 12.0% for this projection" in hot.source
    cold = holding_asset("CBA", "ASX", D(1000), _growing(0.5, 5), "past")
    assert cold.expected_return == D(0) and "raised to 0.0%" in cold.source


def test_past_performance_needs_three_years():
    a = holding_asset("VAS", "ASX", D(1000), _growing(1.2, 2), "past")
    assert a.expected_return == D("0.07") and a.source.startswith("Less than 3 years")


# Combining ------------------------------------------------------------------------------

def _a(value, ret="0.07", vol="0.15", **kw):
    return Asset("x", "x", D(value), D(ret), D(vol), False, "", **kw)


def test_combine_one_asset_gives_its_own():
    assert combine([_a(100)]) == (D("0.07"), D("0.15"))


def test_combine_two_correlated_assets_lowers_volatility():
    mu, vol = combine([_a(100), _a(100)])
    expected = math.sqrt(0.5 * (1 + float(CORRELATION))) * 0.15
    assert mu == D("0.07") and float(vol) == pytest.approx(expected, abs=1e-4)
    assert vol < D("0.15")


def test_combine_ignores_savings_and_cash_funds_are_uncorrelated():
    savings = Asset("cash:1", "ING", D(1000), D("0.05"), D(0), True, "")
    mu, vol = combine([_a(100), _a(100, "0.035", "0.01", is_cash_fund=True), savings])
    assert mu == D("0.0525")
    assert float(vol) == pytest.approx(math.sqrt(0.25 * 0.15 ** 2 + 0.25 * 0.01 ** 2), abs=1e-4)
    assert combine([savings]) == (D(0), D(0))


@pytest.mark.parametrize("risk", list(BASE_GROWTH))
def test_an_empty_goal_uses_its_target_mix(risk):
    growth = BASE_GROWTH[risk]
    assets = target_mix_assets(growth, "Nothing in this goal yet")
    assert all(a.value == 0 for a in assets)
    assert f"{growth}% shares, {100 - growth}% cash" in assets[0].source
    mu, vol = combine(assets)
    g = D(growth) / 100
    assert mu == (g * D("0.07") + (1 - g) * D("0.035")).quantize(D("0.0001"))
    assert vol > 0


# The engine --------------------------------------------------------------------------------

TODAY = date(2026, 11, 14)


def _savings(value, rate):
    return Asset("cash:1", "ING", D(value), D(rate), D(0), True, "")


def test_no_volatility_is_exact_compound_growth():
    p = project([_a(10000, vol="0")], [], today=TODAY, years=5)
    f = p.final
    assert f.worst == f.base == f.best == D(round(10000 * 1.07 ** 5))
    assert p.points[0].month == date(2026, 11, 1) and f.month == date(2031, 11, 1)
    assert len(p.points) == 61 and p.growth == f.base - D(10000)


def test_the_median_grows_at_the_expected_return():
    p = project([_a(10000)], [], today=TODAY, years=10)
    assert float(p.final.base) == pytest.approx(10000 * 1.07 ** 10, rel=0.01)
    assert all(pt.worst < pt.base < pt.best for pt in p.points[1:])
    assert p.points[0].worst == p.points[0].base == p.points[0].best == D(10000)


def test_same_inputs_same_result():
    args = ([_a(10000), _a(5000, vol="0.3")], [ContributionFlow(D(100), Frequency.MONTHLY,
                                                                TODAY)])
    assert project(*args, today=TODAY, years=7) == project(*args, today=TODAY, years=7)


@pytest.mark.parametrize("frequency, amount, total", [
    (Frequency.WEEKLY, 100, 5200), (Frequency.FORTNIGHTLY, 500, 13000),
    (Frequency.MONTHLY, 200, 2400), (Frequency.QUARTERLY, 1000, 4000),
    (Frequency.YEARLY, 3000, 3000)])
def test_each_frequency_pays_the_right_total_in_a_year(frequency, amount, total):
    c = ContributionFlow(D(amount), frequency, date(2026, 12, 1))
    paid = sum(paid_in_month(c, date(2026 + (11 + k) // 12, (11 + k) % 12 + 1, 1))
               for k in range(12))
    assert paid == pytest.approx(D(total))
    p = project([], [c], today=TODAY, years=1)
    assert p.contributed == D(total) and p.final.put_in == D(total)


def test_start_and_end_dates():
    past = ContributionFlow(D(100), Frequency.MONTHLY, date(2020, 1, 1))
    assert project([], [past], today=TODAY, years=1).contributed == D(1200)  # from today only
    ends = ContributionFlow(D(100), Frequency.MONTHLY, date(2026, 12, 1), date(2027, 3, 31),
                            label="Holiday top-up")
    p = project([], [ends], today=TODAY, years=1)
    assert p.contributed == D(400) and "Holiday top-up ends in Mar 2027." in p.notes
    later = ContributionFlow(D(100), Frequency.MONTHLY, date(2030, 1, 1))
    p = project([], [later], today=TODAY, years=1)
    assert p.contributed == 0 and "after this projection ends" in p.notes[0]


def test_savings_and_contributions_into_savings_grow_at_their_rate():
    rate = 0.05
    p = project([_savings(1000, rate)], [ContributionFlow(D(100), Frequency.MONTHLY,
                                                          date(2026, 12, 1),
                                                          into_cash_rate=D(rate))],
                today=TODAY, years=1)
    monthly = (1 + rate) ** (1 / 12)
    expected = 1000 * (1 + rate) + sum(100 * monthly ** k for k in range(12))
    f = p.final
    assert f.worst == f.base == f.best == D(round(expected))  # a cash-only scope: no band


def test_contributions_into_the_mix_follow_the_invested_paths():
    p = project([_a(10000)], [ContributionFlow(D(500), Frequency.MONTHLY, TODAY)],
                today=TODAY, years=5)
    assert p.final.put_in == D(10000 + 500 * 60)
    assert p.final.worst < p.final.base < p.final.best and p.growth > 0


def test_todays_dollars():
    nominal = project([_a(10000, vol="0")], [], today=TODAY, years=10)
    real = project([_a(10000, vol="0")], [], today=TODAY, years=10, real=True)
    assert real.final.base == D(round(10000 * 1.07 ** 10 / 1.025 ** 10))
    assert real.final.base < nominal.final.base and real.final.put_in < D(10000)


@pytest.mark.parametrize("target, chance", [(5000, 95), (None, None), (60000, 5)])
def test_chance_of_reaching_a_target(target, chance):
    p = project([_a(10000)], [], today=TODAY, years=10)
    median = p.final.base
    t = D(target) if target else median
    got = project([_a(10000)], [], today=TODAY, years=10, target=t,
                  target_date=date(2036, 11, 30)).chance_of_target
    if chance:
        assert got == D(chance)
    else:
        assert D(45) <= got <= D(55)  # about half the paths reach the median


def test_a_target_outside_the_horizon_has_no_chance():
    p = project([_a(10000)], [], today=TODAY, years=5, target=D(1),
                target_date=date(2040, 1, 1))
    assert p.chance_of_target is None


def test_forty_years_runs_in_under_a_second():
    import time

    started = time.perf_counter()
    project([_a(10000), _a(5000, vol="0.3"), _savings(2000, 0.05)],
            [ContributionFlow(D(500), Frequency.FORTNIGHTLY, TODAY),
             ContributionFlow(D(100), Frequency.MONTHLY, TODAY, into_cash_rate=D("0.05"))],
            today=TODAY, years=40)
    assert time.perf_counter() - started < 1


# Saved contributions -----------------------------------------------------------------------


def _c(**kw):
    return Contribution(id=uuid4(), user_id=USER_ID, **({"amount": D(100)} | kw))


def test_contribution_validation():
    with pytest.raises(ValidationError):
        ContributionIn(amount=D(0))
    with pytest.raises(ValidationError, match="end date"):
        ContributionIn(amount=D(10), start_date=date(2027, 1, 1), end_date=date(2026, 1, 1))
    c = ContributionIn(amount=D(10), label="  ", notes="")
    assert c.label is None and c.notes is None and c.frequency is Frequency.MONTHLY


@pytest.mark.parametrize("frequency, monthly", [
    ("weekly", 2167), ("fortnightly", 1083), ("monthly", 500), ("quarterly", 167),
    ("yearly", 42)])
def test_monthly_equivalent(frequency, monthly):
    assert monthly_equivalent(_c(amount=D(500), frequency=frequency)) == D(monthly)


def test_to_flows():
    account = CashAccount(id=uuid4(), user_id=USER_ID, institution="ING", balance=D(0),
                          interest_rate=D("5.5"), as_of=TODAY, category_id=uuid4())
    goal = uuid4()
    rows = [_c(label="paused", active=False),
            _c(label="ended", start_date=date(2025, 1, 1), end_date=date(2026, 1, 1)),
            _c(label="saver", cash_account_id=account.id, category_id=goal),
            _c(label="plain", frequency="fortnightly", category_id=goal)]
    flows = to_flows(rows, {account.id: account}, TODAY)
    assert [f.label for f in flows] == ["saver", "plain"]
    assert flows[0].into_cash_rate == D("0.055") and flows[1].into_cash_rate is None
    # The account's goal wins over the saved one.
    cash_goals = {account.id: account.category_id}
    assert contribution_goal(rows[2], cash_goals) == account.category_id
    assert contribution_goal(rows[3], cash_goals) == goal
    assert contribution_goal(_c(), cash_goals) is None


def test_fake_repository_contributions():
    from tests.fakes import FakeRepository

    repo = FakeRepository()
    goal = next(iter(repo.categories))
    c = repo.add_contribution(ContributionIn(amount=D(500), frequency="fortnightly",
                                             category_id=goal))
    repo.update_contribution(c.id, ContributionIn(amount=D(600), category_id=goal))
    assert repo.list_contributions()[0].amount == D(600)
    repo.delete_category(goal)  # becomes a whole-portfolio contribution
    assert repo.list_contributions()[0].category_id is None
    repo.delete_contribution(c.id)
    assert repo.list_contributions() == []
