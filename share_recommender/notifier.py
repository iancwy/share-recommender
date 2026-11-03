"""Work out one person's notifications: snapshot, compare, and write the messages.

Shared by the scheduled job and the app's "Check for changes now" preview. Fetching is
passed in, like the other services, so it can be tested with fakes.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from share_recommender.core.models import CashAccount, Category, Holding, WatchlistItem
from share_recommender.core.notifications import (
    PORTFOLIO,
    Message,
    deadline_reminders,
    diff,
    goal_message,
    portfolio_message,
    price_message,
    price_moves,
    snapshot,
    watchlist_alerts,
    watchlist_message,
)
from share_recommender.core.performance import goal_progress
from share_recommender.core.picker import PickRow
from share_recommender.core.preferences import InvestorPreferences
from share_recommender.portfolio_service import (
    HistoryFn,
    health_report,
    valued_cash,
    valued_holdings,
)
from share_recommender.recommendations import FundamentalsFn, FxFn, QuotesFn


@dataclass
class Outcome:
    messages: list[Message]
    snapshots: dict[str, dict]  # scope -> payload, to save
    baseline: bool              # first run: snapshots stored, nothing sent


def build_notifications(*, categories: list[Category], goal_prefs: dict,
                        holdings: list[Holding], cash_accounts: list[CashAccount],
                        inv: InvestorPreferences | None, base: str,
                        old_snapshots: dict[str, dict], quotes_fn: QuotesFn, fx_fn: FxFn,
                        fundamentals_fn: FundamentalsFn, history_fn: HistoryFn, today: date,
                        weekly: bool, watchlist: list[WatchlistItem] = (),
                        picks: list[PickRow] | None = None) -> Outcome:
    """``picks`` are the latest AI picker run's (None if there hasn't been one)."""
    report = health_report(categories, holdings, inv, goal_prefs, base, quotes_fn, fx_fn,
                           fundamentals_fn, today, cash_accounts=cash_accounts)
    goal_names = {c.name for c in categories}
    snaps: dict[str, dict] = {}
    for cat in categories:
        snaps[str(cat.id)] = snapshot(
            report.recommendations.get(cat.id),
            [f for f in report.findings if f.area == cat.name],
            picks=None if picks is None else [p for p in picks if p.goal_id == str(cat.id)])

    # Extras, remembered in the portfolio snapshot so each is said once.
    old_portfolio = old_snapshots.get(PORTFOLIO)
    sent = list((old_portfolio or {}).get("sent", []))
    moves, deadlines, alerts, watch_moves = [], [], [], []
    held = {(h.ticker, h.exchange) for h in holdings}
    symbols = tuple(sorted(held | {w.key for w in watchlist}))
    if symbols:
        prices, _ = history_fn(symbols, (), base, today - timedelta(days=14), today)
        days = 7 if weekly else 1
        moves = price_moves(holdings, prices, days, set(sent), today)
        unheld = [w for w in watchlist if w.key not in held]  # held ones are covered above
        watch_moves = price_moves(unheld, prices, days, set(sent), today)
        latest = {k: max(bars, key=lambda b: b.day) for k, bars in prices.items() if bars}
        alerts = watchlist_alerts(list(watchlist), latest, set(sent), today)
    values = valued_holdings(holdings, base, quotes_fn, fx_fn)
    cash = valued_cash(cash_accounts, base, fx_fn)
    progress = {c.id: goal_progress(c, [v for v in values if v.holding.category_id == c.id],
                                    today, [x for x in cash if x.account.category_id == c.id])
                for c in categories}
    deadlines = deadline_reminders(categories, progress, base, set(sent), today)
    sent += ([k for k, _ in moves] + [k for k, _ in watch_moves] + [k for k, _ in alerts]
             + [d[0] for d in deadlines])
    snaps[PORTFOLIO] = snapshot(None, [f for f in report.findings if f.area not in goal_names],
                                sent)

    if old_portfolio is None:  # first run for this person: store a baseline only
        return Outcome([], snaps, baseline=True)

    messages = []
    for cat in categories:
        change = diff(old_snapshots.get(str(cat.id)), snaps[str(cat.id)])
        if change is not None and change.due:
            messages.append(goal_message(cat, change))
    change = diff(old_portfolio, snaps[PORTFOLIO])
    if change is not None and change.due:
        messages.append(portfolio_message(change))
    if moves:
        messages.append(price_message(moves))
    if alerts or watch_moves:
        messages.append(watchlist_message(alerts, watch_moves))
    for key, cat, title, body in deadlines:
        messages.append(Message("deadline", title, body, cat.id, cat.name, {"key": key}))
    return Outcome(messages, snaps, baseline=False)
