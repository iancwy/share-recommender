"""Rules-based recommender. Pure functions: every input is passed in, nothing is fetched.

For one goal category it works out:

1. a **target mix**: how much in growth assets (shares) versus defensive assets (bonds,
   cash), from the category's risk level and time horizon and the person's answers, then
   split into asset classes;
2. **picks**: an ETF for each slice, plus individual shares for part of the shares
   portion if the person wants them;
3. **gaps**: what the category holds now against that mix;
4. a **buy plan**: whole units to buy with new money, moving towards the mix without selling.

A **speculative** goal skips step 1: it is all in shares, spread over a few high-growth
companies and theme ETFs in the themes the person chose.

Every step records plain-English reasons so the result can be explained.
"""

import math
from datetime import date
from decimal import ROUND_FLOOR, Decimal
from uuid import UUID

from pydantic import BaseModel

from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.models import Category, Holding, RiskLevel
from share_recommender.core.preferences import (
    CategoryPreferences,
    DirectShares,
    DrawdownReaction,
    Experience,
    Flexibility,
    GoalStyle,
    HomeBias,
    IncomeGrowth,
    InvestorPreferences,
)
from share_recommender.core.universe import (
    ASSET_CLASS_LABELS,
    ETFS,
    EXCLUSION_LABELS,
    SHARES,
    SPECULATIVE_SHARES,
    THEME_LABELS,
    AssetClass,
    EtfStyle,
    Instrument,
    Kind,
    Theme,
    classify,
    lookup,
)
from share_recommender.data.base import Fundamentals, Quote

DEFAULT_HORIZON_YEARS = 10
BASE_GROWTH = {RiskLevel.LOW: 30, RiskLevel.MEDIUM: 70, RiskLevel.HIGH: 90}
TOLERANCE_ADJUST = {DrawdownReaction.SELL_ALL: -20, DrawdownReaction.SELL_SOME: -10,
                    DrawdownReaction.HOLD: 0, DrawdownReaction.BUY_MORE: 10}
FLEXIBILITY_ADJUST = {Flexibility.MUST_MEET: -10, Flexibility.FLEXIBLE: 0,
                      Flexibility.NICE_TO_HAVE: 5}
# (horizon below this many years, most growth allowed in %)
HORIZON_CAPS = [(1, 0), (2, 10), (3, 30), (5, 50), (7, 70), (10, 85)]
AU_SHARE_OF_DEVELOPED = {HomeBias.MOSTLY_AUSTRALIAN: Decimal("0.5"),
                         HomeBias.BALANCED: Decimal("0.35"),
                         HomeBias.MOSTLY_INTERNATIONAL: Decimal("0.2")}
EMERGING_SHARE_OF_GROWTH = Decimal("0.1")
EMERGING_MIN_GROWTH = 60
DIRECT_FRACTION = {DirectShares.ETF_ONLY: Decimal(0), DirectShares.SOME: Decimal("0.25"),
                   DirectShares.MIX: Decimal("0.5")}
BEGINNER_DIRECT_CAP = Decimal("0.25")
MAX_SHARES_PER_SLICE = 5
MIN_SHARE_WEIGHT = Decimal(2)  # % of the category; smaller positions aren't worth the brokerage
MIN_TRADE = Decimal(500)       # also the ASX minimum for a first purchase of a stock
MAX_BROKERAGE_RATIO = Decimal("0.02")
SPECULATIVE_ETF_FRACTION = Decimal("0.3")  # of a speculative pot, when it also holds shares
SPECULATIVE_MAX_ETFS = 2                   # 3 when the person wants ETFs only
SPECULATIVE_MAX_SHARES = 6
SPECULATIVE_MIN_HORIZON = 5
SPECULATIVE_MAX_PORTFOLIO_PCT = Decimal(10)
CENT = Decimal("0.01")


# Results ----------------------------------------------------------------------

class Slice(BaseModel):
    asset_class: AssetClass
    label: str
    weight: Decimal  # % of the category


