"""Watchlist figures: price, changes, 52-week range and key metrics. Pure functions.

Anything missing (no price, no history, no fundamentals) comes back as None, so the page
shows a blank rather than an error.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from share_recommender.core.models import WatchlistItem
from share_recommender.data.base import Fundamentals, PriceBar, Quote

CURRENCY_SYMBOLS = {"AUD": "A$", "USD": "US$", "GBP": "£", "CAD": "C$", "SGD": "S$",
                    "HKD": "HK$", "NZD": "NZ$"}


def short_money(amount: Decimal | None, currency: str) -> str | None:
    """'A$12.3bn', 'US$1.2tn', 'A$450m'. None stays None."""
    if amount is None:
        return None
    symbol = CURRENCY_SYMBOLS.get(currency, f"{currency} ")
    for size, suffix in [(Decimal("1e12"), "tn"), (Decimal("1e9"), "bn"), (Decimal("1e6"), "m")]:
        if abs(amount) >= size:
            return f"{symbol}{amount / size:,.1f}{suffix}"
    return f"{symbol}{amount:,.0f}"


@dataclass(frozen=True)
class WatchRow:
    item: WatchlistItem
    price: Decimal | None            # exchange currency
    currency: str
    day_pct: Decimal | None
    since_added_pct: Decimal | None
    year_pct: Decimal | None
    range_pos: Decimal | None        # 0 = at the 52-week low, 100 = at the high
    pe_ratio: Decimal | None
    dividend_yield_pct: Decimal | None
    market_cap: str | None           # base currency, short form
    held: bool

    @property
    def alerts(self) -> str:
        parts = []
        if self.item.alert_below:
            parts.append(f"≤ {self.item.alert_below:,.2f}")
        if self.item.alert_above:
            parts.append(f"≥ {self.item.alert_above:,.2f}")
        return ", ".join(parts)


def _pct(new: Decimal | None, old: Decimal | None) -> Decimal | None:
    if new is None or not old:
        return None
    return ((new / old - 1) * 100).quantize(Decimal("0.1"))


def watch_rows(items: list[WatchlistItem], quotes: dict[tuple[str, str], Quote | None],
               fundamentals: dict[tuple[str, str], Fundamentals | None],
               history: dict[tuple[str, str], list[PriceBar]], base: str,
               fx: dict[str, Decimal | None], held: set[tuple[str, str]],
               today: date | None = None) -> list[WatchRow]:
    """One row per watchlist item. ``history`` should cover about the last year."""
    today = today or date.today()
    rows = []
    for item in items:
        q = quotes.get(item.key)
        f = fundamentals.get(item.key)
        bars = sorted(history.get(item.key, []), key=lambda b: b.day)
        price = q.price if q else (bars[-1].close if bars else None)
        day = _pct(bars[-1].close, bars[-2].close) if len(bars) >= 2 else None
        year_ago = [b for b in bars if b.day <= today - timedelta(days=365)]
        start = year_ago[-1] if year_ago else None
        year = _pct(price, start.close) if start else None
        recent = [b.close for b in bars if b.day > today - timedelta(days=365)]
        low = getattr(f, "low_52w", None) or (min(recent) if recent else None)
        high = getattr(f, "high_52w", None) or (max(recent) if recent else None)
        pos = (((price - low) / (high - low)) * 100).quantize(Decimal("1")) \
            if price is not None and low is not None and high and high > low else None
        cap = None
        if f and f.market_cap:
            rate = Decimal(1) if item.currency == base else fx.get(item.currency)
            cap = short_money(f.market_cap / rate, base) if rate else None
        rows.append(WatchRow(
            item=item, price=price, currency=item.currency, day_pct=day,
            since_added_pct=_pct(price, item.added_price), year_pct=year, range_pos=pos,
            pe_ratio=f.pe_ratio if f else None,
            dividend_yield_pct=(f.dividend_yield * 100).quantize(Decimal("0.01"))
            if f and f.dividend_yield is not None else None,
            market_cap=cap, held=item.key in held))
    return rows
