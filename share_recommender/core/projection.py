"""Projections: what a portfolio, a goal or one holding could be worth, as a range. Pure.

Every projection starts from a list of **assets**, each with a value today, a yearly return
and a yearly volatility (how much it goes up and down). The returns and volatilities below are
**assumptions, not forecasts**: long-run averages by default, or each holding's own past
prices (capped) if the person chooses "Past performance". The returns are the Dashboard's
``ASSUMED_RETURN``, so the base case here and the Dashboard's steady-return figure agree.
"""

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

import numpy as np

from share_recommender.core.performance import ASSUMED_RETURN, CashValue
from share_recommender.core.universe import (
    SPECULATIVE_SHARES,
    AssetClass,
    EtfStyle,
    Kind,
    classify,
    lookup,
)
from share_recommender.data.base import PriceBar

D = Decimal

# Long-run assumptions: yearly volatility by asset class (returns are ASSUMED_RETURN).
# Assumptions, not forecasts. Nominal, before fees and tax, with dividends reinvested.
CLASS_VOLATILITY = {
    AssetClass.AU_SHARES: D("0.15"),
    AssetClass.GLOBAL_SHARES: D("0.15"),
    AssetClass.EMERGING_SHARES: D("0.20"),
    AssetClass.CASH: D("0.01"),
}
# Individual shares: their own volatility over the last few years, kept within a range.
SHARE_VOLATILITY = (D("0.15"), D("0.60"), D("0.30"))        # (lowest, highest, if too new)
SPECULATIVE_VOLATILITY = (D("0.20"), D("0.80"), D("0.40"))  # speculative shares, theme ETFs
VOLATILITY_YEARS = 3

# "Past performance": each holding's own record, within limits.
PAST_MIN_YEARS = 3
PAST_MAX_YEARS = 10
PAST_RETURN_FLOOR = D("0")
PAST_RETURN_CAP = D("0.12")

# One correlation between any two share or share-ETF assets; cash funds move on their own.
CORRELATION = D("0.7")

_SPECULATIVE = {s.key for s in SPECULATIVE_SHARES}


@dataclass(frozen=True)
class Asset:
    key: str                  # "VAS:ASX", or "cash:<account id>"
    label: str                # "VAS", "ING Savings Maximiser"
    value: Decimal            # base currency, today
    expected_return: Decimal  # yearly, fraction (0.07)
    volatility: Decimal       # yearly, fraction (0.15); 0 for savings accounts
    is_cash: bool             # savings accounts grow at their rate, never simulated
    source: str               # plain-English note: "Long-run average for Australian shares"
    is_cash_fund: bool = False       # a cash ETF such as AAA: not correlated with shares
    weight: Decimal | None = None    # mix weight when nothing is held yet (an empty goal)


def _pct(x: Decimal) -> str:
    return f"{x * 100:.1f}%"


# Volatility from monthly closes -------------------------------------------------------

def month_end_closes(bars: list[PriceBar]) -> list[PriceBar]:
    """The last close in each calendar month, oldest first (bad closes skipped)."""
    by_month: dict[tuple[int, int], PriceBar] = {}
    for b in sorted(bars, key=lambda b: b.day):
        if b.close is not None and b.close.is_finite() and b.close > 0:
            by_month[(b.day.year, b.day.month)] = b
    return [by_month[k] for k in sorted(by_month)]


def _log_returns(closes: list[PriceBar]) -> list[float]:
    return [math.log(float(b.close / a.close)) for a, b in zip(closes, closes[1:], strict=False)]


def _yearly_sd(monthly: list[float]) -> Decimal:
    mean = sum(monthly) / len(monthly)
    var = sum((r - mean) ** 2 for r in monthly) / (len(monthly) - 1)
    return D(str(round(math.sqrt(var) * math.sqrt(12), 4)))


def volatility(bars: list[PriceBar], years: int = VOLATILITY_YEARS) -> Decimal | None:
    """Yearly volatility from the last ``years`` of month-end closes: the standard deviation
    of the monthly log returns times √12. None with fewer than 12 monthly returns."""
    closes = month_end_closes(bars)[-(years * 12 + 1):]
    monthly = _log_returns(closes)
    return _yearly_sd(monthly) if len(monthly) >= 12 else None


def _clamp(x: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, x))


# Assumptions for one holding ------------------------------------------------------------

def _class_label(ac: AssetClass) -> str:
    return {AssetClass.AU_SHARES: "Australian shares", AssetClass.GLOBAL_SHARES:
            "international shares", AssetClass.EMERGING_SHARES: "emerging markets shares",
            AssetClass.CASH: "cash"}[ac]


