"""Gather data for the dashboard and health check. Shared by the Streamlit UI and the API.

Like ``recommendations.py``, fetching is passed in so the UI can use its cached versions.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal

from share_recommender.core.health import (
    HealthReport,
    check_goal,
    check_platforms,
    check_portfolio,
    platform_summaries,
    sort_findings,
)
from share_recommender.core.models import CashAccount, Category, Holding
from share_recommender.core.performance import (
    CashValue,
    HoldingValue,
    ValuePoint,
    cash_values,
    current_values,
    goal_progress,
    value_series,
)
from share_recommender.core.preferences import CategoryPreferences, InvestorPreferences
from share_recommender.data.base import PriceBar
from share_recommender.recommendations import (
    FundamentalsFn,
    FxFn,
    QuotesFn,
    build_recommendation,
)

Symbols = tuple[tuple[str, str], ...]
# (symbols, currencies, base, start, end) -> (price bars per symbol, fx bars per currency)
HistoryFn = Callable[[Symbols, tuple[str, ...], str, date, date],
                     tuple[dict[tuple[str, str], list[PriceBar]], dict[str, list[PriceBar]]]]


def valued_holdings(holdings: list[Holding], base: str, quotes_fn: QuotesFn, fx_fn: FxFn
                    ) -> list[HoldingValue]:
    symbols = tuple(sorted({(h.ticker, h.exchange) for h in holdings}))
    currencies = tuple(sorted({h.currency for h in holdings} - {base}))
    quotes = quotes_fn(symbols) if symbols else {}
    fx = fx_fn(base, currencies) if currencies else {}
    return current_values(holdings, quotes, fx, base)


def valued_cash(accounts: list[CashAccount], base: str, fx_fn: FxFn) -> list[CashValue]:
    currencies = tuple(sorted({a.currency for a in accounts} - {base}))
    return cash_values(accounts, fx_fn(base, currencies) if currencies else {}, base)


def portfolio_history(holdings: list[Holding], base: str, start: date, end: date,
                      history_fn: HistoryFn, cash: list[CashAccount] = ()) -> list[ValuePoint]:
    if not holdings and not cash:
        return []
    symbols = tuple(sorted({(h.ticker, h.exchange) for h in holdings}))
    currencies = tuple(sorted(({h.currency for h in holdings} | {a.currency for a in cash})
                              - {base}))
    # Exchange rates go back to the first purchase, to convert what was paid at the time.
    fx_start = min([start] + [h.buy_date for h in holdings] + [a.as_of for a in cash])
    prices, fx = history_fn(symbols, currencies, base, fx_start, end)
    return value_series(holdings, prices, fx, base, start, end, cash=list(cash))


def health_report(categories: list[Category], holdings: list[Holding],
                  inv: InvestorPreferences | None,
                  goal_prefs: dict, base: str, quotes_fn: QuotesFn, fx_fn: FxFn,
                  fundamentals_fn: FundamentalsFn, today: date | None = None,
                  cash_accounts: list[CashAccount] = ()) -> HealthReport:
    today = today or date.today()
    values = valued_holdings(holdings, base, quotes_fn, fx_fn)
    cash = valued_cash(list(cash_accounts), base, fx_fn)
    findings = check_portfolio(values, base, inv is not None, cash)
    recommendations = {}
    for cat in categories:
        in_goal = [h for h in holdings if h.category_id == cat.id]
        goal_cash = [a for a in cash_accounts if a.category_id == cat.id]
        rec = build_recommendation(cat, inv or InvestorPreferences(),
                                   goal_prefs.get(cat.id) or CategoryPreferences(), in_goal, base,
                                   quotes_fn, fx_fn, fundamentals_fn, today=today,
                                   all_holdings=holdings, cash_accounts=goal_cash)
        progress = goal_progress(cat, [v for v in values if v.holding.category_id == cat.id],
                                 today, [c for c in cash if c.account.category_id == cat.id])
        findings += check_goal(cat, rec, progress, base)
        recommendations[cat.id] = rec
    summaries = platform_summaries(values, cash)
    findings += check_platforms(summaries)
    return HealthReport(sort_findings(findings), summaries, recommendations)


def total(values: list[HoldingValue], cash: list[CashValue] = ()) -> tuple[Decimal, Decimal]:
    """(current value, money invested) in base currency. Cash counts in both at its balance."""
    in_cash = sum((c.value for c in cash), Decimal(0))
    return (sum((v.value for v in values), Decimal(0)) + in_cash,
            sum((v.cost for v in values), Decimal(0)) + in_cash)