class Pick(BaseModel):
    ticker: str
    exchange: str
    name: str
    kind: Kind
    asset_class: AssetClass
    currency: str
    weight: Decimal  # % of the category
    reason: str


class TargetMix(BaseModel):
    growth_pct: int
    horizon_years: Decimal
    slices: list[Slice]
    picks: list[Pick]
    reasons: list[str]
    warnings: list[str]
    speculative: bool = False


class SliceGap(BaseModel):
    key: str  # an AssetClass value
    label: str
    target_pct: Decimal
    current_pct: Decimal
    current_value: Decimal
    target_value: Decimal
    difference: Decimal  # positive = underweight (buy more), negative = overweight


class Gaps(BaseModel):
    base_currency: str
    total_value: Decimal
    slices: list[SliceGap]
    notes: list[str]


class BuyOrder(BaseModel):
    ticker: str
    exchange: str
    name: str
    units: int
    price: Decimal
    currency: str
    brokerage: Decimal         # base currency
    total_cost: Decimal        # base currency, including brokerage


class BuyPlan(BaseModel):
    amount: Decimal
    base_currency: str
    orders: list[BuyOrder]
    leftover: Decimal
    notes: list[str]


class Recommendation(BaseModel):
    category_id: UUID
    category_name: str
    mix: TargetMix
    gaps: Gaps
    buy_plan: BuyPlan | None = None


# Target mix -------------------------------------------------------------------

def horizon_years(category: Category, prefs: CategoryPreferences, today: date) -> Decimal:
    if category.target_date:
        days = (category.target_date - today).days
        return max(Decimal(0), Decimal(days) / Decimal("365.25")).quantize(Decimal("0.1"))
    if prefs.horizon_years is not None:
        return Decimal(prefs.horizon_years)
    return Decimal(DEFAULT_HORIZON_YEARS)


def _horizon_cap(years: Decimal) -> int:
    return next((cap for limit, cap in HORIZON_CAPS if years < limit), 100)


def growth_percent(category: Category, inv: InvestorPreferences, prefs: CategoryPreferences,
                   years: Decimal) -> tuple[int, list[str]]:
    base = BASE_GROWTH[category.risk_level]
    reasons = [f"A {category.risk_level.value}-risk goal starts at {base}% in shares."]
    adjust = TOLERANCE_ADJUST[inv.drawdown_reaction] + FLEXIBILITY_ADJUST[prefs.flexibility]
    if TOLERANCE_ADJUST[inv.drawdown_reaction]:
        reasons.append(f"Your answer about a 20% fall moves that by "
                       f"{TOLERANCE_ADJUST[inv.drawdown_reaction]:+d} points.")
    if FLEXIBILITY_ADJUST[prefs.flexibility]:
        reasons.append(f"How much this goal can flex moves it by "
                       f"{FLEXIBILITY_ADJUST[prefs.flexibility]:+d} points.")
    cap = _horizon_cap(years)
    growth = max(0, min(base + adjust, cap))
    if base + adjust > cap:
        reasons.append(f"With about {years:g} years to go, shares are capped at {cap}% "
                       "so a market fall near the deadline can't do too much damage.")
    growth = int(5 * round(growth / 5))
    return growth, reasons