def _average_return(mix: dict[AssetClass, Decimal]) -> Decimal:
    return sum((frac * (ASSUMED_RETURN["cash"] if ac is AssetClass.CASH
                        else ASSUMED_RETURN["growth"]) for ac, frac in mix.items()), D(0))


def long_run(ticker: str, exchange: str, bars: list[PriceBar]
             ) -> tuple[Decimal, Decimal, str, bool]:
    """(return, volatility, note, is a cash fund) from the long-run averages."""
    inst = lookup(ticker, exchange)
    mix = classify(ticker, exchange)
    speculative = (ticker, exchange) in _SPECULATIVE or (
        inst is not None and inst.style is EtfStyle.THEMATIC)
    if speculative or inst is None or inst.kind is Kind.SHARE:
        low, high, default = SPECULATIVE_VOLATILITY if speculative else SHARE_VOLATILITY
        kind = "speculative" if speculative else "individual"
        own = volatility(bars)
        if own is None:
            return (ASSUMED_RETURN["growth"], default,
                    f"Long-run average return; less than a year of prices, so ups and downs "
                    f"of {_pct(default)} a year are assumed for a {kind} holding", False)
        vol = _clamp(own, low, high)
        note = f"Long-run average return; its own ups and downs over {VOLATILITY_YEARS} years"
        if vol != own:
            note += f" ({_pct(own)}, kept to {_pct(vol)} for a {kind} holding)"
        return ASSUMED_RETURN["growth"], vol, note, False
    vol = sum((frac * CLASS_VOLATILITY[ac] for ac, frac in mix.items()), D(0))
    if len(mix) > 1:
        note = "Long-run averages for its mix: " + ", ".join(
            f"{frac * 100:.0f}% {_class_label(ac)}" for ac, frac in mix.items())
    else:
        note = f"Long-run average for {_class_label(next(iter(mix)))}"
    is_cash_fund = set(mix) == {AssetClass.CASH}
    return _average_return(mix), vol, note, is_cash_fund


def past_performance(ticker: str, exchange: str, bars: list[PriceBar],
                     dividend_yield: Decimal | None) -> tuple[Decimal, Decimal, str, bool]:
    """(return, volatility, note, is a cash fund) from the holding's own record: price growth
    over up to 10 years plus its dividend yield, kept between 0% and 12% a year. Falls back
    to the long-run averages with less than 3 years of prices."""
    closes = month_end_closes(bars)[-(PAST_MAX_YEARS * 12 + 1):]
    months = len(closes) - 1
    if months < PAST_MIN_YEARS * 12:
        ret, vol, note, cash_fund = long_run(ticker, exchange, bars)
        return (ret, vol, f"Less than {PAST_MIN_YEARS} years of prices, so the long-run "
                          f"average is used. {note}", cash_fund)
    years = months / 12
    growth = D(str(round((float(closes[-1].close / closes[0].close)) ** (1 / years) - 1, 4)))
    total = growth + (dividend_yield or D(0))
    ret = _clamp(total, PAST_RETURN_FLOOR, PAST_RETURN_CAP)
    span = f"{years:.0f} years" if years >= 1 else "the period"
    note = f"Past {span}: {_pct(total)} a year"
    if dividend_yield:
        note += f" (prices {_pct(growth)} plus {_pct(dividend_yield)} dividends)"
    if ret == PAST_RETURN_CAP and total > ret:
        note += f", capped at {_pct(ret)} for this projection"
    elif ret == PAST_RETURN_FLOOR and total < ret:
        note += f", raised to {_pct(ret)} for this projection"
    cash_fund = long_run(ticker, exchange, bars)[3]
    vol = _yearly_sd(_log_returns(closes))
    inst = lookup(ticker, exchange)
    if inst is None or inst.kind is Kind.SHARE or inst.style is EtfStyle.THEMATIC:
        low, high, _ = (SPECULATIVE_VOLATILITY if (ticker, exchange) in _SPECULATIVE or (
            inst is not None and inst.style is EtfStyle.THEMATIC) else SHARE_VOLATILITY)
        vol = _clamp(vol, low, high)
    return ret, max(vol, D("0.01")), note, cash_fund


def holding_asset(ticker: str, exchange: str, value: Decimal, bars: list[PriceBar],
                  basis: str = "average", dividend_yield: Decimal | None = None,
                  label: str | None = None) -> Asset:
    if basis == "past":
        ret, vol, note, cash_fund = past_performance(ticker, exchange, bars, dividend_yield)
    else:
        ret, vol, note, cash_fund = long_run(ticker, exchange, bars)
    return Asset(f"{ticker}:{exchange}", label or ticker, value, ret, vol, False, note,
                 is_cash_fund=cash_fund)


