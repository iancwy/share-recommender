"""Run the AI picker: candidate pool -> rules screen per goal -> Claude ranks -> checks.

Shared by the Streamlit UI, the API, the scheduled job and ``scripts/picker_dry_run.py``.
Every fetch and the Claude call are passed in, so tests use fakes. The caller saves the run
and its picks (``repo.save_pick_run``) and prunes old runs.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from share_recommender.ai.client import AIUnavailable
from share_recommender.ai.picker import Ranked
from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.models import CashAccount, Category, Holding, WatchlistItem
from share_recommender.core.picker import (
    PickerGoal,
    PickRow,
    PickRun,
    build_payload,
    check,
    summaries,
)
from share_recommender.core.preferences import (
    CategoryPreferences,
    DirectShares,
    InvestorPreferences,
)
from share_recommender.core.recommender import holding_values, is_speculative
from share_recommender.core.screen import (
    SIZE_LIMITS,
    GoalScreenInput,
    Key,
    ScreenRow,
    Shortlist,
    momentum,
    screen,
)
from share_recommender.core.universe import ETFS, AssetClass, EtfStyle, Kind, lookup
from share_recommender.data.base import Candidate, PriceBar
from share_recommender.recommendations import (
    FundamentalsFn,
    FxFn,
    QuotesFn,
    Symbols,
    build_recommendation,
)

log = logging.getLogger(__name__)

PoolFn = Callable[[list[str]], list[Candidate]]
HistoryFn = Callable[[Symbols, date, date], dict[Key, list[PriceBar]]]
RankFn = Callable[[dict], Ranked]
ProgressFn = Callable[[str], None]

DAILY_LIMIT = 5           # manual runs per person per day
KEEP_RUNS = 10
HISTORY_PER_GOAL = 60     # price histories fetched per goal, largest companies first
HISTORY_DAYS = 400
REUSE_HOURS = 24
NOT_READY = ("Do the \"About you\" part of the interview first (Interview page), so the "
             "picker knows your markets and what you want from your shares.")


class PickerNotReady(ValueError):
    """The person hasn't answered enough of the interview; the message is safe to show."""


@dataclass
class PickRunResult:
    run: PickRun
    picks: list[PickRow]
    shortlists: dict[str, Shortlist] = field(default_factory=dict)  # by category id
    reused: bool = False


