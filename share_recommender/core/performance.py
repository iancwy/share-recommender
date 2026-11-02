"""Dashboard numbers: value over time, breakdowns and progress towards goals. Pure functions.

All amounts are in the person's base currency. Exchange rates are given as units of the
other currency per 1 unit of base (AUD->USD 0.65), the same convention as everywhere else.

Cash in savings accounts is passed alongside holdings as ``CashValue``s. It is valued at its
balance, always counts as cash, and grows at the account's own interest rate in projections.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from share_recommender.core.models import CashAccount, Category, Holding
from share_recommender.core.portfolio import UNCATEGORISED
from share_recommender.core.universe import (
    ASSET_CLASS_LABELS,
    GROWTH_CLASSES,
    AssetClass,
    Kind,
    classify,
    lookup,
)
from share_recommender.data.base import PriceBar, Quote

CENT = Decimal("0.01")

# Long-run return assumptions used only to project goals. Nominal, before fees and tax.
# These are assumptions, not forecasts: real returns vary a lot from year to year.
ASSUMED_RETURN = {"growth": Decimal("0.07"), "cash": Decimal("0.035")}


# Value over time ----------------------------------------------------------------

@dataclass(frozen=True)
class ValuePoint:
    day: date
    value: Decimal     # what the holdings bought by this day were worth, plus cash
    invested: Decimal  # what was paid for them, including fees, plus cash


class _Series:
    """Look up the last known value on or before a day."""

    def __init__(self, bars: list[PriceBar]):
        bars = sorted(bars, key=lambda b: b.day)
        self.days = [b.day for b in bars]
        self.values = [b.close for b in bars]

    def at(self, day: date) -> Decimal | None:
        i = bisect_right(self.days, day)
        return self.values[i - 1] if i else None

    def first(self) -> Decimal | None:
        return self.values[0] if self.values else None


def sample_days(start: date, end: date) -> list[date]:
    """Daily for up to about 13 months, weekly beyond that. Always includes the end."""
    step = 1 if (end - start).days <= 400 else 7
    days = [start + timedelta(days=i) for i in range(0, (end - start).days + 1, step)]
    if days and days[-1] != end:
        days.append(end)
    return days


def value_series(holdings: list[Holding], prices: dict[tuple[str, str], list[PriceBar]],
                 fx: dict[str, list[PriceBar]], base: str, start: date, end: date,
                 cash: list[CashAccount] = ()) -> list[ValuePoint]:
    """Daily (or weekly) portfolio value and money invested.

    Before a holding's first known price it counts at its buy price. Money invested is
    converted at the exchange rate on the buy date; value at the rate on each day. Cash
    accounts have no balance history, so each adds its current balance as a flat line from
    the date it was last checked.
    """
    price_series = {k: _Series(v) for k, v in prices.items()}
    fx_series = {c: _Series(v) for c, v in fx.items()}

    def rate(ccy: str, day: date) -> Decimal | None:
        if ccy == base:
            return Decimal(1)
        s = fx_series.get(ccy)
        return (s.at(day) or s.first()) if s else None

    points = []
    for day in sample_days(start, end):
        value = invested = Decimal(0)
        for h in holdings:
            if h.buy_date > day:
                continue
            r_now, r_bought = rate(h.currency, day), rate(h.currency, h.buy_date)
            if not r_now or not r_bought:
                continue
            s = price_series.get((h.ticker, h.exchange))
            price = (s.at(day) if s else None) or h.buy_price
            value += h.units * price / r_now
            invested += h.cost_base / r_bought
        for a in cash:
            r_now, r_then = rate(a.currency, day), rate(a.currency, a.as_of)
            if a.as_of <= day and r_now and r_then:
                value += a.balance / r_now
                invested += a.balance / r_then
        points.append(ValuePoint(day, value.quantize(CENT), invested.quantize(CENT)))
    return points


# Current values and breakdowns -------------------------------------------------

@dataclass(frozen=True)
class HoldingValue:
    holding: Holding
    value: Decimal        # base currency; at cost if there's no price
    cost: Decimal         # base currency, at today's rate
    priced: bool


@dataclass(frozen=True)
class CashValue:
    account: CashAccount
    value: Decimal  # base currency

    @property
    def rate(self) -> Decimal:
        """Yearly interest as a fraction (5.25% -> 0.0525)."""
        return self.account.interest_rate / 100


def cash_values(accounts: list[CashAccount], fx: dict[str, Decimal | None], base: str
                ) -> list[CashValue]:
    """Cash accounts valued in base currency. Accounts with no exchange rate are left out."""
    out = []
    for a in accounts:
        rate = Decimal(1) if a.currency == base else fx.get(a.currency)
        if rate:
            out.append(CashValue(a, (a.balance / rate).quantize(CENT)))
    return out


def current_values(holdings: list[Holding], quotes: dict[tuple[str, str], Quote | None],
                   fx: dict[str, Decimal | None], base: str) -> list[HoldingValue]:
    """Every holding valued in base currency. Holdings with no exchange rate are left out."""
    out = []
    for h in holdings:
        rate = Decimal(1) if h.currency == base else fx.get(h.currency)
        if not rate:
            continue
        q = quotes.get((h.ticker, h.exchange))
        local = h.units * q.price if q else h.cost_base
        out.append(HoldingValue(h, (local / rate).quantize(CENT),
                                (h.cost_base / rate).quantize(CENT), q is not None))
    return out


class Dimension(StrEnum):
    GOAL = "Goal"
    PLATFORM = "Platform"
    TYPE = "Type"
    ASSET_CLASS = "Asset class"
    CURRENCY = "Currency"
    HOLDING = "Holding"


@dataclass(frozen=True)
class Slice:
    label: str
    value: Decimal
    pct: Decimal




def breakdown(values: list[HoldingValue], categories: list[Category], by: Dimension,
              top: int = 10, cash: list[CashValue] = ()) -> list[Slice]:
    """Value per goal, platform, type, asset class, currency or holding, largest first.

    Holdings beyond the top ``top`` are folded into "Other" so the chart stays readable.
    Cash accounts count under their goal, their bank (as the platform) and as cash.
    """
    names: dict[UUID, str] = {c.id: c.name for c in categories}

    def label(v: HoldingValue) -> str:
        h = v.holding
        match by:
            case Dimension.GOAL:
                return names.get(h.category_id, UNCATEGORISED) if h.category_id else UNCATEGORISED
            case Dimension.PLATFORM:
                return h.platform or "No platform set"
            case Dimension.TYPE:
                inst = lookup(h.ticker, h.exchange)
                # Anything not on the list is assumed to be a company's shares.
                return "ETFs" if inst and inst.kind is Kind.ETF else "Shares"
            case Dimension.CURRENCY:
                return h.currency
            case Dimension.HOLDING:
                return h.ticker

    totals: dict[str, Decimal] = {}
    for v in values:
        if by is Dimension.ASSET_CLASS:  # diversified funds count towards several classes
            for ac, frac in classify(v.holding.ticker, v.holding.exchange).items():
                name = ASSET_CLASS_LABELS[ac]
                totals[name] = totals.get(name, Decimal(0)) + v.value * frac
        else:
            totals[label(v)] = totals.get(label(v), Decimal(0)) + v.value
    for c in cash:
        a = c.account
        name = {Dimension.GOAL: names.get(a.category_id, UNCATEGORISED) if a.category_id
                else UNCATEGORISED,
                Dimension.PLATFORM: a.institution, Dimension.TYPE: "Cash",
                Dimension.ASSET_CLASS: ASSET_CLASS_LABELS[AssetClass.CASH],
                Dimension.CURRENCY: a.currency, Dimension.HOLDING: a.label}[by]
        totals[name] = totals.get(name, Decimal(0)) + c.value
    total = sum(totals.values(), Decimal(0))
    ordered = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    if len(ordered) > top:
        ordered = ordered[:top - 1] + [("Other", sum((v for _, v in ordered[top - 1:]),
                                                     Decimal(0)))]
    return [Slice(k, v, (v / total * 100).quantize(Decimal("0.1")) if total else Decimal(0))
            for k, v in ordered]


# Goal progress -------------------------------------------------------------------

@dataclass(frozen=True)
class GoalProgress:
    category: Category
    value: Decimal
    target: Decimal | None
    years_left: Decimal | None
    assumed_return: Decimal         # yearly, blended from the goal's shares and cash
    projected: Decimal | None       # value at the target date with no more money added
    monthly_needed: Decimal | None  # extra per month to reach the target, at that return

    @property
    def pct_of_target(self) -> Decimal | None:
        return (self.value / self.target * 100).quantize(Decimal("0.1")) if self.target else None

    @property
    def on_track(self) -> bool | None:
        if self.target is None or self.projected is None:
            return None
        return self.projected >= self.target


def _holdings_return(values: list[HoldingValue]) -> Decimal:
    """Blend of the shares and cash-fund assumptions, by the share held in shares."""
    total = sum((v.value for v in values), Decimal(0))
    if not total:
        return ASSUMED_RETURN["growth"]
    growth = sum((v.value * frac for v in values
                  for ac, frac in classify(v.holding.ticker, v.holding.exchange).items()
                  if ac in GROWTH_CLASSES), Decimal(0)) / total
    return growth * ASSUMED_RETURN["growth"] + (1 - growth) * ASSUMED_RETURN["cash"]


def assumed_return(values: list[HoldingValue], cash: list[CashValue] = ()) -> Decimal:
    """Yearly return for a goal: holdings at the assumptions, cash at each account's rate."""
    held = sum((v.value for v in values), Decimal(0))
    total = held + sum((c.value for c in cash), Decimal(0))
    if not total:
        return ASSUMED_RETURN["growth"]
    blended = (held * _holdings_return(values) + sum((c.value * c.rate for c in cash),
                                                      Decimal(0))) / total
    return blended.quantize(Decimal("0.0001"))


