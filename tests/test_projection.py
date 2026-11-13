"""Projections: assumptions per holding, combining them, and the Monte Carlo engine."""

import math
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from share_recommender.core.models import CashAccount
from share_recommender.core.performance import ASSUMED_RETURN, CashValue
from share_recommender.core.projection import (
    CORRELATION,
    Asset,
    cash_asset,
    combine,
    holding_asset,
    month_end_closes,
    target_mix_assets,
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