def asset_mix(growth: int, years: Decimal, inv: InvestorPreferences
              ) -> tuple[list[Slice], list[str]]:
    g = Decimal(growth)
    reasons = []
    emerging = (g * EMERGING_SHARE_OF_GROWTH) if growth >= EMERGING_MIN_GROWTH else Decimal(0)
    if emerging:
        reasons.append("A small emerging markets slice adds diversification for a growth-heavy "
                       "mix.")
    developed = g - emerging
    au = developed * AU_SHARE_OF_DEVELOPED[inv.home_bias]
    global_ = developed - au
    if growth:
        reasons.append(f"Of the developed-market shares, "
                       f"{AU_SHARE_OF_DEVELOPED[inv.home_bias] * 100:g}% is Australian, from "
                       "your home-market preference.")

    defensive = 100 - g
    cash_fraction = Decimal(1) if years < 2 else Decimal("0.4") if years < 5 else Decimal("0.2")
    cash = defensive * cash_fraction
    bonds = defensive - cash
    if defensive:
        reasons.append("The defensive part is mostly cash when the money is needed soon, and "
                       "mostly bonds for longer horizons.")

    raw = {AssetClass.AU_SHARES: au, AssetClass.GLOBAL_SHARES: global_,
           AssetClass.EMERGING_SHARES: emerging, AssetClass.BONDS: bonds,
           AssetClass.CASH: cash}
    weights = _round_to_100({k: v for k, v in raw.items() if v > 0})
    slices = [Slice(asset_class=k, label=ASSET_CLASS_LABELS[k], weight=w)
              for k, w in weights.items()]
    return slices, reasons


def _round_to_100(weights: dict) -> dict:
    """Round to 0.5% steps, then fix any drift on the largest weight so they total 100."""
    out = {k: (v * 2).quantize(Decimal(1)) / 2 for k, v in weights.items()}
    if out:
        largest = max(out, key=out.get)
        out[largest] += Decimal(100) - sum(out.values())
    return out


# Picks ------------------------------------------------------------------------

def _market(exchange: str) -> str:
    return "US" if exchange in {"NYSE", "NASDAQ"} else exchange


def allowed(inst: Instrument, inv: InvestorPreferences) -> bool:
    return (inst.exchange in inv.markets and not (inst.excluded_by & set(inv.exclusions))
            and inst.ticker not in inv.avoid_tickers)


def share_candidates(inv: InvestorPreferences) -> list[Instrument]:
    """Shares the recommender may pick, so fundamentals can be fetched for just these."""
    if inv.direct_shares is DirectShares.ETF_ONLY:
        return []
    return [s for s in SHARES if allowed(s, inv)]


def _direct_fraction(inv: InvestorPreferences) -> Decimal:
    f = DIRECT_FRACTION[inv.direct_shares]
    if inv.experience is Experience.BEGINNER:
        f = min(f, BEGINNER_DIRECT_CAP)
    return f


def _choose_etf(asset_class: AssetClass, inv: InvestorPreferences,
                held: set[tuple[str, str]]) -> tuple[Instrument | None, str]:
    options = [e for e in ETFS if e.asset_class is asset_class and allowed(e, inv)
               and e.style not in {EtfStyle.THEMATIC, EtfStyle.DIVERSIFIED}]
    label = ASSET_CLASS_LABELS[asset_class].lower().replace("australian", "Australian")
    if not options:
        return None, ""
    for e in options:
        if e.key in held:
            return e, "You already hold it in this category, so adding to it keeps things simple."
    if inv.exclusions:
        for e in options:
            if e.style is EtfStyle.SUSTAINABLE:
                return e, ("A sustainability-screened ETF, because you asked to avoid some "
                           "industries. Check its exclusion policy matches yours.")
    if inv.income_growth is IncomeGrowth.INCOME:
        for e in options:
            if e.style is EtfStyle.INCOME:
                return e, f"Tilts {label} towards higher dividend payers, for your income focus."
    for e in options:
        if e.style is EtfStyle.BROAD:
            return e, f"A low-cost, broadly diversified fund for the {label} slice."
    e = options[0]
    return e, (f"The broadest {label} fund on your chosen markets, though it covers only one "
               "country's market.")