def cash_asset(c: CashValue) -> Asset:
    a = c.account
    label = f"{a.institution} {a.account_name}" if a.account_name else a.institution
    return Asset(f"cash:{a.id}", label, c.value, c.rate, D(0), True,
                 f"Its interest rate, {a.interest_rate:g}% a year, with no ups and downs")


def target_mix_assets(growth_pct: int, why: str) -> list[Asset]:
    """Two stand-in assets for a goal with nothing in it yet: shares and cash in its target
    mix, so contributions have something to follow."""
    g = D(growth_pct) / 100
    note = (f"{why}, so it uses the goal's target mix: {growth_pct}% shares, "
            f"{100 - growth_pct}% cash")
    assets = []
    if g:
        assets.append(Asset("mix:shares", "Shares (target mix)", D(0), ASSUMED_RETURN["growth"],
                            CLASS_VOLATILITY[AssetClass.AU_SHARES], False, note, weight=g))
    if g < 1:
        assets.append(Asset("mix:cash", "Cash (target mix)", D(0), ASSUMED_RETURN["cash"],
                            CLASS_VOLATILITY[AssetClass.CASH], False, note, is_cash_fund=True,
                            weight=1 - g))
    return assets


# Combining the invested part -------------------------------------------------------------

def _weights(invested: list[Asset]) -> list[Decimal]:
    total = sum((a.value for a in invested), D(0))
    if total > 0:
        return [a.value / total for a in invested]
    w = [a.weight or D(0) for a in invested]
    s = sum(w, D(0))
    return [x / s for x in w] if s else [D(1) / len(invested)] * len(invested)


def combine(assets: list[Asset]) -> tuple[Decimal, Decimal]:
    """Expected return and volatility of the invested (non-savings) assets as one portfolio,
    weighted by value: σp² = Σᵢ Σⱼ wᵢ wⱼ ρᵢⱼ σᵢ σⱼ, with ρ = CORRELATION between shares or
    share ETFs, 0 with a cash fund and 1 on the diagonal."""
    invested = [a for a in assets if not a.is_cash]
    if not invested:
        return D(0), D(0)
    w = _weights(invested)
    mu = sum((wi * a.expected_return for wi, a in zip(w, invested, strict=True)), D(0))
    var = D(0)
    for i, a in enumerate(invested):
        for j, b in enumerate(invested):
            rho = (D(1) if i == j else D(0) if a.is_cash_fund or b.is_cash_fund
                   else CORRELATION)
            var += w[i] * w[j] * rho * a.volatility * b.volatility
    return mu.quantize(D("0.0001")), var.sqrt().quantize(D("0.0001"))


# The projection engine ----------------------------------------------------------------------

PATHS = 2000
SEED = 6
WORST_PCT, BASE_PCT, BEST_PCT = 10, 50, 90   # "1 in 10" worse, the middle, "1 in 10" better
INFLATION = D("0.025")                        # the RBA's target midpoint, for today's dollars
CHANCE_STEP, CHANCE_MIN, CHANCE_MAX = D(5), D(5), D(95)

PROJECTION_DISCLAIMER = (
    "Projections are illustrations, not forecasts. They use assumed returns and past prices, "
    "and real results can be much better or worse. Figures are before fees and tax, and in "
    "future dollars unless you chose today's dollars. This is general information, not "
    "personal financial advice.")


class Frequency(StrEnum):
    WEEKLY = "weekly"
    FORTNIGHTLY = "fortnightly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


PER_MONTH = {Frequency.WEEKLY: D(52) / 12, Frequency.FORTNIGHTLY: D(26) / 12,
             Frequency.MONTHLY: D(1)}


@dataclass(frozen=True)
class ContributionFlow:
    """A regular payment as the engine sees it (the saved row is ``models.Contribution``)."""

    amount: Decimal                  # base currency, each payment
    frequency: Frequency
    start: date
    end: date | None = None
    into_cash_rate: Decimal | None = None   # set when paid into a savings account (its rate)
    label: str | None = None


@dataclass(frozen=True)
class ProjectionPoint:
    month: date            # first of the month
    worst: Decimal         # whole dollars, base currency
    base: Decimal
    best: Decimal
    put_in: Decimal        # starting value plus contributions so far


@dataclass(frozen=True)
class Projection:
    points: list[ProjectionPoint]   # month 0 (today) to the horizon
    start_value: Decimal
    contributed: Decimal            # total contributions over the horizon
    expected_return: Decimal        # combined return of the invested part
    volatility: Decimal             # combined volatility
    chance_of_target: Decimal | None
    assets: list[Asset]
    notes: list[str]

    @property
    def final(self) -> ProjectionPoint:
        return self.points[-1]

    @property
    def growth(self) -> Decimal:
        """Base case at the end minus everything put in."""
        return self.final.base - self.final.put_in


