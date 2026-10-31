"""Portfolio health check: findings by goal, by platform and across the whole portfolio.

Pure functions over data already gathered. Fixes are always top-ups: the health check
never suggests selling, so it never triggers capital gains tax. The thresholds are named
constants so they're easy to change.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from share_recommender.core.models import Category
from share_recommender.core.performance import CashValue, GoalProgress, HoldingValue
from share_recommender.core.recommender import Recommendation
from share_recommender.core.universe import GROWTH_CLASSES, Kind, lookup

DRIFT_WARN = Decimal(5)          # percentage points away from the target mix
DRIFT_ALERT = Decimal(10)
COMPANY_WARN = Decimal(10)       # % of the whole portfolio in one company
COMPANY_ALERT = Decimal(20)
FEES_WARN = Decimal(1)           # brokerage as % of the money invested on a platform
NEAR_DEADLINE_YEARS = Decimal(2)


class Severity(StrEnum):
    OK = "ok"
    INFO = "info"
    WARN = "warning"
    ALERT = "alert"


SEVERITY_ORDER = [Severity.ALERT, Severity.WARN, Severity.INFO, Severity.OK]


@dataclass(frozen=True)
class Finding:
    severity: Severity
    area: str             # "Portfolio", a goal's name, or a platform's name
    title: str
    detail: str
    action: str | None = None


@dataclass(frozen=True)
class PlatformSummary:
    platform: str
    value: Decimal
    pct: Decimal
    holdings: int
    invested: Decimal
    fees: Decimal

    @property
    def fees_pct(self) -> Decimal:
        return (self.fees / self.invested * 100).quantize(Decimal("0.01")) if self.invested \
            else Decimal(0)


@dataclass(frozen=True)
class HealthReport:
    findings: list[Finding]
    platforms: list[PlatformSummary]

    def count(self, severity: Severity) -> int:
        return sum(1 for f in self.findings if f.severity is severity)


def _fmt(amount: Decimal, base: str) -> str:
    return f"{amount:,.0f} {base}"


# Goals ---------------------------------------------------------------------------

def check_goal(category: Category, rec: Recommendation, progress: GoalProgress,
               base: str) -> list[Finding]:
    name = category.name
    gaps = rec.gaps
    if not gaps.total_value:
        return [Finding(Severity.INFO, name, "Nothing held yet",
                        "There are no holdings in this goal, so there's nothing to check.",
                        "Use the Recommendations page to plan a first purchase.")]

    out = []
    worst = max(gaps.slices, key=lambda g: abs(g.current_pct - g.target_pct))
    drift = abs(worst.current_pct - worst.target_pct)
    under = [g.label for g in gaps.slices if g.difference > 0
             and g.current_pct + DRIFT_WARN <= g.target_pct]
    action = (f"Point new money at {', '.join(under).lower()}. The Recommendations page works "
              "out exactly what to buy." if under else
              "Point new money at the underweight slices on the Recommendations page.")
    if drift >= DRIFT_WARN:
        direction = "over" if worst.current_pct > worst.target_pct else "under"
        out.append(Finding(
            Severity.ALERT if drift >= DRIFT_ALERT else Severity.WARN, name,
            f"Off its target mix by {drift:g} points",
            f"{worst.label} is {worst.current_pct:g}% against a target of {worst.target_pct:g}% "
            f"({direction}weight).", action))
    else:
        out.append(Finding(Severity.OK, name, "On its target mix",
                           f"Every slice is within {DRIFT_WARN:g} points of its target."))

    if (progress.years_left is not None and progress.years_left < NEAR_DEADLINE_YEARS
            and not rec.mix.speculative):
        growth_now = sum((g.current_pct for g in gaps.slices if g.key in
                          {c.value for c in GROWTH_CLASSES}), Decimal(0))
        if growth_now > rec.mix.growth_pct + DRIFT_WARN:
            out.append(Finding(
                Severity.ALERT, name, "Close to its deadline with too much in shares",
                f"The money is needed in about {progress.years_left:g} years, but {growth_now:g}% "
                f"is in shares against a target of {rec.mix.growth_pct}%. A market fall now "
                "would have little time to recover.",
                "Put new money into cash until it's back on target."))

    if progress.target is not None and progress.on_track is not None:
        if progress.on_track:
            out.append(Finding(
                Severity.OK, name, "On track for its target",
                f"At an assumed {progress.assumed_return * 100:g}% a year it would reach about "
                f"{_fmt(progress.projected, base)} by the target date, against "
                f"{_fmt(progress.target, base)}."))
        else:
            out.append(Finding(
                Severity.WARN, name, "Behind its target",
                f"At an assumed {progress.assumed_return * 100:g}% a year it would reach about "
                f"{_fmt(progress.projected, base)} by the target date, short of "
                f"{_fmt(progress.target, base)}.",
                f"Adding about {_fmt(progress.monthly_needed, base)} a month would close the gap, "
                "or the target or date could change."))

    out += [Finding(Severity.WARN, name, "Recommender warning", w) for w in rec.mix.warnings]
    return out


# Portfolio and platforms -------------------------------------------------------

def check_portfolio(values: list[HoldingValue], base: str, preferences_done: bool,
                    cash: list[CashValue] = ()) -> list[Finding]:
    """Checks across everything. Cash counts in the total but is never a "company"."""
    out = []
    if not preferences_done:
        out.append(Finding(Severity.INFO, "Portfolio", "Preferences not answered",
                           "Goal checks use default answers until you do.",
                           "Answer the questionnaire on the Preferences page."))
    total = sum((v.value for v in values), Decimal(0)) + sum((c.value for c in cash), Decimal(0))
    if not total:
        return out

    by_company: dict[str, Decimal] = {}
    for v in values:
        inst = lookup(v.holding.ticker, v.holding.exchange)
        if inst is None or inst.kind is Kind.SHARE:  # unknown tickers are treated as companies
            by_company[v.holding.ticker] = by_company.get(v.holding.ticker, Decimal(0)) + v.value
    for ticker, value in sorted(by_company.items(), key=lambda kv: kv[1], reverse=True):
        pct = (value / total * 100).quantize(Decimal("0.1"))
        if pct >= COMPANY_WARN:
            out.append(Finding(
                Severity.ALERT if pct >= COMPANY_ALERT else Severity.WARN, "Portfolio",
                f"{pct:g}% in one company ({ticker})",
                "If that one company has a bad year, a big part of your portfolio goes with it.",
                f"Let new money go to ETFs so {ticker} becomes a smaller share over time. You "
                f"can also add {ticker} to the tickers to leave out on the Preferences page."))

    unpriced = sorted({v.holding.ticker for v in values if not v.priced})
    if unpriced:
        out.append(Finding(Severity.WARN, "Portfolio", "Holdings without a current price",
                           f"{', '.join(unpriced)} are counted at what you paid, so totals may be "
                           "out.", "Check the ticker and exchange on the Holdings page."))
    uncategorised = [v for v in values if v.holding.category_id is None]
    if uncategorised:
        out.append(Finding(Severity.INFO, "Portfolio",
                           f"{len(uncategorised)} holding(s) not in a goal",
                           "They count towards totals but not towards any goal's checks.",
                           "Give them a category on the Holdings page."))
    foreign = (sum((v.value for v in values if v.holding.currency != base), Decimal(0))
               + sum((c.value for c in cash if c.account.currency != base), Decimal(0)))
    if foreign:
        out.append(Finding(Severity.OK, "Portfolio", "Currency exposure",
                           f"{foreign / total * 100:.0f}% of your portfolio is in currencies other "
                           f"than {base}, so exchange rates move its value too."))
    return out


def platform_summaries(values: list[HoldingValue], cash: list[CashValue] = ()
                       ) -> list[PlatformSummary]:
    """Per platform (a cash account's bank counts as its platform). Cash adds to value and
    share, but not to money invested or fees, so it can't hide brokerage costs."""
    total = sum((v.value for v in values), Decimal(0)) + sum((c.value for c in cash), Decimal(0))
    names = {v.holding.platform or "No platform set" for v in values} | {
        c.account.institution for c in cash}
    out = []
    for name in names:
        vs = [v for v in values if (v.holding.platform or "No platform set") == name]
        cs = [c for c in cash if c.account.institution == name]
        value = sum((v.value for v in vs), Decimal(0)) + sum((c.value for c in cs), Decimal(0))
        fees = sum((v.holding.fees * v.cost / v.holding.cost_base for v in vs), Decimal(0))
        out.append(PlatformSummary(
            name, value, (value / total * 100).quantize(Decimal("0.1")) if total else Decimal(0),
            len(vs) + len(cs), sum((v.cost for v in vs), Decimal(0)),
            fees.quantize(Decimal("0.01"))))
    return sorted(out, key=lambda p: p.value, reverse=True)


def check_platforms(summaries: list[PlatformSummary]) -> list[Finding]:
    out = []
    for p in summaries:
        if p.platform == "No platform set":
            out.append(Finding(Severity.INFO, p.platform,
                               f"{p.holdings} holding(s) without a platform",
                               "They can't be included in the per-platform checks.",
                               "Set the platform on the Holdings page."))
        elif not p.invested:
            continue  # only cash here: no brokerage to check
        elif p.fees_pct >= FEES_WARN:
            out.append(Finding(
                Severity.WARN, p.platform, f"Brokerage is {p.fees_pct:g}% of what you invested",
                f"You've paid {p.fees:,.2f} in brokerage on {p.invested:,.0f} invested here.",
                "Fewer, larger purchases keep fees down. The buy plan skips trades too small "
                "to be worth the brokerage."))
        else:
            out.append(Finding(Severity.OK, p.platform, "Fees look reasonable",
                               f"Brokerage is {p.fees_pct:g}% of the {p.invested:,.0f} invested "
                               "here."))
    return out


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: SEVERITY_ORDER.index(f.severity))
