"""The AI picker's rules screen: a big candidate pool down to a short, scored list per goal.

Pure functions. For one goal it:

1. drops candidates that fail a **hard filter** (markets, tickers to avoid, excluded
   industries, bonds and cash funds, size and trading, price history, ETFs only, speculative
   themes), each with a plain-English reason;
2. **scores** the rest 0 to 100 on six factors (momentum, quality, value, income, growth,
   stability), each a percentile rank within the goal's passing rows, weighted by the goal and
   the person's answers;
3. keeps the **top 15** (at most 3 from one sector), plus everything the person holds directly
   in the goal and everything on their watchlist for it, even if those failed a filter, so
   Claude can say whether they still fit.

Thresholds and weights are named constants below.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.models import Category, RiskLevel
from share_recommender.core.preferences import (
    CategoryPreferences,
    DirectShares,
    IncomeGrowth,
    InvestorPreferences,
)
from share_recommender.core.recommender import TargetMix
from share_recommender.core.universe import (
    AssetClass,
    EtfStyle,
    Exclusion,
    industry_exclusion,
    industry_theme,
    is_bond_fund,
    lookup,
)
from share_recommender.core.watchlist import short_money
from share_recommender.data.base import Candidate, Fundamentals, PriceBar

Key = tuple[str, str]

# Minimum market cap and average daily trading (price x volume), in the base currency.
SIZE_LIMITS = {  # goal -> (market cap, daily trading)
    RiskLevel.LOW: (Decimal("5e9"), Decimal("5e6")),
    RiskLevel.MEDIUM: (Decimal("2e9"), Decimal("2e6")),
    RiskLevel.HIGH: (Decimal("500e6"), Decimal("1e6")),
    "speculative": (Decimal("200e6"), Decimal("250e3")),
}
MIN_HISTORY_DAYS = 365   # core goals need a year of prices
SHORTLIST_SIZE = 15
MAX_PER_SECTOR = 3
MAX_ROWS = 40            # screened rows are trimmed first, never held or watchlist ones
BELOW_200D_PENALTY = 10
MISSING_SCORE = 50.0

FACTORS = ["momentum", "quality", "value", "income", "growth", "stability"]
WEIGHTS = {  # each row adds up to 100
    IncomeGrowth.INCOME: {"momentum": 15, "quality": 25, "value": 15, "income": 30,
                          "growth": 5, "stability": 10},
    IncomeGrowth.BALANCED: {"momentum": 20, "quality": 25, "value": 20, "income": 10,
                            "growth": 15, "stability": 10},
    IncomeGrowth.GROWTH: {"momentum": 25, "quality": 20, "value": 10, "income": 0,
                          "growth": 35, "stability": 10},
    "speculative": {"momentum": 35, "quality": 10, "value": 0, "income": 0, "growth": 50,
                    "stability": 5},
}
RISK_SHIFT = 10  # points moved towards stability (low risk) or momentum (high risk)


# Momentum ------------------------------------------------------------------------

@dataclass(frozen=True)
class Momentum:
    r3m: Decimal | None          # fractions
    r6m: Decimal | None
    r12m: Decimal | None
    r12_1: Decimal | None        # 12 months to 1 month ago, skipping the latest month
    volatility: Decimal | None   # yearly, from daily changes over the last year
    max_drawdown: Decimal | None  # largest fall from a peak in the year (negative)
    history_days: int


def momentum(bars: list[PriceBar], today: date) -> Momentum | None:
    bars = sorted((b for b in bars if b.close is not None and b.close.is_finite() and b.close > 0),
                  key=lambda b: b.day)
    if len(bars) < 2:
        return None
    last = bars[-1].close

    def close_ago(days: int) -> Decimal | None:
        before = [b.close for b in bars if b.day <= today - timedelta(days=days)]
        return before[-1] if before else None

    def ret(a, b):
        return (a / b - 1) if a and b else None

    year = [b.close for b in bars if b.day > today - timedelta(days=365)]
    changes = [math.log(float(b / a)) for a, b in zip(year, year[1:], strict=False)]
    vol = None
    if len(changes) >= 20:
        mean = sum(changes) / len(changes)
        var = sum((c - mean) ** 2 for c in changes) / (len(changes) - 1)
        vol = Decimal(str(round(math.sqrt(var) * math.sqrt(252), 4)))
    peak, drawdown = Decimal(0), Decimal(0)
    for c in year:
        peak = max(peak, c)
        drawdown = min(drawdown, c / peak - 1)
    return Momentum(ret(last, close_ago(91)), ret(last, close_ago(182)), ret(last, close_ago(365)),
                    ret(close_ago(30), close_ago(365)), vol, drawdown if year else None,
                    (bars[-1].day - bars[0].day).days)


# Inputs and outputs ------------------------------------------------------------

@dataclass(frozen=True)
class ScreenRow:
    candidate: Candidate
    fundamentals: Fundamentals | None = None
    momentum: Momentum | None = None
    source: str = "screen"  # "screen", or "universe" for a curated ETF

    @property
    def key(self) -> Key:
        return self.candidate.key

    @property
    def is_etf(self) -> bool:
        return self.candidate.kind == "etf" or bool(self.fundamentals and
                                                    self.fundamentals.is_fund)

    @property
    def name(self) -> str | None:
        return (self.fundamentals.name if self.fundamentals else None) or self.candidate.name


@dataclass(frozen=True)
class GoalScreenInput:
    category: Category
    prefs: CategoryPreferences
    speculative: bool
    mix: TargetMix


@dataclass
class ShortlistItem:
    key: Key
    name: str | None
    kind: str                      # "share" or "etf"
    sector: str | None
    industry: str | None
    score: float | None            # None when it failed a filter (held or watchlist only)
    factors: dict[str, float | None]
    metrics: dict
    source: str                    # "screen", "held", "watchlist" or "universe"
    filter_notes: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)  # names of the hard filters it failed
    summary: str | None = None     # the provider's description, at most 300 characters


@dataclass
class Shortlist:
    goal_id: str
    items: list[ShortlistItem]
    counts: dict[str, int]         # pool size, failures per filter, passed, shortlisted


# Hard filters --------------------------------------------------------------------

def _to_base(amount: Decimal | None, currency: str, base: str, fx: dict) -> Decimal | None:
    if amount is None:
        return None
    rate = Decimal(1) if currency == base else fx.get(currency)
    return amount / rate if rate else None


def hard_filters(row: ScreenRow, goal: GoalScreenInput, inv: InvestorPreferences, base: str,
                 fx: dict) -> list[tuple[str, str]]:
    """(filter, reason) for every filter the row fails; empty if it passes them all."""
    c, f, inst = row.candidate, row.fundamentals, lookup(*row.key)
    out = []
    if c.exchange not in inv.markets:
        out.append(("markets", f"{c.exchange} isn't one of your markets"))
    if c.ticker in inv.avoid_tickers:
        out.append(("avoid", "On your list of tickers to leave out"))
    excluded: set[Exclusion] = set(inst.excluded_by) if inst else set()
    if f and (x := industry_exclusion(f.industry)):
        excluded.add(x)
    if hit := excluded & set(inv.exclusions):
        out.append(("exclusions", f"In an industry you avoid ({', '.join(sorted(hit))})"))
    if row.is_etf and (is_bond_fund(row.name, f.fund_category if f else None)
                       or (inst is not None and AssetClass.CASH in inst.mix)
                       or "cash" in (row.name or "").lower()):
        out.append(("bonds", "A bond, fixed-income or cash fund: the picker is for shares "
                             "and share ETFs"))
    if not row.is_etf:
        min_cap, min_trading = SIZE_LIMITS["speculative" if goal.speculative
                                           else goal.category.risk_level]
        currency = EXCHANGES[c.exchange].currency
        cap = _to_base(c.market_cap or (f.market_cap if f else None), currency, base, fx)
        volume = c.avg_volume or (f.avg_volume if f else None)
        trading = _to_base(c.price * volume, currency, base, fx) if c.price and volume else None
        if cap is None:
            out.append(("size", "Company size unknown"))
        elif cap < min_cap:
            out.append(("size", f"Too small for this goal (under {short_money(min_cap, base)})"))
        elif trading is not None and trading < min_trading:
            out.append(("size", f"Too thinly traded (under {short_money(min_trading, base)} a "
                                "day)"))
    if not goal.speculative and (row.momentum is None
                                 or row.momentum.history_days < MIN_HISTORY_DAYS - 7):
        out.append(("history", "Less than a year of price history"))
    if inv.direct_shares is DirectShares.ETF_ONLY and not row.is_etf:
        out.append(("etf_only", "You chose ETFs only"))
    if goal.speculative:
        themes = set(inst.themes) if inst else set()
        if f and (t := industry_theme(f.industry)):
            themes.add(t)
        if not themes & set(goal.prefs.themes):
            out.append(("themes", "Not in this goal's themes"))
    elif inst is not None and inst.style is EtfStyle.THEMATIC:
        out.append(("themes", "A theme ETF: only for speculative goals"))
    return out


# Scoring ---------------------------------------------------------------------------

def weights_for(goal: GoalScreenInput, inv: InvestorPreferences) -> dict[str, int]:
    w = dict(WEIGHTS["speculative" if goal.speculative else inv.income_growth])
    risk = goal.category.risk_level
    if risk is RiskLevel.LOW:  # half from growth (if it has it), the rest from momentum
        take_g = min(w["growth"], RISK_SHIFT // 2)
        take_m = min(w["momentum"], RISK_SHIFT - take_g)
        w["growth"] -= take_g
        w["momentum"] -= take_m
        w["stability"] += take_g + take_m
    elif risk is RiskLevel.HIGH:  # from stability to momentum
        take = min(w["stability"], RISK_SHIFT)
        w["stability"] -= take
        w["momentum"] += take
    return w


def _percentiles(values: dict[Key, float | None], higher_is_better: bool = True
                 ) -> dict[Key, float | None]:
    """0 to 100 within the rows that have a value; ties share the same rank."""
    known = {k: v for k, v in values.items() if v is not None}
    if len(known) == 1:
        return {k: 50.0 if v is not None else None for k, v in values.items()}
    ordered = sorted(known.values())
    n = len(ordered)
    out = {}
    for k, v in values.items():
        if v is None:
            out[k] = None
            continue
        below = sum(1 for x in ordered if x < v)
        equal = sum(1 for x in ordered if x == v)
        pct = (below + (equal - 1) / 2) / (n - 1) * 100
        out[k] = round(pct if higher_is_better else 100 - pct, 1)
    return out


def _mean(*values: float | None) -> float | None:
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


def _f(value) -> float | None:
    return float(value) if value is not None else None


def score_rows(rows: list[ScreenRow], weights: dict[str, int]
               ) -> dict[Key, tuple[float, dict[str, float | None], list[str]]]:
    """Score, factor scores and notes for each row (all assumed to have passed the filters)."""
    def fund(r, name):
        return _f(getattr(r.fundamentals, name)) if r.fundamentals else None

    def mom(r, name):
        return _f(getattr(r.momentum, name)) if r.momentum else None

    def earnings_yield(r):
        pe = fund(r, "pe_ratio") or fund(r, "forward_pe") or _f(r.candidate.pe_ratio)
        if pe is None:
            return None
        return 1 / pe if pe > 0 else -1.0  # loss-makers rank last

    keys = [r.key for r in rows]
    by_key = {r.key: r for r in rows}
    p = {
        "m12_1": _percentiles({r.key: mom(r, "r12_1") for r in rows}),
        "m6": _percentiles({r.key: mom(r, "r6m") for r in rows}),
        "roe": _percentiles({r.key: fund(r, "return_on_equity") for r in rows}),
        "margin": _percentiles({r.key: fund(r, "profit_margin") for r in rows}),
        "debt": _percentiles({r.key: fund(r, "debt_to_equity") for r in rows}, False),
        "ey": _percentiles({r.key: earnings_yield(r) for r in rows}),
        "yield": _percentiles({r.key: fund(r, "dividend_yield")
                               if fund(r, "dividend_yield") is not None
                               else _f(r.candidate.dividend_yield) for r in rows}),
        "rev": _percentiles({r.key: fund(r, "revenue_growth") for r in rows}),
        "earn": _percentiles({r.key: fund(r, "earnings_growth") for r in rows}),
        "vol": _percentiles({r.key: mom(r, "volatility") for r in rows}, False),
        "dd": _percentiles({r.key: mom(r, "max_drawdown") for r in rows}),  # nearer 0 is better
        "fee": _percentiles({r.key: fund(r, "expense_ratio") for r in rows}, False),
    }
    out = {}
    for k in keys:
        r = by_key[k]
        m = None
        if p["m12_1"][k] is not None or p["m6"][k] is not None:
            m = 0.6 * (p["m12_1"][k] if p["m12_1"][k] is not None else MISSING_SCORE) + \
                0.4 * (p["m6"][k] if p["m6"][k] is not None else MISSING_SCORE)
            if r.candidate.above_200d is False:
                m = max(0.0, m - BELOW_200D_PENALTY)
        factors = {
            "momentum": m,
            "quality": _mean(p["roe"][k], p["margin"][k], p["debt"][k]),
            "value": p["ey"][k],
            "income": p["yield"][k],
            "growth": _mean(p["rev"][k], p["earn"][k]),
            "stability": _mean(p["vol"][k], p["dd"][k]),
        }
        w = dict(weights)
        if r.is_etf:  # funds: momentum, stability, income and (lower) fees only
            fee_weight = w.pop("quality") + w.pop("value") + w.pop("growth")
            factors = {name: factors[name] for name in ("momentum", "income", "stability")}
            factors["fees"] = p["fee"][k]
            w["fees"] = fee_weight
        notes = []
        labels = {"momentum": "No price trend data", "quality": "No profit data",
                  "value": "No earnings data", "income": "No dividend data",
                  "growth": "No growth data", "stability": "No volatility data",
                  "fees": "No fee data"}
        total = 0.0
        for name, weight in w.items():
            value = factors[name]
            if value is None:
                if weight:
                    notes.append(labels[name])
                value = MISSING_SCORE
            total += weight * value / 100
        out[k] = (round(total, 1), {n: (round(v, 1) if v is not None else None)
                                    for n, v in factors.items()}, notes)
    return out


# Metrics shown to Claude and on the page -------------------------------------------

def _pct(value, places=1):
    return round(float(value) * 100, places) if value is not None else None


def metrics(row: ScreenRow, base: str, fx: dict) -> dict:
    c, f, m = row.candidate, row.fundamentals, row.momentum
    currency = EXCHANGES[c.exchange].currency
    cap = _to_base(c.market_cap or (f.market_cap if f else None), currency, base, fx)
    price = c.price
    high = f.high_52w if f else None
    out = {
        "price": round(float(price), 2) if price is not None else None,
        "currency": currency,
        "market_cap": short_money(cap, base) if not row.is_etf else None,
        "pe": _f(f.pe_ratio if f and f.pe_ratio is not None else c.pe_ratio),
        "forward_pe": _f(f.forward_pe) if f else None,
        "yield_pct": _pct(f.dividend_yield if f and f.dividend_yield is not None
                          else c.dividend_yield, 2),
        "roe_pct": _pct(f.return_on_equity) if f else None,
        "margin_pct": _pct(f.profit_margin) if f else None,
        "debt_to_equity": _f(f.debt_to_equity) if f else None,
        "revenue_growth_pct": _pct(f.revenue_growth) if f else None,
        "return_3m_pct": _pct(m.r3m) if m else None,
        "return_6m_pct": _pct(m.r6m) if m else None,
        "return_12m_pct": _pct(m.r12m) if m else _pct(c.change_52w),
        "volatility_pct": _pct(m.volatility) if m else None,
        "largest_fall_pct": _pct(m.max_drawdown) if m else None,
        "from_52w_high_pct": _pct(price / high - 1) if price and high else None,
        "expense_ratio_pct": _pct(f.expense_ratio, 2) if row.is_etf and f else None,
    }
    return {k: v for k, v in out.items() if v is not None}


# The screen -------------------------------------------------------------------------

def _sector(row: ScreenRow) -> str:
    if row.is_etf:
        return "ETF"
    inst = lookup(*row.key)
    return (row.fundamentals.sector if row.fundamentals and row.fundamentals.sector
            else inst.sector if inst and inst.sector else "Unknown")


def screen(goal: GoalScreenInput, pool: list[ScreenRow], inv: InvestorPreferences,
           held: set[Key], watchlist: set[Key], base_currency: str, fx: dict) -> Shortlist:
    """The shortlist for one goal. ``held`` and ``watchlist`` are the keys for this goal."""
    rows = {r.key: r for r in pool}
    counts: dict[str, int] = {"pool": len(rows)}
    failures: dict[Key, list[tuple[str, str]]] = {}
    for key, row in rows.items():
        failed = hard_filters(row, goal, inv, base_currency, fx)
        failures[key] = failed
        for name, _ in failed[:1]:  # count each row once, under its first failure
            counts[f"failed_{name}"] = counts.get(f"failed_{name}", 0) + 1
    passing = [rows[k] for k in sorted(rows) if not failures[k]]
    counts["passed"] = len(passing)
    scored = score_rows(passing, weights_for(goal, inv))

    def item(row: ScreenRow, source: str) -> ShortlistItem:
        score, factors, notes = scored.get(row.key, (None, {}, []))
        f = row.fundamentals
        return ShortlistItem(
            key=row.key, name=row.name, kind="etf" if row.is_etf else "share",
            sector=_sector(row), industry=f.industry if f else None, score=score,
            factors=factors, metrics=metrics(row, base_currency, fx), source=source,
            filter_notes=[reason for _, reason in failures.get(row.key, [])] + notes,
            failed=[name for name, _ in failures.get(row.key, [])],
            summary=(f.summary or "")[:300] or None if f else None)

    # Highest score first; ties broken by ticker so every run gives the same list.
    ranked = sorted(passing, key=lambda r: (-scored[r.key][0], r.key))
    chosen, per_sector = [], {}
    for r in ranked:
        if len(chosen) >= SHORTLIST_SIZE:
            break
        sector = _sector(r)
        if per_sector.get(sector, 0) < MAX_PER_SECTOR:
            chosen.append(item(r, r.source))
            per_sector[sector] = per_sector.get(sector, 0) + 1

    # Held direct shares and watchlist items always go through, with their reasons.
    picked = {i.key for i in chosen}
    for key, source in [(k, "held") for k in sorted(held)] + \
                       [(k, "watchlist") for k in sorted(watchlist - held)]:
        row = rows.get(key) or ScreenRow(Candidate(key[0], key[1], None, "share", None, None))
        if source == "held" and row.is_etf and key not in watchlist:
            continue  # ETFs you hold are the rules recommender's job
        if key in picked:
            for i in chosen:
                if i.key == key:
                    i.source = source
            continue
        if key not in rows:
            failures[key] = [("data", "No market data found")]
        chosen.append(item(row, source))
        picked.add(key)

    # At most MAX_ROWS: drop the lowest-scoring screened rows first.
    while len(chosen) > MAX_ROWS:
        screened = [i for i in chosen if i.source in ("screen", "universe")]
        if not screened:
            break
        chosen.remove(min(screened, key=lambda i: (i.score or 0, i.key)))
    counts["shortlisted"] = len(chosen)
    return Shortlist(str(goal.category.id), chosen, counts)