def _month(d: date, add: int = 0) -> date:
    n = d.year * 12 + d.month - 1 + add
    return date(n // 12, n % 12 + 1, 1)


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def paid_in_month(c: ContributionFlow, month: date) -> Decimal:
    """What a contribution pays in the calendar month starting ``month``."""
    if month < _month(c.start) or (c.end is not None and month > _month(c.end)):
        return D(0)
    if c.frequency in PER_MONTH:
        return c.amount * PER_MONTH[c.frequency]
    since = _months_between(_month(c.start), month)
    step = 3 if c.frequency is Frequency.QUARTERLY else 12
    return c.amount if since % step == 0 else D(0)


def project(assets: list[Asset], contributions: list[ContributionFlow], *, today: date,
            years: int, target: Decimal | None = None, target_date: date | None = None,
            real: bool = False, paths: int = PATHS, seed: int = SEED) -> Projection:
    """Worst, base and best values month by month: ``paths`` simulated monthly paths for the
    invested part, savings at their own rates, contributions at the end of each month."""
    months = years * 12
    start = _month(today)
    mu, sigma = combine(assets)
    invested0 = float(sum((a.value for a in assets if not a.is_cash), D(0)))
    savings = [(float(a.value), float(a.expected_return)) for a in assets if a.is_cash]

    rng = np.random.default_rng(seed)
    mean = math.log(1 + float(mu)) / 12
    if sigma > 0:
        steps = rng.normal(mean, float(sigma) / math.sqrt(12), size=(paths, months))
    else:
        steps = np.full((paths, months), mean)
    growth = np.exp(steps)

    invested = np.empty((paths, months + 1))
    invested[:, 0] = invested0
    cash_total = np.empty(months + 1)
    cash_total[0] = sum(v for v, _ in savings)
    cash_balances = [v for v, _ in savings]
    into_cash: dict[float, float] = {}       # rate -> balance of contributions into savings
    put_in = np.empty(months + 1)
    put_in[0] = invested0 + cash_total[0]
    for k in range(1, months + 1):
        # A month's growth first, then that month's contributions at its end.
        cash_balances = [b * (1 + r) ** (1 / 12) for b, (_, r) in
                         zip(cash_balances, savings, strict=True)]
        into_cash = {r: b * (1 + r) ** (1 / 12) for r, b in into_cash.items()}
        to_invest = to_cash = 0.0
        for c in contributions:
            paid = float(paid_in_month(c, _month(start, k)))
            if c.into_cash_rate is not None:
                rate = float(c.into_cash_rate)
                into_cash[rate] = into_cash.get(rate, 0.0) + paid
                to_cash += paid
            else:
                to_invest += paid
        invested[:, k] = invested[:, k - 1] * growth[:, k - 1] + to_invest
        cash_total[k] = sum(cash_balances) + sum(into_cash.values())
        put_in[k] = put_in[k - 1] + to_invest + to_cash
    totals = invested + cash_total

    chance = None
    if target is not None and target_date is not None:
        idx = _months_between(start, _month(target_date))
        if 0 < idx <= months:
            share = D(str(float(np.mean(totals[:, idx] >= float(target))) * 100))
            chance = _clamp((share / CHANCE_STEP).quantize(D(1)) * CHANCE_STEP,
                            CHANCE_MIN, CHANCE_MAX)

    if real:
        deflator = (1 + float(INFLATION)) ** (np.arange(months + 1) / 12)
        totals = totals / deflator
        put_in = put_in / deflator
    worst, base, best = np.percentile(totals, [WORST_PCT, BASE_PCT, BEST_PCT], axis=0)

    def whole(x: float) -> Decimal:
        return D(round(float(x)))

    points = [ProjectionPoint(_month(start, k), whole(worst[k]), whole(base[k]), whole(best[k]),
                              whole(put_in[k])) for k in range(months + 1)]
    notes = []
    horizon_end = _month(start, months)
    for c in contributions:
        name = c.label or "A regular contribution"
        if c.end is not None and start < _month(c.end) < horizon_end:
            notes.append(f"{name} ends in {c.end:%b %Y}.")
        if _month(c.start) > horizon_end:
            notes.append(f"{name} starts in {c.start:%b %Y}, after this projection ends.")
    contributed = whole(put_in[-1] - put_in[0])
    return Projection(points, whole(put_in[0]), contributed, mu, sigma, chance, list(assets),
                      notes)
