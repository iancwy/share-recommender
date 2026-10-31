from datetime import date
from decimal import Decimal
from uuid import uuid4

from share_recommender.core.models import Category, Holding
from share_recommender.core.performance import (
    Dimension,
    breakdown,
    current_values,
    goal_progress,
    sample_days,
    value_series,
)
from share_recommender.data.base import PriceBar
from tests.fakes import USER_ID, FakeProvider

GOAL = Category(id=uuid4(), user_id=USER_ID, name="Kids fund", target_amount=Decimal(100000),
                target_date=date(2032, 1, 1))


def _h(ticker, exchange, units, price, day, fees="0", platform=None, category=None):
    return Holding(id=uuid4(), user_id=USER_ID, ticker=ticker, exchange=exchange, units=units,
                   buy_price=price, buy_date=day, fees=fees, platform=platform,
                   category_id=category.id if category else None)


def test_value_series_follows_prices_purchases_and_fx():
    vas = _h("VAS", "ASX", "10", "100", date(2026, 1, 1), fees="10")
    voo = _h("VOO", "NYSE", "1", "500", date(2026, 1, 3))
    prices = {("VAS", "ASX"): [PriceBar(date(2026, 1, 2), Decimal(110))],
              ("VOO", "NYSE"): [PriceBar(date(2026, 1, 3), Decimal(520))]}
    fx = {"USD": [PriceBar(date(2026, 1, 1), Decimal("0.5")),
                  PriceBar(date(2026, 1, 4), Decimal("0.4"))]}
    points = value_series([vas, voo], prices, fx, "AUD", date(2026, 1, 1), date(2026, 1, 4))
    by_day = {p.day: p for p in points}

    assert by_day[date(2026, 1, 1)].value == 1000  # no price yet: buy price
    assert by_day[date(2026, 1, 1)].invested == 1010
    assert by_day[date(2026, 1, 2)].value == 1100
    assert by_day[date(2026, 1, 3)].value == 1100 + 1040  # 520 USD at 0.5
    assert by_day[date(2026, 1, 3)].invested == 1010 + 1000
    assert by_day[date(2026, 1, 4)].value == 1100 + 1300  # same price, weaker AUD
    assert by_day[date(2026, 1, 4)].invested == 2010      # invested stays at the buy-day rate


def test_sample_days_weekly_for_long_ranges():
    assert len(sample_days(date(2026, 1, 1), date(2026, 1, 31))) == 31
    long = sample_days(date(2020, 1, 1), date(2026, 1, 1))
    assert long[-1] == date(2026, 1, 1) and (long[1] - long[0]).days == 7


def test_breakdowns_and_other_bucket():
    holdings = [_h("VAS", "ASX", "10", "100", date(2026, 1, 1), platform="CMC Markets",
                   category=GOAL),
                _h("CBA", "ASX", "5", "100", date(2026, 1, 1), platform="CommSec"),
                _h("AAA", "ASX", "5", "100", date(2026, 1, 1))]
    quotes = FakeProvider(price_everything=True).get_quotes(
        [(h.ticker, h.exchange) for h in holdings])
    values = current_values(holdings, quotes, {}, "AUD")

    goal = {s.label: s.pct for s in breakdown(values, [GOAL], Dimension.GOAL)}
    assert goal == {"Kids fund": 50, "Uncategorised": 50}
    platform = [s.label for s in breakdown(values, [GOAL], Dimension.PLATFORM)]
    assert platform == ["CMC Markets", "CommSec", "No platform set"]
    classes = {s.label for s in breakdown(values, [GOAL], Dimension.ASSET_CLASS)}
    assert classes == {"Australian shares", "Cash"}
    folded = breakdown(values, [GOAL], Dimension.HOLDING, top=2)
    assert [s.label for s in folded] == ["VAS", "Other"] and folded[1].value == 1000


def test_goal_progress_on_track_and_behind():
    today = date(2026, 1, 1)
    values = current_values([_h("VAS", "ASX", "800", "100", today, category=GOAL)],
                            FakeProvider(price_everything=True).get_quotes([("VAS", "ASX")]),
                            {}, "AUD")
    p = goal_progress(GOAL, values, today)  # 80,000 in shares, 6 years at 7%
    assert p.assumed_return == Decimal("0.07")
    assert p.on_track and p.monthly_needed == 0 and p.projected > 100000

    small = current_values([_h("AAA", "ASX", "100", "100", today, category=GOAL)],
                           FakeProvider(price_everything=True).get_quotes([("AAA", "ASX")]),
                           {}, "AUD")
    p = goal_progress(GOAL, small, today)  # 10,000 in cash at 3.5%
    assert p.assumed_return == Decimal("0.035")
    assert p.on_track is False and 900 < p.monthly_needed < 1200
    assert p.pct_of_target == 10

    no_date = Category(id=uuid4(), user_id=USER_ID, name="General")
    assert goal_progress(no_date, small, today).on_track is None