def _rank_shares(candidates: list[Instrument], inv: InvestorPreferences,
                 fundamentals: dict[tuple[str, str], Fundamentals | None]) -> list[Instrument]:
    """Order by preference, interleaving markets so no single country dominates."""
    def score(s: Instrument) -> Decimal:
        f = fundamentals.get(s.key)
        return f.dividend_yield if f and f.dividend_yield is not None else Decimal(-1)

    groups: dict[str, list[Instrument]] = {}
    for s in candidates:
        groups.setdefault(_market(s.exchange), []).append(s)
    if inv.income_growth is IncomeGrowth.INCOME and fundamentals:
        for g in groups.values():
            g.sort(key=score, reverse=True)  # stable, so curated order breaks ties
    market_order = list(dict.fromkeys(_market(m) for m in inv.markets))
    ordered = []
    for i in range(max((len(g) for g in groups.values()), default=0)):
        for m in market_order:
            if i < len(groups.get(m, [])):
                ordered.append(groups[m][i])
    return ordered


def _choose_shares(ranked: list[Instrument], n: int) -> list[Instrument]:
    """Take the first n, one per sector first so the picks are spread out."""
    chosen, sectors = [], set()
    for s in ranked:
        if len(chosen) < n and s.sector not in sectors:
            chosen.append(s)
            sectors.add(s.sector)
    for s in ranked:
        if len(chosen) < n and s not in chosen:
            chosen.append(s)
    return chosen


def select_picks(slices: list[Slice], inv: InvestorPreferences, held: set[tuple[str, str]],
                 fundamentals: dict[tuple[str, str], Fundamentals | None],
                 ) -> tuple[list[Pick], list[str], list[str]]:
    picks, reasons, warnings = [], [], []
    direct = _direct_fraction(inv)
    if inv.experience is Experience.BEGINNER and DIRECT_FRACTION[inv.direct_shares] > direct:
        reasons.append("As you're newer to investing, individual shares are kept to a quarter "
                       "of the shares portion.")
    if inv.exclusions:
        names = ", ".join(EXCLUSION_LABELS[x].lower() for x in inv.exclusions)
        reasons.append(f"Individual shares in {names} are left out. Broad index ETFs still "
                       "hold some of these companies unless they are screened.")
    if inv.avoid_tickers:
        reasons.append(f"{', '.join(inv.avoid_tickers)} are never picked, as you asked.")
    income_ranked = inv.income_growth is IncomeGrowth.INCOME and any(fundamentals.values())

    def pick(inst: Instrument, weight: Decimal, reason: str) -> Pick:
        return Pick(ticker=inst.ticker, exchange=inst.exchange, name=inst.name, kind=inst.kind,
                    asset_class=inst.asset_class, currency=EXCHANGES[inst.exchange].currency,
                    weight=weight, reason=reason)

    for sl in slices:
        shares: list[Instrument] = []
        if direct and sl.asset_class in {AssetClass.AU_SHARES, AssetClass.GLOBAL_SHARES}:
            pool = [s for s in SHARES if s.asset_class is sl.asset_class and allowed(s, inv)]
            n = min(MAX_SHARES_PER_SLICE, math.floor(sl.weight * direct / MIN_SHARE_WEIGHT))
            shares = _choose_shares(_rank_shares(pool, inv, fundamentals), n) if n else []
            if not shares and pool:
                reasons.append(f"{sl.label} are too small a slice here for individual shares to "
                               "be worthwhile, so it's all in an ETF.")
            elif not pool and sl.asset_class is AssetClass.GLOBAL_SHARES:
                reasons.append("None of your chosen markets outside the ASX have individual "
                               "shares on the list, so international shares are ETF only.")

        etf, why = _choose_etf(sl.asset_class, inv, held)
        if etf is None and not shares:
            warnings.append(f"No {sl.label.lower()} investment on your chosen markets, so "
                            f"this {sl.weight:g}% slice can't be filled. Add a market such "
                            "as the ASX in your preferences.")
            continue
        # With no ETF on these markets, the shares take the whole slice.
        share_weight = sl.weight * direct if etf is not None else sl.weight
        each = (share_weight / len(shares)).quantize(Decimal("0.1")) if shares else Decimal(0)
        if etf is not None:
            picks.append(pick(etf, sl.weight - each * len(shares), why))
        if shares:
            why_share = ("Among the higher dividend yields on the list, picked one per sector "
                         "where possible." if income_ranked else
                         "One of the largest companies on the list, picked one per sector "
                         "where possible.")
            for s in shares:
                held_note = " You already hold it." if s.key in held else ""
                picks.append(pick(s, each, why_share + held_note))
    return picks, reasons, warnings


