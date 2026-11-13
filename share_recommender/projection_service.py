"""Gather what a projection needs for one scope, then run ``core.projection.project``.

Shared by the Streamlit UI and the API. Every fetch is passed in, like the other services,
so the UI hands over its cached versions and tests pass fakes.
"""

import math
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from share_recommender.core.models import (
    CashAccount,
    Category,
    Contribution,
    Holding,
    RiskLevel,
)
from share_recommender.core.preferences import CategoryPreferences, InvestorPreferences
from share_recommender.core.projection import (
    PAST_MAX_YEARS,
    VOLATILITY_YEARS,
    Asset,
    ContributionFlow,
    Projection,
    cash_asset,
    contribution_goal,
    holding_asset,
    project,
    target_mix_assets,
    to_flows,
)
from share_recommender.core.recommender import BASE_GROWTH, growth_percent, horizon_years
from share_recommender.portfolio_service import HistoryFn, valued_cash, valued_holdings
from share_recommender.recommendations import FundamentalsFn, FxFn, QuotesFn

DEFAULT_YEARS = 5
MAX_YEARS = 40
PORTFOLIO_GROWTH = BASE_GROWTH[RiskLevel.MEDIUM]  # a whole portfolio with nothing in it yet


class Scope(StrEnum):
    PORTFOLIO = "portfolio"
    GOAL = "goal"
    SHARE = "share"


def default_years(category: Category | None, today: date) -> int:
    """Years to a goal's target date, rounded up (at least 1); otherwise 5."""
    if category is None or not category.target_date:
        return DEFAULT_YEARS
    days = (category.target_date - today).days
    return max(1, min(MAX_YEARS, math.ceil(days / 365.25)))


def run_projection(*, scope: Scope, category_id: UUID | None = None, ticker: str | None = None,
                   exchange: str | None = None, amount: Decimal | None = None,
                   extra: ContributionFlow | None = None, holdings: list[Holding],
                   cash_accounts: list[CashAccount], categories: list[Category],
                   contributions: list[Contribution],
                   goal_prefs: dict[UUID, CategoryPreferences | None],
                   inv: InvestorPreferences | None, base: str, quotes_fn: QuotesFn, fx_fn: FxFn,
                   history_fn: HistoryFn, fundamentals_fn: FundamentalsFn, years: int,
                   basis: Literal["average", "past"], real: bool, today: date) -> Projection:
    notes: list[str] = []
    category = next((c for c in categories if c.id == category_id), None)
    if scope is Scope.GOAL and category is None:
        raise LookupError("Goal not found")

    # What's in scope today.
    if scope is Scope.PORTFOLIO:
        mine, accounts = holdings, cash_accounts
    elif scope is Scope.GOAL:
        mine = [h for h in holdings if h.category_id == category_id]
        accounts = [a for a in cash_accounts if a.category_id == category_id]
    else:
        mine = [h for h in holdings if (h.ticker, h.exchange) == (ticker, exchange)]
        accounts = []

    values: dict[tuple[str, str], Decimal] = {}
    for v in valued_holdings(mine, base, quotes_fn, fx_fn):  # holdings are lots: group them
        key = (v.holding.ticker, v.holding.exchange)
        values[key] = values.get(key, Decimal(0)) + v.value
        if not v.priced and f"{key[0]} has" not in " ".join(notes):
            notes.append(f"{key[0]} has no current price, so it's counted at what you paid.")
    if scope is Scope.SHARE and not values and ticker and exchange:
        values[(ticker, exchange)] = amount or Decimal(0)

    # Prices for volatility (and past returns), fundamentals for dividend yields.
    keys = tuple(sorted(values))
    back = PAST_MAX_YEARS if basis == "past" else VOLATILITY_YEARS
    bars = {}
    if keys:
        start = today - timedelta(days=int(back * 365.25) + 31)
        bars, _ = history_fn(keys, (), base, start, today)
    yields = {}
    if basis == "past" and keys:
        yields = {k: f.dividend_yield for k, f in fundamentals_fn(keys).items() if f}

    assets: list[Asset] = [
        holding_asset(t, e, values[(t, e)], bars.get((t, e), []), basis, yields.get((t, e)))
        for t, e in keys]
    assets += [cash_asset(c) for c in valued_cash(accounts, base, fx_fn)]

    # Contributions in scope.
    cash_by_id = {a.id: a for a in cash_accounts}
    cash_goals = {a.id: a.category_id for a in cash_accounts}
    if scope is Scope.PORTFOLIO:
        chosen = contributions
    elif scope is Scope.GOAL:
        chosen = [c for c in contributions if contribution_goal(c, cash_goals) == category_id]
        if any(c.active and contribution_goal(c, cash_goals) is None for c in contributions):
            notes.append("Contributions to the whole portfolio aren't included when you "
                         "project one goal.")
    else:
        chosen = []
    flows = to_flows(chosen, cash_by_id, today)
    if extra is not None:
        flows.append(extra)

    # Nothing invested yet: contributions follow the goal's (or a medium-risk) target mix.
    if not any(not a.is_cash for a in assets) and any(f.into_cash_rate is None for f in flows):
        if scope is Scope.GOAL:
            prefs = goal_prefs.get(category.id)
            if prefs is not None and inv is not None:
                growth, _ = growth_percent(category, inv, prefs,
                                           horizon_years(category, prefs, today))
            else:
                growth = BASE_GROWTH[category.risk_level]
            assets += target_mix_assets(growth, "Nothing in this goal yet")
        else:
            assets += target_mix_assets(PORTFOLIO_GROWTH, "Nothing invested yet")

    target = category.target_amount if scope is Scope.GOAL and category else None
    target_date = category.target_date if scope is Scope.GOAL and category else None
    result = project(assets, flows, today=today, years=max(1, min(MAX_YEARS, years)),
                     target=target, target_date=target_date, real=real)
    return replace(result, notes=notes + result.notes)
