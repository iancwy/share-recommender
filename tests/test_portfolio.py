from datetime import date
from decimal import Decimal
from uuid import uuid4

from share_recommender.core.models import Category, Holding
from share_recommender.core.portfolio import UNCATEGORISED, totals_by_currency, value_holdings
from tests.fakes import USER_ID, FakeProvider


def _h(ticker, exchange, units, price, fees="0", category_id=None):
    return Holding(id=uuid4(), user_id=USER_ID, ticker=ticker, exchange=exchange, units=units,
                   buy_price=price, buy_date=date(2026, 1, 1), fees=fees, category_id=category_id)


def test_value_holdings_with_and_without_prices():
    cat = Category(id=uuid4(), user_id=USER_ID, name="Kids fund")
    holdings = [_h("VAS", "ASX", "10", "90", fees="10", category_id=cat.id),
                _h("NOPE", "ASX", "5", "2")]
    quotes = FakeProvider().get_quotes([("VAS", "ASX"), ("NOPE", "ASX")])

    vas, nope = value_holdings(holdings, [cat], quotes)

    assert vas.category_name == "Kids fund"
    assert vas.market_value == Decimal("1000")
    assert vas.gain == Decimal("90")
    assert round(vas.gain_pct, 2) == Decimal("9.89")
    assert nope.category_name == UNCATEGORISED
    assert nope.market_value is None

    totals = totals_by_currency([vas, nope])["AUD"]
    assert totals["cost_base"] == Decimal("920")
    assert totals["market_value"] == Decimal("1000")
    assert totals["priced_cost_base"] == Decimal("910")