# Speculative goals ------------------------------------------------------------

def is_speculative(category: Category, prefs: CategoryPreferences) -> bool:
    if prefs.style is not None:
        return prefs.style is GoalStyle.SPECULATIVE
    return category.name.strip().lower() == "speculative"


def _interleave(groups: list[list[Instrument]]) -> list[Instrument]:
    """Take one from each group in turn, skipping repeats, so every theme gets a look-in."""
    out: list[Instrument] = []
    for i in range(max((len(g) for g in groups), default=0)):
        for g in groups:
            if i < len(g) and g[i] not in out:
                out.append(g[i])
    return out


def speculative_mix(inv: InvestorPreferences, prefs: CategoryPreferences, years: Decimal,
                    held: set[tuple[str, str]]) -> TargetMix:
    themes = prefs.themes or [Theme.AI]
    names = ", ".join(THEME_LABELS[t] for t in themes)
    reasons = [
        f"This is a speculative goal, so it is all in high-growth shares with no bonds or "
        f"cash, focused on: {names}.",
        "Expect big swings: companies like these can rise or fall by half in a year, and some "
        "fail. Only put money here you could lose without changing your plans.",
    ]
    warnings = []
    if years < SPECULATIVE_MIN_HORIZON:
        warnings.append(f"You need this money in about {years:g} years. Speculative shares can "
                        f"take years to recover from a fall, so money needed within "
                        f"{SPECULATIVE_MIN_HORIZON} years is safer in an ordinary goal.")
    if inv.experience is Experience.BEGINNER:
        warnings.append("As you're newer to investing, consider keeping this pot especially "
                        "small while you see how these shares behave.")
    if inv.exclusions or inv.avoid_tickers:
        reasons.append("Companies in the industries, and any tickers, you asked to avoid are "
                       "left out.")

    def held_first(items: list[Instrument]) -> list[Instrument]:
        return sorted(items, key=lambda i: i.key not in held)  # stable: curated order otherwise

    etf_only = inv.direct_shares is DirectShares.ETF_ONLY
    etfs = _interleave([held_first([e for e in ETFS if e.style is EtfStyle.THEMATIC
                                    and t in e.themes and allowed(e, inv)]) for t in themes])
    etfs = etfs[:SPECULATIVE_MAX_ETFS + (1 if etf_only else 0)]
    # Within each theme, alternate between markets so smaller ASX names get a look-in too.
    market_order = list(dict.fromkeys(_market(m) for m in inv.markets))

    def by_market(items: list[Instrument]) -> list[Instrument]:
        return _interleave([[i for i in items if _market(i.exchange) == m]
                            for m in market_order])

    shares = [] if etf_only else _interleave(
        [held_first(by_market([s for s in SHARES + SPECULATIVE_SHARES
                               if t in s.themes and allowed(s, inv)]))
         for t in themes])[:SPECULATIVE_MAX_SHARES]
    if etf_only:
        reasons.append("You chose ETFs only, so the themes are covered by theme ETFs rather "
                       "than individual companies.")
    elif not shares:
        reasons.append("None of your chosen markets have individual companies in these themes "
                       "on the list, so it's ETFs only.")
    if not etfs and not shares:
        warnings.append("Nothing on the list matches these themes on your chosen markets. Add "
                        "the ASX or a US exchange in your preferences, or choose other themes.")
        return TargetMix(growth_pct=100, horizon_years=years, slices=[], picks=[],
                         reasons=reasons, warnings=warnings, speculative=True)

    etf_total = Decimal(100) * (SPECULATIVE_ETF_FRACTION if shares else 1) if etfs else Decimal(0)
    weights = [(e, etf_total / len(etfs)) for e in etfs]
    weights += [(s, (100 - etf_total) / len(shares)) for s in shares]
    weights = [(i, w.quantize(Decimal("0.1"))) for i, w in weights]
    first, w0 = weights[0]
    weights[0] = (first, w0 + Decimal(100) - sum(w for _, w in weights))
    if shares:
        reasons.append(f"Individual companies get equal weights of about {weights[-1][1]:g}% "
                       "each, so no single bet dominates.")

    def theme_of(i: Instrument) -> str:
        return THEME_LABELS[next(t for t in themes if t in i.themes)]

    picks = []
    for inst, w in weights:
        if inst.kind is Kind.ETF:
            why = (f"A theme ETF holding many {theme_of(inst)} companies, which softens the "
                   "blow if any one of them fails.")
        else:
            why = f"A company in the {theme_of(inst)} theme." + (
                " You already hold it." if inst.key in held else "")
        picks.append(Pick(ticker=inst.ticker, exchange=inst.exchange, name=inst.name,
                          kind=inst.kind, asset_class=inst.asset_class,
                          currency=EXCHANGES[inst.exchange].currency, weight=w, reason=why))
    by_class: dict[AssetClass, Decimal] = {}
    for p in picks:
        by_class[p.asset_class] = by_class.get(p.asset_class, Decimal(0)) + p.weight
    slices = [Slice(asset_class=ac, label=ASSET_CLASS_LABELS[ac], weight=w)
              for ac, w in by_class.items()]
    return TargetMix(growth_pct=100, horizon_years=years, slices=slices, picks=picks,
                     reasons=reasons, warnings=warnings, speculative=True)


