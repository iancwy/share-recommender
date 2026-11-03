"""The pool of shares the AI picker screens, per exchange.

Not pure: it asks the provider's screener (``discover``). If that fails or returns nothing,
it falls back to the seed list for the exchange (``data/seeds/<EXCHANGE>.csv``, refreshed by
``scripts/refresh_seeds.py``). Every share on the curated list (``core/universe.py``) on those
exchanges is added too, so the pool is never empty.
"""

import csv
import logging
from pathlib import Path

from share_recommender.core.universe import SHARES, SPECULATIVE_SHARES
from share_recommender.data.base import Candidate, MarketDataProvider

log = logging.getLogger(__name__)

SEEDS = Path(__file__).with_name("seeds")


def seed_list(exchange: str) -> list[Candidate]:
    """The saved list of an exchange's largest shares: tickers and names only."""
    path = SEEDS / f"{exchange}.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [Candidate(row["ticker"], exchange, row.get("name") or None, "share", None, None)
                for row in csv.DictReader(f) if row.get("ticker")]


def candidate_pool(provider: MarketDataProvider, exchanges: list[str], limit: int = 150
                   ) -> list[Candidate]:
    pool: dict[tuple[str, str], Candidate] = {}
    for exchange in exchanges:
        found: list[Candidate] = []
        try:
            found = provider.discover(exchange, limit)
        except Exception:
            log.warning("Discovery failed for %s", exchange, exc_info=True)
        if not found:
            found = seed_list(exchange)[:limit]
            log.info("Using the seed list for %s (%d shares)", exchange, len(found))
        for c in found:
            pool.setdefault(c.key, c)
        for s in SHARES + SPECULATIVE_SHARES:
            if s.exchange == exchange:
                pool.setdefault(s.key, Candidate(s.ticker, s.exchange, s.name, "share", None,
                                                 None))
    return list(pool.values())
