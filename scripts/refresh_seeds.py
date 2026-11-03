"""Rewrite the seed lists: the largest ~150 shares on each exchange, from the provider.

The AI picker falls back to these when live discovery fails or is rate-limited. Run it now
and then (say every few months) and commit the CSVs:

    python scripts/refresh_seeds.py
"""

import csv
import logging
import sys

from share_recommender.core.exchanges import EXCHANGES
from share_recommender.data import get_provider
from share_recommender.data.candidates import SEEDS

LIMIT = 150


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    provider = get_provider()
    SEEDS.mkdir(exist_ok=True)
    failed = []
    for code in EXCHANGES:
        found = provider.discover(code, LIMIT)
        if not found:
            failed.append(code)
            print(f"{code}: nothing found, kept the existing file")
            continue
        with (SEEDS / f"{code}.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["ticker", "name"])
            writer.writerows((c.ticker, c.name or "") for c in found)
        print(f"{code}: {len(found)} shares")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