def _grow(amount: Decimal, yearly: Decimal, months: int) -> Decimal:
    return amount * (1 + yearly) ** (Decimal(months) / 12)


def goal_progress(category: Category, values: list[HoldingValue], today: date,
                  cash: list[CashValue] = ()) -> GoalProgress:
    value = sum((v.value for v in values), Decimal(0)) + sum((c.value for c in cash), Decimal(0))
    rate = assumed_return(values, cash)
    target = category.target_amount
    if not category.target_date:
        return GoalProgress(category, value, target, None, rate, None, None)
    years = max(Decimal(0), Decimal((category.target_date - today).days) / Decimal("365.25"))
    months = int(years * 12)
    monthly = (1 + rate) ** (Decimal(1) / 12) - 1
    # Holdings grow at the assumptions, each cash account at its own rate.
    projected = (_grow(sum((v.value for v in values), Decimal(0)), _holdings_return(values),
                       months)
                 + sum((_grow(c.value, c.rate, months) for c in cash), Decimal(0))
                 ).quantize(CENT)
    needed = None
    if target is not None:
        shortfall = target - projected
        if shortfall <= 0:
            needed = Decimal(0)
        elif months == 0:
            needed = shortfall.quantize(CENT)
        else:
            growth = (1 + monthly) ** months - 1
            needed = (shortfall * monthly / growth).quantize(CENT)
    return GoalProgress(category, value, target, years.quantize(Decimal("0.1")), rate, projected,
                        needed)
