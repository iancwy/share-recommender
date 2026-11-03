"""The AI picker's rules screen: filters, scores and the shortlist for one goal."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from share_recommender.core.models import Category, RiskLevel
from share_recommender.core.preferences import (
    CategoryPreferences,
    DirectShares,
    IncomeGrowth,
    InvestorPreferences,
)
from share_recommender.core.screen import (
    MAX_PER_SECTOR,
    SHORTLIST_SIZE,
    WEIGHTS,
    GoalScreenInput,
    Momentum,
    ScreenRow,
    hard_filters,
    momentum,
    screen,
    weights_for,
)
from share_recommender.core.universe import Exclusion, Theme
from share_recommender.data.base import Candidate, Fundamentals, PriceBar

TODAY = date(2026, 11, 9)
D = Decimal
FX = {"USD": D("0.65")}
YEAR = Momentum(D("0.05"), D("0.1"), D("0.2"), D("0.15"), D("0.2"), D("-0.1"), 400)


def _goal(risk=RiskLevel.MEDIUM, speculative=False, themes=(Theme.AI,)):
    cat = Category(id=uuid4(), user_id=uuid4(), name="Speculative" if speculative else "House",
                   risk_level=risk)
    return GoalScreenInput(cat, CategoryPreferences(themes=list(themes)), speculative, None)


def _inv(**kw):
    return InvestorPreferences(**({"markets": ["ASX", "NYSE"]} | kw))


def _row(ticker, exchange="ASX", cap="10e9", volume="1e6", price="20", sector="Financials",
         industry="Banks", mom=YEAR, kind="share", above=True, **fund):
    c = Candidate(ticker, exchange, f"{ticker} Ltd", kind, D(price), D(cap) if cap else None,
                  avg_volume=D(volume), above_200d=above)
    f = Fundamentals(sector=sector, industry=industry, name=f"{ticker} Ltd",
                     quote_type="ETF" if kind == "etf" else "EQUITY", **fund)
    return ScreenRow(c, f, mom)


def _row_named(ticker, name, **kw):
    row = _row(ticker, **kw)
    return ScreenRow(Candidate(**{**row.candidate.__dict__, "name": name}),
                     Fundamentals(**{**row.fundamentals.__dict__, "name": name}), row.momentum)


def _reasons(row, goal=None, inv=None):
    return [name for name, _ in hard_filters(row, goal or _goal(), inv or _inv(), "AUD", FX)]


# Filters ----------------------------------------------------------------------------

def test_a_good_large_company_passes():
    assert _reasons(_row("ZGD")) == []


@pytest.mark.parametrize("row, inv, failed", [
    (_row("ZLN", "LSE"), None, "markets"),
    (_row("ZAV"), _inv(avoid_tickers=["ZAV"]), "avoid"),
    (_row("ZTB", industry="Tobacco"), _inv(exclusions=[Exclusion.TOBACCO]), "exclusions"),
    (_row("ZBD", kind="etf", fund_category="Intermediate Core Bond"), None, "bonds"),
    (_row_named("ZCS", "Fake Cash ETF", kind="etf"), None, "bonds"),
    (_row("ZSM", cap="1e9"), None, "size"),
    (_row("ZTH", volume="1000"), None, "size"),
    (_row("ZNW", mom=Momentum(None, D("0.1"), None, None, None, None, 120)), None, "history"),
    (_row("ZSH"), _inv(direct_shares=DirectShares.ETF_ONLY), "etf_only"),
], ids=["markets", "avoid", "exclusions", "bond fund", "cash fund", "small", "thin", "new",
        "etf only"])
def test_each_hard_filter(row, inv, failed):
    assert failed in _reasons(row, inv=inv)


def test_size_limits_follow_risk():
    mid = _row("ZMD", cap="1e9")  # A$1bn
    assert "size" in _reasons(mid, _goal(RiskLevel.MEDIUM))
    assert "size" not in _reasons(mid, _goal(RiskLevel.HIGH))
    assert "size" in _reasons(_row("ZMD", cap="3e9"), _goal(RiskLevel.LOW))


def test_us_size_is_converted_to_the_base_currency():
    # US$1.5bn is about A$2.3bn: big enough for a medium-risk goal.
    assert "size" not in _reasons(_row("ZUS", "NYSE", cap="1.5e9"))


def test_speculative_goals_keep_their_themes_only():
    goal = _goal(speculative=True, themes=[Theme.AI])
    chip = _row("ZCH", cap="300e6", volume="1e5", price="5", industry="Semiconductors",
                mom=None)
    bank = _row("ZBK", cap="300e6", volume="1e5", price="5")
    assert _reasons(chip, goal) == []  # small, short history: fine for speculative
    assert _reasons(bank, goal) == ["themes"]


def test_theme_etfs_only_go_in_speculative_goals():
    row = ScreenRow(Candidate("SEMI", "ASX", "Global X Semiconductor ETF", "etf", D(10), None),
                    Fundamentals(quote_type="ETF"), YEAR)
    assert "themes" in _reasons(row)


# Weights ------------------------------------------------------------------------------

@pytest.mark.parametrize("risk", list(RiskLevel))
@pytest.mark.parametrize("style", list(IncomeGrowth))
def test_weights_add_up_to_100(risk, style):
    assert sum(weights_for(_goal(risk), _inv(income_growth=style)).values()) == 100
    assert sum(weights_for(_goal(risk, speculative=True), _inv()).values()) == 100


def test_risk_moves_weight():
    base = WEIGHTS[IncomeGrowth.BALANCED]
    low = weights_for(_goal(RiskLevel.LOW), _inv())
    high = weights_for(_goal(RiskLevel.HIGH), _inv())
    assert low["stability"] == base["stability"] + 10
    assert high["momentum"] == base["momentum"] + 10 and high["stability"] == 0


# Momentum -----------------------------------------------------------------------------

def test_momentum_from_price_bars():
    bars = [PriceBar(TODAY - timedelta(days=d), D(100) + D(400 - d) / 10) for d in range(400)]
    m = momentum(bars, TODAY)
    assert m.history_days == 399
    assert m.r12m > m.r6m > m.r3m > 0
    assert m.max_drawdown == 0 and m.volatility is not None
    assert momentum(bars[:1], TODAY) is None
    gappy = bars + [PriceBar(TODAY - timedelta(days=500), D("NaN"))]
    assert momentum(gappy, TODAY) == m  # missing closes are skipped


# The shortlist --------------------------------------------------------------------------

def _pool():
    rows = []
    for i in range(20):  # 20 passing banks and miners, scores rising with i
        sector = "Financials" if i % 2 else "Materials"
        rows.append(_row(f"ZP{i:02d}", sector=sector, dividend_yield=D(i) / 200,
                         return_on_equity=D(i) / 100,
                         mom=Momentum(None, D(i) / 50, None, D(i) / 40, D("0.3") - D(i) / 100,
                                      D(-0.2), 400)))
    for i in range(10):  # one each from ten other sectors
        rows.append(_row(f"ZT{i}", sector=f"Sector {i}"))
    rows.append(_row("ZSM", cap="1e8"))                       # too small
    rows.append(_row("ZTB", industry="Tobacco"))             # excluded
    return rows


def test_shortlist_size_sector_cap_and_counts():
    inv = _inv(exclusions=[Exclusion.TOBACCO])
    out = screen(_goal(), _pool(), inv, set(), set(), "AUD", FX)
    assert len(out.items) == SHORTLIST_SIZE
    sectors = [i.sector for i in out.items]
    assert all(sectors.count(s) <= MAX_PER_SECTOR for s in sectors)
    assert out.counts["pool"] == 32 and out.counts["passed"] == 30
    assert out.counts["failed_size"] == 1 and out.counts["failed_exclusions"] == 1
    scores = [i.score for i in out.items]
    assert scores == sorted(scores, reverse=True)


def test_held_and_watchlist_rows_always_come_through():
    inv = _inv(exclusions=[Exclusion.TOBACCO])
    out = screen(_goal(), _pool(), inv, held={("ZSM", "ASX")},
                 watchlist={("ZTB", "ASX"), ("ZGONE", "ASX")}, base_currency="AUD", fx=FX)
    by = {i.key: i for i in out.items}
    assert by[("ZSM", "ASX")].source == "held" and by[("ZSM", "ASX")].score is None
    assert any("Too small" in n for n in by[("ZSM", "ASX")].filter_notes)
    assert by[("ZTB", "ASX")].source == "watchlist"
    assert by[("ZGONE", "ASX")].filter_notes == ["No market data found"]


def test_an_income_investor_gets_the_highest_yield_first():
    inv = _inv(income_growth=IncomeGrowth.INCOME)
    rows = [_row("ZLOW", dividend_yield=D("0.01")), _row("ZMID", dividend_yield=D("0.03")),
            _row("ZHI", dividend_yield=D("0.07"), sector="Energy")]
    out = screen(_goal(), rows, inv, set(), set(), "AUD", FX)
    assert out.items[0].key == ("ZHI", "ASX")
    assert out.items[0].metrics["yield_pct"] == 7.0


def test_missing_data_scores_50_with_a_note():
    rows = [_row("ZA", return_on_equity=D("0.1")), _row("ZB")]
    out = screen(_goal(), rows, _inv(), set(), set(), "AUD", FX)
    b = next(i for i in out.items if i.key == ("ZB", "ASX"))
    assert "No profit data" in b.filter_notes and "No dividend data" in b.filter_notes
    assert b.factors["income"] is None


def test_etfs_score_on_fees_not_profits():
    rows = [_row("ZE1", kind="etf", sector=None, expense_ratio=D("0.0004")),
            _row("ZE2", kind="etf", sector=None, expense_ratio=D("0.004"))]
    out = screen(_goal(), rows, _inv(), set(), set(), "AUD", FX)
    first = out.items[0]
    assert first.key == ("ZE1", "ASX") and set(first.factors) == {
        "momentum", "income", "stability", "fees"}
    assert first.kind == "etf" and first.sector == "ETF"


def test_the_screen_is_deterministic():
    inv = _inv(exclusions=[Exclusion.TOBACCO])
    a = screen(_goal(), _pool(), inv, set(), set(), "AUD", FX)
    b = screen(_goal(), list(reversed(_pool())), inv, set(), set(), "AUD", FX)
    assert [(i.key, i.score) for i in a.items] == [(i.key, i.score) for i in b.items]
