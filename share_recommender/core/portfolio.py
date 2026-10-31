"""Valuation of holdings against current prices. Pure functions, easy to test."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from share_recommender.core.models import Category, Holding
from share_recommender.data.base import Quote

UNCATEGORISED = "Uncategorised"


@dataclass(frozen=True)
class HoldingValuation:
    holding: Holding
    category_name: str
    last_price: Decimal | None
    market_value: Decimal | None
    gain: Decimal | None
    gain_pct: Decimal | None

    @property
    def cost_base(self) -> Decimal:
        return self.holding.cost_base


def value_holding(holding: Holding, category_name: str, quote: Quote | None) -> HoldingValuation:
    if quote is None:
        return HoldingValuation(holding, category_name, None, None, None, None)
    market_value = holding.units * quote.price
    gain = market_value - holding.cost_base
    gain_pct = (gain / holding.cost_base * 100) if holding.cost_base else None
    return HoldingValuation(holding, category_name, quote.price, market_value, gain, gain_pct)


def value_holdings(
    holdings: list[Holding],
    categories: list[Category],
    quotes: dict[tuple[str, str], Quote | None],
) -> list[HoldingValuation]:
    names: dict[UUID, str] = {c.id: c.name for c in categories}
    return [
        value_holding(
            h,
            names.get(h.category_id, UNCATEGORISED) if h.category_id else UNCATEGORISED,
            quotes.get((h.ticker, h.exchange)),
        )
        for h in holdings
    ]


def totals_by_currency(valuations: list[HoldingValuation]) -> dict[str, dict[str, Decimal]]:
    """Cost and market value per currency. Holdings without a price count towards cost only."""
    out: dict[str, dict[str, Decimal]] = {}
    for v in valuations:
        t = out.setdefault(v.holding.currency, {"cost_base": Decimal(0), "market_value": Decimal(0),
                                                 "priced_cost_base": Decimal(0)})
        t["cost_base"] += v.cost_base
        if v.market_value is not None:
            t["market_value"] += v.market_value
            t["priced_cost_base"] += v.cost_base
    return out