def portfolio_share_warning(category_value: Decimal, portfolio_value: Decimal,
                            amount: Decimal = Decimal(0)) -> str | None:
    """Warn when a speculative pot is more than a small slice of everything the person holds."""
    total = portfolio_value + amount
    if not total:
        return None
    pct = (category_value + amount) / total * 100
    if pct <= SPECULATIVE_MAX_PORTFOLIO_PCT:
        return None
    after = " after this purchase" if amount else ""
    return (f"This speculative pot would be {pct:.0f}% of all your holdings{after}. Many "
            f"investors keep speculative bets to {SPECULATIVE_MAX_PORTFOLIO_PCT:g}% or less.")


def target_mix(category: Category, inv: InvestorPreferences, prefs: CategoryPreferences,
               today: date, held: set[tuple[str, str]] = frozenset(),
               fundamentals: dict[tuple[str, str], Fundamentals | None] | None = None,
               ) -> TargetMix:
    years = horizon_years(category, prefs, today)
    if is_speculative(category, prefs):
        return speculative_mix(inv, prefs, years, set(held))
    warnings = []
    if category.target_date and category.target_date <= today:
        warnings.append("The target date has passed, so everything is kept in cash. Update the "
                        "date if the goal is still running.")
    growth, reasons = growth_percent(category, inv, prefs, years)
    slices, mix_reasons = asset_mix(growth, years, inv)
    picks, pick_reasons, pick_warnings = select_picks(slices, inv, set(held), fundamentals or {})
    return TargetMix(growth_pct=growth, horizon_years=years, slices=slices, picks=picks,
                     reasons=reasons + mix_reasons + pick_reasons,
                     warnings=warnings + pick_warnings)


# Gaps -------------------------------------------------------------------------

def to_base(amount: Decimal, currency: str, base: str, fx: dict[str, Decimal | None]
            ) -> Decimal | None:
    """Convert using ``fx[currency]`` = units of that currency per 1 unit of base currency."""
    if currency == base:
        return amount
    rate = fx.get(currency)
    return amount / rate if rate else None


