from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.models import Category, Holding, RiskLevel
from share_recommender.core.preferences import (
    CategoryPreferences,
    DirectShares,
    DrawdownReaction,
    Experience,
    Flexibility,
    GoalStyle,
    IncomeGrowth,
    InvestorPreferences,
)
from share_recommender.core.recommender import (
    analyse_gaps,
    holding_values,
    plan_purchase,
    target_mix,
)
from share_recommender.core.universe import ETFS, UNIVERSE, AssetClass, Exclusion, Kind, Theme
from share_recommender.data.base import Fundamentals
from share_recommender.recommendations import build_recommendation, provider_fns
from tests.fakes import USER_ID, FakeProvider

TODAY = date(2026, 10, 25)
ETF_ONLY = InvestorPreferences(direct_shares=DirectShares.ETF_ONLY)


def _cat(risk=RiskLevel.MEDIUM, target_date=None):
    return Category(id=uuid4(), user_id=USER_ID, name="Goal", risk_level=risk,
                    target_date=target_date)


def _h(ticker, exchange, units="10", price="100"):
    return Holding(id=uuid4(), user_id=USER_ID, ticker=ticker, exchange=exchange, units=units,
                   buy_price=price, buy_date=date(2026, 1, 1))


def _weights(mix):
    return {s.asset_class: s.weight for s in mix.slices}


def _tickers(mix):
    return [p.ticker for p in mix.picks]


# Universe ---------------------------------------------------------------------

def test_universe_is_consistent():
    keys = [i.key for i in UNIVERSE]
    assert len(keys) == len(set(keys))
    assert all(i.exchange in EXCHANGES for i in UNIVERSE)
    for ac in AssetClass:  # every slice can be filled from the ASX alone
        assert any(e.asset_class is ac and e.exchange == "ASX" for e in ETFS)


# Target mix -------------------------------------------------------------------

@pytest.mark.parametrize("risk, inv, prefs, target_date, growth", [
    (RiskLevel.MEDIUM, InvestorPreferences(), CategoryPreferences(), None, 70),
    (RiskLevel.HIGH, InvestorPreferences(drawdown_reaction=DrawdownReaction.BUY_MORE),
     CategoryPreferences(horizon_years=20), None, 100),
    (RiskLevel.MEDIUM, InvestorPreferences(drawdown_reaction=DrawdownReaction.SELL_ALL),
     CategoryPreferences(flexibility=Flexibility.MUST_MEET), None, 40),
    # Short deadline caps shares however brave the answers are.
    (RiskLevel.HIGH, InvestorPreferences(drawdown_reaction=DrawdownReaction.BUY_MORE),
     CategoryPreferences(), date(2028, 6, 1), 10),
    (RiskLevel.MEDIUM, InvestorPreferences(), CategoryPreferences(), date(2030, 1, 1), 50),
])
def test_growth_percent(risk, inv, prefs, target_date, growth):
    mix = target_mix(_cat(risk, target_date), inv, prefs, TODAY)
    assert mix.growth_pct == growth
    assert sum(_weights(mix).values()) == 100
    assert sum(p.weight for p in mix.picks) == 100


def test_mix_shape_for_long_horizon():
    mix = target_mix(_cat(), ETF_ONLY, CategoryPreferences(horizon_years=15), TODAY)
    w = _weights(mix)
    assert w[AssetClass.EMERGING_SHARES] == 7  # 10% of 70% growth
    assert w[AssetClass.AU_SHARES] + w[AssetClass.GLOBAL_SHARES] == 63
    assert w[AssetClass.BONDS] == 24 and w[AssetClass.CASH] == 6
    assert _tickers(mix) == ["VAS", "VGS", "VGE", "VAF", "AAA"]
    assert mix.reasons


def test_passed_target_date_is_all_cash():
    mix = target_mix(_cat(target_date=date(2026, 1, 1)), ETF_ONLY, CategoryPreferences(), TODAY)
    assert _weights(mix) == {AssetClass.CASH: 100}
    assert any("passed" in w for w in mix.warnings)


# Picks ------------------------------------------------------------------------

def test_prefers_an_etf_already_held():
    mix = target_mix(_cat(), ETF_ONLY, CategoryPreferences(), TODAY, held={("IOZ", "ASX")})
    assert "IOZ" in _tickers(mix) and "VAS" not in _tickers(mix)


