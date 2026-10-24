"""Fetch what the recommender needs and run it. Shared by the Streamlit UI and the API.

The fetch functions are passed in so the UI can hand over its cached versions and tests
can pass fakes; ``core.recommender`` itself never touches the network.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal

from share_recommender.core.models import Category, Holding
from share_recommender.core.preferences import (
    CategoryPreferences,
    IncomeGrowth,
    InvestorPreferences,
)
from share_recommender.core.recommender import (
    Recommendation,
    analyse_gaps,
    holding_values,
    is_speculative,
    plan_purchase,
    portfolio_share_warning,
    share_candidates,
    target_mix,
)
from share_recommender.data.base import Fundamentals, MarketDataProvider, Quote

Symbols = tuple[tuple[str, str], ...]
QuotesFn = Callable[[Symbols], dict[tuple[str, str], Quote | None]]
FxFn = Callable[[str, tuple[str, ...]], dict[str, Decimal | None]]
FundamentalsFn = Callable[[Symbols], dict[tuple[str, str], Fundamentals | None]]


def provider_fns(provider: MarketDataProvider) -> tuple[QuotesFn, FxFn, FundamentalsFn]:
    return (lambda s: provider.get_quotes(list(s)),
            lambda base, ccys: provider.get_fx_rates(base, list(ccys)),
            lambda s: provider.get_many_fundamentals(list(s)))


def build_recommendation(
    category: Category,
    inv: InvestorPreferences,
    prefs: CategoryPreferences,
    holdings: list[Holding],
    base_currency: str,
    quotes_fn: QuotesFn,
    fx_fn: FxFn,
    fundamentals_fn: FundamentalsFn,
    amount: Decimal | None = None,
    today: date | None = None,
    all_holdings: list[Holding] | None = None,
) -> Recommendation:
    """Recommendation for one category. ``holdings`` should be that category's holdings;
    ``all_holdings`` (every category) lets a speculative goal be checked against the total."""
    today = today or date.today()
    held = {(h.ticker, h.exchange) for h in holdings}
    all_holdings = all_holdings or []

    # Fundamentals are only used to rank shares by dividend yield, so skip the (slow) fetch
    # unless the person wants income and individual shares in an ordinary goal.
    fundamentals = {}
    if inv.income_growth is IncomeGrowth.INCOME and not is_speculative(category, prefs):
        candidates = tuple(sorted(s.key for s in share_candidates(inv)))
        if candidates:
            fundamentals = fundamentals_fn(candidates)

    mix = target_mix(category, inv, prefs, today, held, fundamentals)
    everything = holdings + (all_holdings if mix.speculative else [])
    symbols = tuple(sorted({(h.ticker, h.exchange) for h in everything}
                           | {(p.ticker, p.exchange) for p in mix.picks}))
    quotes = quotes_fn(symbols) if symbols else {}
    currencies = tuple(sorted(({h.currency for h in everything}
                               | {p.currency for p in mix.picks}) - {base_currency}))
    fx = fx_fn(base_currency, currencies) if currencies else {}

    values, notes = holding_values(holdings, quotes, fx, base_currency)
    gaps = analyse_gaps(mix, values, base_currency, notes)
    if mix.speculative and all_holdings:
        all_values, _ = holding_values(all_holdings, quotes, fx, base_currency)
        warning = portfolio_share_warning(gaps.total_value, sum(all_values.values(), Decimal(0)),
                                          amount or Decimal(0))
        if warning:
            mix.warnings.append(warning)
    plan = None
    if amount:
        plan = plan_purchase(mix, values, amount, quotes, fx, base_currency,
                             inv.brokerage_per_trade)
    return Recommendation(category_id=category.id, category_name=category.name, mix=mix,
                          gaps=gaps, buy_plan=plan)
