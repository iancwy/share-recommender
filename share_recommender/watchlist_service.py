"""Checking a ticker before it goes on the watchlist. Shared by the Streamlit UI and the API.

Fetching is passed in, like the other services, so tests use fakes.
"""

from share_recommender.core.models import WatchlistItemIn
from share_recommender.core.universe import Kind, is_bond_fund, lookup
from share_recommender.recommendations import FundamentalsFn, QuotesFn

BONDS_REFUSED = "This app only tracks shares, ETFs and cash"


def prepare_item(item: WatchlistItemIn, quotes_fn: QuotesFn,
                 fundamentals_fn: FundamentalsFn) -> WatchlistItemIn:
    """Look the ticker up and fill in its name, kind and today's price.

    Raises ValueError with a message for the person if it can't be found or is a bond fund.
    """
    key = item.key
    quote = quotes_fn((key,)).get(key)
    if quote is None:
        raise ValueError(f"Couldn't find {item.ticker} on {item.exchange}. Check the ticker and "
                         "exchange.")
    inst = lookup(*key)
    if inst is not None:
        name, kind = inst.name, inst.kind
        fund, category = inst.kind is Kind.ETF, None
    else:
        f = fundamentals_fn((key,)).get(key)
        name = f.name if f else None
        fund, category = (f.is_fund, f.fund_category) if f else (False, None)
        kind = Kind.ETF if fund else Kind.SHARE if f else None
    if fund and is_bond_fund(name, category):
        raise ValueError(BONDS_REFUSED)
    return item.model_copy(update={"name": item.name or name, "kind": item.kind or kind,
                                   "added_price": item.added_price or quote.price})