def holding_values(holdings: list[Holding], quotes: dict[tuple[str, str], Quote | None],
                   fx: dict[str, Decimal | None], base: str
                   ) -> tuple[dict[tuple[str, str], Decimal], list[str]]:
    """Current value of each (ticker, exchange) in base currency, plus notes on estimates."""
    values: dict[tuple[str, str], Decimal] = {}
    notes = []
    for h in holdings:
        q = quotes.get((h.ticker, h.exchange))
        local = h.units * q.price if q else h.cost_base
        if q is None:
            notes.append(f"{h.ticker} has no current price, so it is counted at what you paid.")
        value = to_base(local, h.currency, base, fx)
        if value is None:
            notes.append(f"No {h.currency} exchange rate, so {h.ticker} is left out of the totals.")
            continue
        values[(h.ticker, h.exchange)] = values.get((h.ticker, h.exchange), Decimal(0)) + value
    return values, list(dict.fromkeys(notes))


def by_asset_class(values: dict[tuple[str, str], Decimal]) -> dict[AssetClass, Decimal]:
    """Value in each asset class, splitting diversified funds across their classes."""
    out: dict[AssetClass, Decimal] = {}
    for key, v in values.items():
        for ac, frac in classify(*key).items():
            out[ac] = out.get(ac, Decimal(0)) + v * frac
    return out


def analyse_gaps(mix: TargetMix, values: dict[tuple[str, str], Decimal], base: str,
                 notes: list[str] | None = None) -> Gaps:
    total = sum(values.values(), Decimal(0))
    current = {ac.value: v for ac, v in by_asset_class(values).items()}

    def pct(v: Decimal) -> Decimal:
        return (v / total * 100).quantize(Decimal("0.1")) if total else Decimal(0)

    rows = []
    for sl in mix.slices:
        cur = current.get(sl.asset_class.value, Decimal(0))
        target_value = (total * sl.weight / 100).quantize(CENT)
        rows.append(SliceGap(key=sl.asset_class.value, label=sl.label, target_pct=sl.weight,
                             current_pct=pct(cur), current_value=cur.quantize(CENT),
                             target_value=target_value,
                             difference=(target_value - cur).quantize(CENT)))
    for key, cur in current.items():
        if key not in {s.asset_class.value for s in mix.slices}:
            rows.append(SliceGap(key=key, label=ASSET_CLASS_LABELS[AssetClass(key)],
                                 target_pct=Decimal(0), current_pct=pct(cur),
                                 current_value=cur.quantize(CENT), target_value=Decimal(0),
                                 difference=-cur.quantize(CENT)))
    gap_notes = list(notes or [])
    unknown = sorted({t for t, ex in values if lookup(t, ex) is None})
    if unknown:
        gap_notes.append(f"{', '.join(unknown)} aren't on the recommender's list, so each is "
                         "counted as one company's shares (Australian if on the ASX).")
    if any((i := lookup(*k)) and i.split for k in values):
        gap_notes.append("Diversified funds are split across asset classes by their "
                         "approximate target allocation.")
    return Gaps(base_currency=base, total_value=total.quantize(CENT), slices=rows, notes=gap_notes)


# Buy plan ---------------------------------------------------------------------