def test_exclusions_filter_shares_and_choose_screened_etfs():
    inv = InvestorPreferences(direct_shares=DirectShares.MIX,
                              exclusions=[Exclusion.FOSSIL_FUELS, Exclusion.TOBACCO])
    mix = target_mix(_cat(RiskLevel.HIGH), inv, CategoryPreferences(), TODAY)
    tickers = _tickers(mix)
    assert {"FAIR", "ETHI"} <= set(tickers)
    assert not {"BHP", "WDS", "STO", "XOM", "PM"} & set(tickers)


def test_income_focus_ranks_shares_by_dividend_yield():
    inv = InvestorPreferences(direct_shares=DirectShares.MIX, markets=["ASX"],
                              income_growth=IncomeGrowth.INCOME)
    fundamentals = {("TLS", "ASX"): Fundamentals(dividend_yield=Decimal("0.09")),
                    ("CBA", "ASX"): Fundamentals(dividend_yield=Decimal("0.03"))}
    mix = target_mix(_cat(RiskLevel.HIGH), inv, CategoryPreferences(), TODAY,
                     fundamentals=fundamentals)
    au_shares = [p.ticker for p in mix.picks
                 if p.kind is Kind.SHARE and p.asset_class is AssetClass.AU_SHARES]
    assert "VHY" in _tickers(mix)
    assert au_shares[0] == "TLS"
    assert "higher dividend" in mix.picks[_tickers(mix).index("TLS")].reason


def test_direct_shares_spread_by_sector_and_market():
    inv = InvestorPreferences(direct_shares=DirectShares.MIX, markets=["ASX", "NASDAQ", "LSE"])
    mix = target_mix(_cat(RiskLevel.HIGH), inv, CategoryPreferences(horizon_years=20), TODAY)
    au = [p for p in mix.picks if p.kind is Kind.SHARE and p.asset_class is AssetClass.AU_SHARES]
    glob = [p for p in mix.picks
            if p.kind is Kind.SHARE and p.asset_class is AssetClass.GLOBAL_SHARES]
    assert len(au) == 5 and len(glob) == 5
    assert {p.exchange for p in glob} == {"NASDAQ", "LSE"}
    au_slice = _weights(mix)[AssetClass.AU_SHARES]
    assert float(sum(p.weight for p in au)) == pytest.approx(float(au_slice) / 2, abs=0.5)


def test_beginners_get_fewer_individual_shares():
    inv = InvestorPreferences(direct_shares=DirectShares.MIX, experience=Experience.BEGINNER)
    mix = target_mix(_cat(RiskLevel.HIGH), inv, CategoryPreferences(horizon_years=20), TODAY)
    shares = sum(p.weight for p in mix.picks if p.kind is Kind.SHARE)
    assert 0 < shares <= mix.growth_pct * Decimal("0.25") + 1


def test_unfillable_slices_warn():
    inv = InvestorPreferences(markets=["LSE"], direct_shares=DirectShares.ETF_ONLY)
    mix = target_mix(_cat(), inv, CategoryPreferences(), TODAY)
    assert "ISF" in _tickers(mix)
    assert any("Bonds" in w or "bonds" in w for w in mix.warnings)


# Gaps -------------------------------------------------------------------------

def test_gaps_compare_holdings_with_target():
    mix = target_mix(_cat(), ETF_ONLY, CategoryPreferences(horizon_years=15), TODAY)
    holdings = [_h("VAS", "ASX"), _h("MYSTERY", "ASX"), _h("VOO", "NYSE"), _h("DHHF", "ASX")]
    provider = FakeProvider(price_everything=True)
    quotes = provider.get_quotes([(h.ticker, h.exchange) for h in holdings])
    values, notes = holding_values(holdings, quotes, {"USD": Decimal("0.5")}, "AUD")
    gaps = analyse_gaps(mix, values, "AUD", notes)

    assert gaps.total_value == Decimal(5000)  # 1000 x 3 in AUD + 1000 USD at 0.5
    by_key = {g.key: g for g in gaps.slices}
    # MYSTERY isn't on the list, so it counts as Australian shares; DHHF is split 37/55/8.
    assert by_key["au_shares"].current_value == 2370
    assert by_key["global_shares"].current_value == 2550
    assert by_key["emerging_shares"].current_value == 80
    assert by_key["bonds"].difference == by_key["bonds"].target_value > 0
    assert any("MYSTERY" in n for n in gaps.notes)
    assert any("Diversified funds" in n for n in gaps.notes)


