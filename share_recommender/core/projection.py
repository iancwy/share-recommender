"""Projections: what a portfolio, a goal or one holding could be worth, as a range. Pure.

Every projection starts from a list of **assets**, each with a value today, a yearly return
and a yearly volatility (how much it goes up and down). The returns and volatilities below are
**assumptions, not forecasts**: long-run averages by default, or each holding's own past
prices (capped) if the person chooses "Past performance". The returns are the Dashboard's
``ASSUMED_RETURN``, so the base case here and the Dashboard's steady-return figure agree.
"""

import math
from dataclasses import dataclass
from decimal import Decimal

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
