"""Run the whole AI picker for a sample profile, against live market data and Claude.

No database: the profile file holds the interview answers, goals, holdings and watchlist.
Prints each goal's shortlist and Claude's picks. Needs ANTHROPIC_API_KEY (in .env or the
environment) unless you pass --screen-only.

    python scripts/picker_dry_run.py --profile tests/fixtures/picker/balanced.json
    python scripts/picker_dry_run.py --profile tests/fixtures/picker/income.json --screen-only
"""

import argparse
import json
import logging
import sys
import time
import warnings
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from share_recommender.ai.client import AIUnavailable
from share_recommender.ai.picker import rank
from share_recommender.core.models import Category, Holding, WatchlistItem
from share_recommender.core.picker import AI_DISCLAIMER
from share_recommender.core.preferences import CategoryPreferences, InvestorPreferences
from share_recommender.data import get_provider
from share_recommender.data.candidates import candidate_pool
from share_recommender.picker_service import run_picker
from share_recommender.recommendations import provider_fns

USER = UUID(int=0)


def load_profile(path: Path) -> dict:
    """The run_picker arguments a profile file describes."""
    data = json.loads(path.read_text(encoding="utf-8"))
    categories, prefs, by_name = [], {}, {}
    for g in data["goals"]:
        c = Category(id=uuid4(), user_id=USER, **{k: v for k, v in g.items() if k != "prefs"})
        categories.append(c)
        by_name[c.name] = c.id
        if g.get("prefs") is not None:
            prefs[c.id] = CategoryPreferences.model_validate(g["prefs"])
    holdings = [Holding(id=uuid4(), user_id=USER, category_id=by_name.get(h.pop("goal", None)),
                        **h) for h in data.get("holdings", [])]
    watchlist = [WatchlistItem(id=uuid4(), user_id=USER,
                               category_id=by_name.get(w.pop("goal", None)), **w)
                 for w in data.get("watchlist", [])]
    return {"categories": categories, "goal_prefs": prefs, "holdings": holdings,
            "cash_accounts": [], "watchlist": watchlist,
            "inv": InvestorPreferences.model_validate(data["investor"]),
            "base": data.get("base_currency", "AUD")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--screen-only", action="store_true",
                        help="Stop before asking Claude and print the shortlists")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    warnings.filterwarnings("ignore", message=".*inplace keyword")  # pandas, inside yfinance
    for noisy in ("httpx", "yfinance", "peewee", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    provider = get_provider()
    quotes_fn, fx_fn, fundamentals_fn = provider_fns(provider)

    def rank_fn(payload):
        if args.screen_only:
            raise AIUnavailable("Stopped before asking Claude (--screen-only).")
        return rank(payload)

    started = time.monotonic()
    result = run_picker(
        **load_profile(args.profile), quotes_fn=quotes_fn, fx_fn=fx_fn,
        fundamentals_fn=fundamentals_fn,
        history_fn=lambda s, start, end: provider.get_many_history(list(s), start, end),
        pool_fn=lambda markets: candidate_pool(provider, markets), rank_fn=rank_fn,
        today=date.today(), trigger="manual",
        progress=lambda stage: print(f"[{time.monotonic() - started:5.1f}s] {stage}"))
    run = result.run
    print(f"\nPool: {run.pool_size} shares and ETFs; shortlisted: {run.shortlisted}; "
          f"took {time.monotonic() - started:.0f}s")
    if run.input_tokens:
        print(f"Claude ({run.model}): {run.input_tokens} input tokens, "
              f"{run.output_tokens} output tokens")
    for skipped in run.summary.get("skipped", []):
        print(f"Skipped {skipped}: no interview answers")

    for goal_id, shortlist in result.shortlists.items():
        goal = run.summary["goals"][goal_id]
        print(f"\n=== {goal['name']} ===")
        print("Filters:", ", ".join(f"{k} {v}" for k, v in shortlist.counts.items()))
        for i in shortlist.items:
            score = f"{i.score:5.1f}" if i.score is not None else "  -  "
            print(f"  {score}  {i.key[0]:>6} {i.key[1]:<6} {i.source:<9} {i.sector or '':<22} "
                  f"{(i.name or '')[:34]}")
        if goal.get("summary"):
            print(f"\n  Claude: {goal['summary']}")
        for p in [p for p in result.picks if p.goal_id == goal_id]:
            weight = f" {p.suggested_weight}%" if p.suggested_weight is not None else ""
            print(f"\n  {p.rank}. {p.action.upper():<6} {p.ticker} ({p.exchange}) "
                  f"{p.conviction}{weight}\n     {p.rationale}\n     Risks: {p.risks}")
    for w in run.summary.get("warnings", []):
        print(f"\nWarning: {w}")
    if run.status == "failed":
        print(f"\n{run.error}")
    print(f"\n{AI_DISCLAIMER}")
    return 0 if run.status == "ok" or args.screen_only else 1


if __name__ == "__main__":
    sys.exit(main())
