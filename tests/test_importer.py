"""Broker CSV import. The sample files are made up in the shape of each broker's export."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from share_recommender.core.importer import (
    Broker,
    Layout,
    detect,
    guess_broker,
    parse_date,
    parse_number,
    parse_rows,
    parse_ticker,
    read_csv,
)
from share_recommender.core.models import Holding
from tests.fakes import USER_ID

COMMSEC = """\
Date,Reference,Details,Debit($),Credit($),Balance($)
03/02/2026,C123456,B 100 VAS @ 95.500000,9569.95,,-9569.95
10/03/2026,C123457,B 25 CBA @ 160.20,4024.95,,-13594.90
15/04/2026,C123458,S 50 VAS @ 101.00,,5030.05,-8564.85
20/04/2026,D998877,Direct Credit 123456 Dividend,,120.00,-8444.85
"""

CMC = """\
CMC Markets Invest - Order History
Account: XXXXXX

Order Date,Code,Market,Buy/Sell,Quantity,Average Price,Brokerage,Consideration,Status
2026-02-03 10:15:00,VGS,ASX,Buy,40,130.25,11.00,5221.00,Filled
2026-03-01 23:01:00,AAPL,NASDAQ,Buy,5,210.10,0.00,1050.50,Filled
2026-03-05 10:00:00,NDQ,ASX,Sell,10,48.00,11.00,469.00,Filled
2026-03-06 10:00:00,FAKE,MOON,Buy,1,1.00,0,1.00,Filled
"""


def _parse(text, **kw):
    header, rows, header_line = read_csv(text)
    return parse_rows(rows, detect(header), header_line=header_line, **kw)


def test_commsec_details_layout():
    header, _, _ = read_csv(COMMSEC)
    mapping = detect(header)
    assert mapping.layout is Layout.COMMSEC and guess_broker(mapping) is Broker.COMMSEC

    rows = _parse(COMMSEC, platform="CommSec")
    vas, cba, sale, dividend = rows
    assert vas.importable and vas.holding.ticker == "VAS" and vas.holding.exchange == "ASX"
    assert vas.holding.units == 100 and vas.holding.buy_price == Decimal("95.5")
    assert vas.holding.fees == Decimal("19.95")  # 9569.95 - 100 x 95.50
    assert vas.holding.buy_date == date(2026, 2, 3) and vas.holding.platform == "CommSec"
    assert cba.holding.fees == Decimal("19.95")
    assert sale.side == "sell" and not sale.importable and "Holdings page" in sale.problem
    assert dividend.side == "skip"
    assert vas.line == 2


def test_cmc_columns_layout_skips_title_lines():
    header, rows, header_line = read_csv(CMC)
    assert header_line == 4 and len(rows) == 4
    mapping = detect(header)
    assert mapping.layout is Layout.COLUMNS
    assert mapping.columns["ticker"] == "Code" and mapping.columns["price"] == "Average Price"
    assert guess_broker(mapping, "CMC_orders.csv") is Broker.CMC

    vgs, aapl, ndq, fake = _parse(CMC)
    assert vgs.importable and vgs.holding.fees == 11 and vgs.holding.buy_date == date(2026, 2, 3)
    assert aapl.holding.exchange == "NASDAQ" and aapl.holding.currency == "USD"
    assert ndq.side == "sell" and not ndq.importable
    assert fake.problem == "Unknown exchange"


def test_duplicates_against_existing_holdings_and_within_the_file():
    existing = [Holding(id=uuid4(), user_id=USER_ID, ticker="VAS", exchange="ASX", units="100",
                        buy_price="95.5", buy_date=date(2026, 2, 3))]
    vas, cba, *_ = _parse(COMMSEC, existing=existing)
    assert vas.duplicate and not cba.duplicate
    twice = COMMSEC + "03/02/2026,C1,B 25 CBA @ 160.20,4024.95,,0\n".replace("03/02", "10/03")
    rows = _parse(twice)
    assert [r.duplicate for r in rows if r.holding] == [False, False, True]


def test_missing_columns_and_bad_values():
    header, rows, _ = read_csv("Date,Code,Quantity\n01/01/2026,VAS,10\n")
    assert detect(header).missing() == ["price"]
    rows = _parse("Date,Code,Quantity,Price\n01/01/2099,VAS,10,90\n,VAS,1,1\n")
    assert "future" in rows[0].problem and rows[1].problem == "Couldn't read the date"


@pytest.mark.parametrize("text, expected", [
    ("$1,234.50", Decimal("1234.50")), ("(12.00)", Decimal("-12.00")), ("", None), ("-", None),
    ("AUD 5", Decimal(5)), ("abc", None),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("03/02/2026", date(2026, 2, 3)), ("2026-02-03", date(2026, 2, 3)),
    ("3-Feb-2026", date(2026, 2, 3)), ("03 Feb 2026", date(2026, 2, 3)),
    ("2026-02-03T10:15:00", date(2026, 2, 3)), ("03/02/2026 10:15", date(2026, 2, 3)),
    ("nonsense", None),
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("text, exchange, expected", [
    ("vas", None, ("VAS", "ASX")), ("VAS.AX", None, ("VAS", "ASX")),
    ("ASX:VAS", None, ("VAS", "ASX")), ("AAPL.US", None, ("AAPL", "NASDAQ")),
    ("JPM:US", None, ("JPM", "NYSE")), ("ZZZZ:US", None, ("ZZZZ", "NYSE")),
    ("AAPL", "NASDAQ", ("AAPL", "NASDAQ")), ("AZN", "LON", ("AZN", "LSE")),
    ("XYZ", "MOON", ("XYZ", None)),  # an exchange named in the file but unknown is flagged
])
def test_parse_ticker(text, exchange, expected):
    assert parse_ticker(text, exchange, "ASX") == expected


# Holdings snapshot (made-up numbers in the shape of CMC's ProfitLoss export) ----------

SNAPSHOT = """\
Code,Last,FX Rate,Units Held,Net Avg Price AUD,Cost AUD,Market Value AUD,Day Gain AUD,P&L AUD,P&L %
MSFT:US,500.00,0.70,10,600.00,6000.00,7142.86,10.00,1142.86,19.05
VAS,100.00,,300,90.00,27000.00,30000.00,50.00,3000.00,11.11
DHHF,40.00,,100,35.00,3500.00,4000.00,5.00,500.00,14.29
WDS,30.00,,20,0,0,600.00,1.00,600.00,0
"""
AS_AT = date(2023, 1, 1)


def _snapshot(existing=(), as_at=AS_AT):
    header, rows, header_line = read_csv(SNAPSHOT)
    return parse_rows(rows, detect(header), header_line=header_line, platform="CMC Markets",
                      existing=list(existing), as_at=as_at)


def test_snapshot_layout_is_detected_and_converts_aud_prices():
    header, _, _ = read_csv(SNAPSHOT)
    mapping = detect(header)
    assert mapping.layout is Layout.SNAPSHOT and guess_broker(mapping) is Broker.CMC
    assert mapping.columns["units"] == "Units Held" and mapping.price_in_aud

    msft, vas, dhhf, wds = _snapshot()
    assert (msft.holding.exchange, msft.holding.currency) == ("NASDAQ", "USD")
    assert msft.holding.buy_price == Decimal("420")  # 600 AUD x 0.70 USD per AUD
    assert vas.holding.units == 300 and vas.holding.buy_date == AS_AT and vas.holding.fees == 0
    assert dhhf.importable
    assert not wds.importable and "demerger" in wds.problem
    assert wds.partial["units"] == 20 and wds.partial["buy_date"] == AS_AT


def test_snapshot_only_adds_what_the_transaction_history_does_not_cover():
    def held(ticker, units, price, exchange="ASX"):
        return Holding(id=uuid4(), user_id=USER_ID, ticker=ticker, exchange=exchange,
                       units=units, buy_price=price, buy_date=date(2024, 5, 1), fees="10")

    # VAS: 200 of 300 units bought after the history starts, for 200 x 95 + 10 = 19,010.
    # DHHF: fully covered. MSFT: more held than the broker shows.
    msft, vas, dhhf, _ = _snapshot([held("VAS", "200", "95"), held("DHHF", "100", "35"),
                                    held("MSFT", "12", "400", "NASDAQ")])
    assert vas.importable and vas.holding.units == 100
    assert vas.holding.buy_price == Decimal("79.9")  # (27,000 - 19,010) / 100
    assert "100 of 300" in vas.note
    assert dhhf.side == "covered" and not dhhf.importable and not dhhf.problem
    assert "2 more MSFT" in msft.problem


def test_snapshot_needs_a_date():
    rows = _snapshot(as_at=None)
    assert rows[1].problem == "Choose a date for these holdings"


def test_snapshot_cost_is_used_over_the_rounded_average_price():
    text = ("Code,FX Rate,Units Held,Net Avg Price AUD,Cost AUD\n"
            "TPW,,3,2.72,8.17\n"           # 3 x 2.72 = 8.16, but the cost is 8.17
            "AAPL:US,0.70,2,354.07,708.13\n")
    header, rows, header_line = read_csv(text)
    tpw, aapl = parse_rows(rows, detect(header), header_line=header_line, as_at=AS_AT)
    cent = Decimal("0.01")  # prices are stored to 6 decimal places, so exact to the cent
    assert tpw.holding.cost_base.quantize(cent) == Decimal("8.17")
    assert aapl.holding.cost_base.quantize(cent) == Decimal("495.69")  # 708.13 AUD x 0.70

    # Without a cost column, the average price is used.
    header, rows, header_line = read_csv("Code,Units Held,Net Avg Price AUD\nTPW,3,2.72\n")
    mapping = detect(header)
    assert mapping.missing() == []
    (tpw,) = parse_rows(rows, mapping, header_line=header_line, as_at=AS_AT)
    assert tpw.holding.cost_base == Decimal("8.16")
