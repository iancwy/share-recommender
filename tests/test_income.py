from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from share_recommender.core.income import by_financial_year, financial_year, income_in_base
from share_recommender.core.models import Dividend, DividendIn
from tests.fakes import USER_ID


def _d(day, amount, franking="0", exchange="ASX", drp=False):
    return Dividend(id=uuid4(), user_id=USER_ID, ticker="VAS", exchange=exchange, pay_date=day,
                    amount=amount, franking_credits=franking, reinvested=drp)


def test_financial_year():
    assert financial_year(date(2026, 6, 30)) == "2025-26"
    assert financial_year(date(2026, 7, 1)) == "2026-27"


def test_totals_by_financial_year_and_currency():
    divs = [_d(date(2026, 7, 15), "100", "30"), _d(date(2026, 10, 1), "50", drp=True),
            _d(date(2026, 6, 30), "80", "20"), _d(date(2026, 8, 1), "10", exchange="NASDAQ")]
    years = by_financial_year(divs)
    assert [(y.year, y.currency) for y in years] == [("2026-27", "USD"), ("2026-27", "AUD"),
                                                     ("2025-26", "AUD")]
    aud = years[1]
    assert (aud.payments, aud.cash, aud.franking, aud.grossed_up, aud.reinvested) == \
        (2, 150, 30, 180, 50)
    assert income_in_base(divs, {"USD": Decimal("0.5")}, "AUD") == Decimal("250.00")
    assert income_in_base(divs, {}, "AUD", since=date(2026, 7, 1)) == Decimal("150.00")


def test_dividend_validation():
    d = DividendIn(ticker=" vas ", exchange="asx", pay_date="2026-07-15", amount="120.50",
                   franking_credits="30", platform=" ")
    assert (d.ticker, d.exchange, d.platform, d.grossed_up, d.currency) == \
        ("VAS", "ASX", None, Decimal("150.50"), "AUD")
    for bad in [{"exchange": "MOON"}, {"amount": "0"}, {"pay_date": "2099-01-01"},
                {"franking_credits": "-1"}]:
        with pytest.raises(ValidationError):
            DividendIn(**({"ticker": "VAS", "exchange": "ASX", "pay_date": "2026-01-01",
                           "amount": "1"} | bad))