def input_hash(payload: dict) -> str:
    """Hash of everything Claude would see except today's date."""
    body = json.dumps({k: v for k, v in payload.items() if k != "today"}, sort_keys=True,
                      default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def _pool(inv: InvestorPreferences, pool_fn: PoolFn, extra: set[Key]) -> list[ScreenRow]:
    rows = {c.key: ScreenRow(c) for c in pool_fn(list(inv.markets))}
    for etf in ETFS:  # the curated ETFs: candidates for every goal, scored like the rest
        if (etf.exchange in inv.markets and etf.key not in rows
                and etf.style is not EtfStyle.DIVERSIFIED
                and AssetClass.CASH not in etf.mix):
            rows[etf.key] = ScreenRow(Candidate(etf.ticker, etf.exchange, etf.name, "etf", None,
                                                None), source="universe")
    for key in sorted(extra - set(rows)):  # held shares and watchlist items always join
        inst = lookup(*key)
        kind = "etf" if inst and inst.kind is Kind.ETF else "share"
        rows[key] = ScreenRow(Candidate(key[0], key[1], inst.name if inst else None, kind, None,
                                        None))
    return list(rows.values())


def _min_cap(goals: list[GoalScreenInput]) -> Decimal:
    return min(SIZE_LIMITS["speculative" if g.speculative else g.category.risk_level][0]
               for g in goals)


def _cap_in_base(row: ScreenRow, base: str, fx: dict) -> Decimal | None:
    cap = row.candidate.market_cap or (row.fundamentals.market_cap if row.fundamentals else None)
    currency = EXCHANGES[row.candidate.exchange].currency
    rate = Decimal(1) if currency == base else fx.get(currency)
    return cap / rate if cap is not None and rate else None


def run_picker(*, categories: list[Category], goal_prefs: dict[UUID, CategoryPreferences | None],
               holdings: list[Holding], cash_accounts: list[CashAccount],
               watchlist: list[WatchlistItem], inv: InvestorPreferences | None, base: str,
               quotes_fn: QuotesFn, fx_fn: FxFn, fundamentals_fn: FundamentalsFn,
               history_fn: HistoryFn, pool_fn: PoolFn, rank_fn: RankFn, today: date,
               trigger: str, latest: PickRun | None = None, now: datetime | None = None,
               progress: ProgressFn | None = None) -> PickRunResult:
    """One run for one person. Raises ``PickerNotReady`` without the "About you" answers;
    otherwise always returns a run, ``failed`` with a friendly ``error`` if something broke."""
    if inv is None:
        raise PickerNotReady(NOT_READY)
    now = now or datetime.now(UTC)
    say = progress or (lambda _: None)
    goals_in = [c for c in sorted(categories, key=lambda c: c.name) if goal_prefs.get(c.id)]
    skipped = [c.name for c in sorted(categories, key=lambda c: c.name)
               if not goal_prefs.get(c.id)]
    summary: dict = {"goals": {}, "warnings": [], "skipped": skipped}
    run = PickRun(trigger=trigger, summary=summary)
    if not goals_in:
        run.status, run.error = "failed", ("None of your goals have interview answers yet. "
                                           "Answer the goal questions on the Interview page.")
        return PickRunResult(run, [])

    shortlists: dict[str, Shortlist] = {}
    try:
        # 1. The rules recommendation for each goal (target mix and gaps).
        screens: list[tuple[GoalScreenInput, list[Holding], object]] = []
        for c in goals_in:
            prefs = goal_prefs[c.id]
            mine = [h for h in holdings if h.category_id == c.id]
            cash = [a for a in cash_accounts if a.category_id == c.id]
            rec = build_recommendation(c, inv, prefs, mine, base, quotes_fn, fx_fn,
                                       fundamentals_fn, today=today, all_holdings=holdings,
                                       cash_accounts=cash)
            screens.append((GoalScreenInput(c, prefs, is_speculative(c, prefs), rec.mix),
                            mine, rec))

        # 2. The candidate pool, with fundamentals for what survives a first cheap pass.
        watch_keys = {(w.ticker, w.exchange) for w in watchlist}
        held_keys = {(h.ticker, h.exchange) for h in holdings}
        say(f"Finding shares on {', '.join(inv.markets)}…")
        pool = _pool(inv, pool_fn, watch_keys | held_keys)
        run.pool_size = len(pool)
        currencies = tuple(sorted({EXCHANGES[r.candidate.exchange].currency for r in pool}
                                  - {base}))
        fx = fx_fn(base, currencies) if currencies else {}
        min_cap = _min_cap([s[0] for s in screens])
        keep = [r for r in pool if r.key in watch_keys | held_keys or (
            r.candidate.ticker not in inv.avoid_tickers
            and not (inv.direct_shares is DirectShares.ETF_ONLY
                     and r.candidate.kind == "share")
            and (r.candidate.kind == "etf" or (cap := _cap_in_base(r, base, fx)) is None
                 or cap >= min_cap))]
        say("Checking fundamentals…")
        fundamentals = fundamentals_fn(tuple(sorted(r.key for r in keep)))
        rows = [ScreenRow(r.candidate, fundamentals.get(r.key), None, r.source) for r in keep]

        # 3. Price history for the largest ~60 per goal (and everything held or watched).
        by_size = sorted(rows, key=lambda r: (-(_cap_in_base(r, base, fx) or 0), r.key))
        wanted = {r.key for r in by_size[:HISTORY_PER_GOAL * len(screens)]}
        wanted |= {r.key for r in rows if r.candidate.kind == "etf" or r.source == "universe"}
        wanted |= watch_keys | held_keys
        say("Looking at price trends…")
        history = history_fn(tuple(sorted(wanted)), today - timedelta(days=HISTORY_DAYS), today)
        rows = [_with_history(r, history.get(r.key) or [], today) for r in rows]

        # 4. Screen each goal.
        goals: list[PickerGoal] = []
        quotes = quotes_fn(tuple(sorted(held_keys))) if held_keys else {}
        for n, (g, mine, rec) in enumerate(screens, start=1):
            held = {(h.ticker, h.exchange) for h in mine}
            watched = {(w.ticker, w.exchange) for w in watchlist
                       if w.category_id in (None, g.category.id)}
            shortlist = screen(g, rows, inv, held, watched, base, fx)
            shortlists[str(g.category.id)] = shortlist
            values, _ = holding_values(mine, quotes, fx, base)
            total = rec.gaps.total_value
            pct = {k: v / total * 100 for k, v in values.items()} if total else {}
            goals.append(PickerGoal(f"g{n}", g.category, g.prefs, g.speculative, rec,
                                    shortlist, pct))
            summary["goals"][str(g.category.id)] = {"name": g.category.name,
                                                    "counts": shortlist.counts}
        run.shortlisted = sum(len(s.items) for s in shortlists.values())

        # 5. Reuse a recent run on the same inputs, otherwise ask Claude.
        payload = build_payload(inv, base, goals, today)
        run.input_hash = input_hash(payload)
        if (latest is not None and latest.status == "ok" and latest.input_hash == run.input_hash
                and latest.created_at and now - latest.created_at < timedelta(hours=REUSE_HOURS)):
            return PickRunResult(latest, [], shortlists, reused=True)
        say("Asking Claude to rank the shortlist…")
        ranked = rank_fn(payload)
        run.model = ranked.model
        run.input_tokens, run.output_tokens = ranked.input_tokens, ranked.output_tokens
        picks, warnings = check(ranked.output, goals)
        for goal_id, text in summaries(ranked.output, goals).items():
            summary["goals"][goal_id]["summary"] = text
        summary["warnings"] = warnings
        return PickRunResult(run, picks, shortlists)
    except AIUnavailable as exc:
        run.status, run.error = "failed", str(exc)
    except Exception as exc:  # market data or anything else: save what happened
        log.exception("AI picker run failed")
        run.status, run.error = "failed", f"The picker couldn't finish: {exc}"
    return PickRunResult(run, [], shortlists)


def _with_history(row: ScreenRow, bars: list[PriceBar], today: date) -> ScreenRow:
    m = momentum(bars, today)
    c = row.candidate
    if bars and c.price is None:  # seed-list rows have no price: use the last close
        c = replace(c, price=bars[-1].close)
    if c.above_200d is None and len(bars) >= 200:
        c = replace(c, above_200d=bars[-1].close > sum(b.close for b in bars[-200:]) / 200)
    return ScreenRow(c, row.fundamentals, m, row.source)