def test_missing_price_counts_at_cost():
    values, notes = holding_values([_h("NOPE", "ASX", units="2", price="50")], {}, {}, "AUD")
    assert values[("NOPE", "ASX")] == 100
    assert "no current price" in notes[0]


# Buy plan ---------------------------------------------------------------------

def _plan(mix, amount, holdings=(), brokerage="0", fx=None):
    provider = FakeProvider(price_everything=True)
    keys = {(p.ticker, p.exchange) for p in mix.picks} | {(h.ticker, h.exchange) for h in holdings}
    quotes = provider.get_quotes(list(keys))
    fx = fx or {"USD": Decimal("0.5")}
    values, _ = holding_values(list(holdings), quotes, fx, "AUD")
    return plan_purchase(mix, values, Decimal(amount), quotes, fx, "AUD", Decimal(brokerage))


def test_buy_plan_from_scratch_follows_the_mix():
    mix = target_mix(_cat(), ETF_ONLY, CategoryPreferences(horizon_years=15), TODAY)
    plan = _plan(mix, "10000")
    units = {o.ticker: o.units for o in plan.orders}
    # 100 per unit: VAS 22.5%, VGS 40.5%, VGE 7%, VAF 24%, AAA 6% of 10,000
    assert units == {"VAS": 22, "VGS": 41, "VGE": 7, "VAF": 24, "AAA": 6}
    spent = sum(o.total_cost for o in plan.orders)
    assert spent + plan.leftover == 10000 and 0 <= plan.leftover < 100


def test_buy_plan_fills_gaps_first_and_never_overspends():
    mix = target_mix(_cat(), ETF_ONLY, CategoryPreferences(horizon_years=15), TODAY)
    # Already heavy in Australian shares, so new money skips VAS.
    plan = _plan(mix, "5000", holdings=[_h("VAS", "ASX", units="100")], brokerage="10")
    tickers = {o.ticker for o in plan.orders}
    assert "VAS" not in tickers and "VGS" in tickers
    assert sum(o.total_cost for o in plan.orders) <= 5000
    assert all(o.brokerage == 10 for o in plan.orders)


def test_buy_plan_drops_tiny_trades():
    mix = target_mix(_cat(), ETF_ONLY, CategoryPreferences(horizon_years=15), TODAY)
    plan = _plan(mix, "2000")
    assert {o.ticker for o in plan.orders} <= {"VGS", "VAF", "VAS"}
    assert any("too small" in n for n in plan.notes)
    assert _plan(mix, "300").orders == []


def test_buy_plan_converts_overseas_prices():
    inv = InvestorPreferences(markets=["NYSE", "NASDAQ"], direct_shares=DirectShares.ETF_ONLY)
    mix = target_mix(_cat(), inv, CategoryPreferences(horizon_years=15), TODAY)
    plan = _plan(mix, "10000")
    vt = next(o for o in plan.orders if o.ticker == "VT")
    assert vt.currency == "USD"
    assert vt.total_cost == vt.units * 200  # 100 USD at 0.5 USD per AUD
    assert any("exchange rate" in n for n in plan.notes)


def test_build_recommendation_end_to_end():
    provider = FakeProvider(price_everything=True)
    cat = _cat()
    inv = InvestorPreferences(direct_shares=DirectShares.SOME,
                              income_growth=IncomeGrowth.INCOME)
    rec = build_recommendation(cat, inv, CategoryPreferences(), [_h("VAS", "ASX")], "AUD",
                               *provider_fns(provider), amount=Decimal(5000), today=TODAY)
    assert rec.category_id == cat.id
    assert rec.gaps.total_value == 1000
    assert rec.buy_plan and rec.buy_plan.orders
    assert rec.model_dump_json()  # serialisable for the API and the AI explanation


# Speculative goals ------------------------------------------------------------

def _spec(name="Speculative"):
    return Category(id=uuid4(), user_id=USER_ID, name=name, risk_level=RiskLevel.HIGH)


