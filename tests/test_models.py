from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from share_recommender.core.models import CategoryIn, HoldingIn


def _holding(**overrides):
    data = dict(ticker=" vas ", exchange="asx", units="10", buy_price="95.50",
                buy_date=date(2026, 1, 2), fees="11", platform="CMC Markets")
    return HoldingIn(**(data | overrides))


def test_holding_normalises_ticker_and_exchange():
    h = _holding()
    assert h.ticker == "VAS"
    assert h.exchange == "ASX"
    assert h.currency == "AUD"


def test_cost_base_includes_fees():
    assert _holding().cost_base == Decimal("966.00")


@pytest.mark.parametrize("field, value", [
    ("units", "0"), ("buy_price", "-1"), ("fees", "-0.01"),
    ("exchange", "MOON"), ("ticker", "   "),
    ("buy_date", date.today() + timedelta(days=1)),
])
def test_holding_rejects_bad_input(field, value):
    with pytest.raises(ValidationError):
        _holding(**{field: value})


def test_blank_platform_becomes_none():
    assert _holding(platform="  ").platform is None


def test_category_name_is_trimmed_and_required():
    assert CategoryIn(name="  Kids fund ").name == "Kids fund"
    with pytest.raises(ValidationError):
        CategoryIn(name="   ")
    with pytest.raises(ValidationError):
        CategoryIn(name="x", target_amount=-5)
