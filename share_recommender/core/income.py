"""Dividend income: totals by Australian financial year (1 July to 30 June). Pure functions."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from share_recommender.core.models import Dividend


def financial_year(d: date) -> str:
    """'2025-26' for any date from 1 July 2025 to 30 June 2026."""
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


@dataclass(frozen=True)
class YearIncome:
    year: str
    currency: str
    payments: int
    cash: Decimal
    franking: Decimal
    withholding: Decimal
    reinvested: Decimal

    @property
    def grossed_up(self) -> Decimal:
        return self.cash + self.franking


def by_financial_year(dividends: list[Dividend]) -> list[YearIncome]:
    """Totals per financial year and currency, most recent year first."""
    groups: dict[tuple[str, str], list[Dividend]] = {}
    for d in dividends:
        groups.setdefault((financial_year(d.pay_date), d.currency), []).append(d)
    out = [YearIncome(year, ccy, len(ds), sum((d.amount for d in ds), Decimal(0)),
                      sum((d.franking_credits for d in ds), Decimal(0)),
                      sum((d.withholding_tax for d in ds), Decimal(0)),
                      sum((d.amount for d in ds if d.reinvested), Decimal(0)))
           for (year, ccy), ds in groups.items()]
    return sorted(out, key=lambda y: (y.year, y.currency), reverse=True)


def income_in_base(dividends: list[Dividend], fx: dict[str, Decimal | None], base: str,
                   since: date | None = None) -> Decimal:
    """Cash dividends converted at today's rates. Payments with no rate are left out."""
    total = Decimal(0)
    for d in dividends:
        if since and d.pay_date < since:
            continue
        rate = Decimal(1) if d.currency == base else fx.get(d.currency)
        if rate:
            total += d.amount / rate
    return total.quantize(Decimal("0.01"))