def plan_purchase(mix: TargetMix, values: dict[tuple[str, str], Decimal], amount: Decimal,
                  quotes: dict[tuple[str, str], Quote | None], fx: dict[str, Decimal | None],
                  base: str, brokerage: Decimal = Decimal(0)) -> BuyPlan:
    """Split new money across the picks to close the biggest gaps first. Never sells."""
    notes: list[str] = []
    if not mix.picks:
        return BuyPlan(amount=amount, base_currency=base, orders=[], leftover=amount,
                       notes=["Nothing to buy until this goal has suggested investments."])
    picks = []
    for p in mix.picks:
        q, rate_ok = quotes.get((p.ticker, p.exchange)), to_base(Decimal(1), p.currency, base, fx)
        if q is None or rate_ok is None:
            notes.append(f"{p.ticker} is skipped: no current price or exchange rate.")
        else:
            picks.append(p)

    total_after = sum(values.values(), Decimal(0)) + amount
    slice_current = by_asset_class(values)

    # How far each pick is below its share of the slice's target, after the new money.
    want: dict[tuple[str, str], Decimal] = {}
    for sl in mix.slices:
        in_slice = [p for p in picks if p.asset_class is sl.asset_class]
        if not in_slice:
            continue
        slice_short = max(Decimal(0), total_after * sl.weight / 100
                          - slice_current.get(sl.asset_class, Decimal(0)))
        pick_short = {(p.ticker, p.exchange): max(Decimal(0), total_after * p.weight / 100
                                                  - values.get((p.ticker, p.exchange), Decimal(0)))
                      for p in in_slice}
        total_short = sum(pick_short.values(), Decimal(0))
        for key, short in pick_short.items():
            if total_short:
                want[key] = slice_short * short / total_short
    total_want = sum(want.values(), Decimal(0))
    alloc = {k: amount * v / total_want for k, v in want.items() if v > 0} if total_want else {}

    # Drop trades too small to be worth it and share their money among the rest.
    min_trade = max(MIN_TRADE, brokerage / MAX_BROKERAGE_RATIO)
    dropped = []
    while alloc:
        smallest = min(alloc, key=alloc.get)
        if alloc[smallest] >= min_trade:
            break
        freed = alloc.pop(smallest)
        dropped.append(smallest[0])
        rest = sum(alloc.values(), Decimal(0))
        for k in alloc:
            alloc[k] += freed * alloc[k] / rest
    if dropped:
        notes.append(f"Left out {', '.join(dropped)} this time: each would be under "
                     f"{min_trade:,.0f} {base}, too small to be worth the brokerage. Buy them "
                     "with a later top-up.")
    if not alloc and amount > 0:
        notes.append(f"The amount is too small to buy anything worthwhile (minimum trade "
                     f"{min_trade:,.0f} {base}).")

    by_key = {(p.ticker, p.exchange): p for p in picks}
    orders: dict[tuple[str, str], BuyOrder] = {}
    for key, budget in alloc.items():
        p, q = by_key[key], quotes[key]
        unit_cost = to_base(q.price, p.currency, base, fx)
        units = int(((budget - brokerage) / unit_cost).to_integral_value(ROUND_FLOOR))
        if units > 0:
            orders[key] = _order(p, q.price, units, unit_cost, brokerage)
        else:
            notes.append(f"{p.ticker} is left out this time: one unit costs about "
                         f"{unit_cost:,.0f} {base}, more than its share of this amount.")

    # Spend what's left on whole units, most underweight first.
    leftover = amount - sum((o.total_cost for o in orders.values()), Decimal(0))
    changed = True
    while changed:
        changed = False
        for key in sorted(orders, key=lambda k: values.get(k, Decimal(0))
                          + orders[k].total_cost - total_after * by_key[k].weight / 100):
            unit_cost = to_base(quotes[key].price, by_key[key].currency, base, fx)
            if unit_cost <= leftover:
                o = orders[key]
                orders[key] = _order(by_key[key], o.price, o.units + 1, unit_cost, brokerage)
                leftover -= unit_cost
                changed = True
                break
    leftover = amount - sum((o.total_cost for o in orders.values()), Decimal(0))

    if any(p.currency != base for p in picks if (p.ticker, p.exchange) in orders):
        notes.append("Costs for overseas shares use today's exchange rate; your broker's rate "
                     "and any currency fee will differ a little.")
    return BuyPlan(amount=amount, base_currency=base, orders=list(orders.values()),
                   leftover=leftover.quantize(CENT), notes=notes)


def _order(p: Pick, price: Decimal, units: int, unit_cost: Decimal, brokerage: Decimal
           ) -> BuyOrder:
    return BuyOrder(ticker=p.ticker, exchange=p.exchange, name=p.name, units=units, price=price,
                    currency=p.currency, brokerage=brokerage,
                    total_cost=(units * unit_cost + brokerage).quantize(CENT))