def test_speculative_goal_is_all_high_growth_shares():
    mix = target_mix(_spec(), InvestorPreferences(), CategoryPreferences(horizon_years=10),
                     TODAY)
    assert mix.speculative and mix.growth_pct == 100
    assert sum(p.weight for p in mix.picks) == 100
    assert not {AssetClass.BONDS, AssetClass.CASH} & set(_weights(mix))
    etfs = [p for p in mix.picks if p.kind is Kind.ETF]
    shares = [p for p in mix.picks if p.kind is Kind.SHARE]
    assert [p.ticker for p in etfs] == ["RBTZ", "SEMI"]
    assert len(shares) == 6 and "NVDA" in {p.ticker for p in shares}
    assert abs(sum(p.weight for p in etfs) - 30) <= Decimal("0.5")  # rounding
    assert not mix.warnings


def test_speculative_follows_themes_markets_and_exclusions():
    inv = InvestorPreferences(markets=["NASDAQ", "NYSE"], exclusions=[Exclusion.WEAPONS])
    prefs = CategoryPreferences(style=GoalStyle.SPECULATIVE,
                                themes=[Theme.AI, Theme.CYBERSECURITY], horizon_years=10)
    mix = target_mix(_cat(), inv, prefs, TODAY)  # any category can be speculative
    tickers = _tickers(mix)
    assert {"BOTZ", "CIBR"} <= set(tickers) and "PLTR" not in tickers
    assert {"CRWD", "NVDA"} <= set(tickers)
    assert all(p.exchange in {"NASDAQ", "NYSE"} for p in mix.picks)


def test_speculative_etf_only_and_held_first():
    inv = InvestorPreferences(direct_shares=DirectShares.ETF_ONLY)
    mix = target_mix(_spec(), inv, CategoryPreferences(themes=[Theme.AI]), TODAY,
                     held={("SMH", "NASDAQ")})
    assert all(p.kind is Kind.ETF for p in mix.picks)
    assert _tickers(mix)[0] == "SMH" and len(mix.picks) == 3


def test_speculative_warnings():
    inv = InvestorPreferences(experience=Experience.BEGINNER)
    mix = target_mix(_spec(), inv, CategoryPreferences(horizon_years=2), TODAY)
    assert len(mix.warnings) == 2
    nothing = target_mix(_spec(), InvestorPreferences(markets=["SGX"]), CategoryPreferences(),
                         TODAY)
    assert nothing.picks == [] and nothing.warnings
    plan = _plan(nothing, "1000")
    assert plan.orders == [] and plan.leftover == 1000


def test_core_goals_never_pick_thematic_or_speculative():
    inv = InvestorPreferences(direct_shares=DirectShares.MIX)
    mix = target_mix(_cat(RiskLevel.HIGH), inv, CategoryPreferences(horizon_years=20), TODAY)
    assert not {"RBTZ", "SEMI", "AMD", "PLTR", "BRN"} & set(_tickers(mix))
    explicit_core = CategoryPreferences(style=GoalStyle.CORE)
    assert not target_mix(_spec(), inv, explicit_core, TODAY).speculative


def test_speculative_pot_too_big_warns():
    provider = FakeProvider(price_everything=True)
    spec, core = _spec(), _cat()
    spec_holding = _h("NVDA", "NASDAQ").model_copy(update={"category_id": spec.id})
    core_holding = _h("VAS", "ASX", units="20").model_copy(update={"category_id": core.id})
    rec = build_recommendation(spec, InvestorPreferences(), CategoryPreferences(horizon_years=10),
                               [spec_holding], "AUD", *provider_fns(provider), today=TODAY,
                               all_holdings=[spec_holding, core_holding])
    # NVDA: 10 x 100 USD = 2,000 AUD; VAS 2,000 AUD, so the pot is half the portfolio.
    assert any("50% of all your holdings" in w for w in rec.mix.warnings)


def test_avoided_tickers_are_never_picked():
    inv = InvestorPreferences(direct_shares=DirectShares.MIX, markets=["ASX"],
                              avoid_tickers=["cba", "VAS"])
    mix = target_mix(_cat(RiskLevel.HIGH), inv, CategoryPreferences(horizon_years=20), TODAY)
    assert not {"CBA", "VAS"} & set(_tickers(mix))
    assert "A200" in _tickers(mix)
    assert any("CBA, VAS" in r for r in mix.reasons)
