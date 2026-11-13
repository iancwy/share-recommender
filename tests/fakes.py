"""In-memory stand-ins for Supabase and the market data provider."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.models import (
    CashAccount,
    CashAccountIn,
    Category,
    CategoryIn,
    Contribution,
    ContributionIn,
    Dividend,
    DividendIn,
    Holding,
    HoldingIn,
    Profile,
    WatchlistItem,
    WatchlistItemIn,
)
from share_recommender.core.picker import PickRow, PickRun
from share_recommender.core.preferences import CategoryPreferences, InvestorPreferences
from share_recommender.data.base import (
    Candidate,
    Fundamentals,
    MarketDataProvider,
    PriceBar,
    Quote,
)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")


class FakeRepository:
    def __init__(self):
        self.profile = Profile(id=USER_ID, display_name="Ian", base_currency="AUD")
        general = Category(id=uuid4(), user_id=USER_ID, name="General investing")
        self.categories: dict[UUID, Category] = {general.id: general}
        self.holdings: dict[UUID, Holding] = {}
        self.cash: dict[UUID, CashAccount] = {}
        self.dividends: dict[UUID, Dividend] = {}
        self.watchlist: dict[UUID, WatchlistItem] = {}
        self.preferences: InvestorPreferences | None = None
        self.category_preferences: dict[UUID, CategoryPreferences] = {}
        self.notifications: list[dict] = []
        self.snapshots: dict[str, dict] = {}
        self.pick_runs: list[PickRun] = []         # oldest first
        self.picks: dict[str, list[PickRow]] = {}  # run id -> picks
        self.manual_runs_today = 0
        self.contributions: dict[UUID, Contribution] = {}

    def list_contributions(self):
        return sorted(self.contributions.values(), key=lambda c: (c.start_date, str(c.id)))

    def add_contribution(self, c: ContributionIn):
        row = Contribution(id=uuid4(), user_id=USER_ID, **c.model_dump())
        self.contributions[row.id] = row
        return row

    def update_contribution(self, contribution_id, c: ContributionIn):
        if contribution_id not in self.contributions:
            raise LookupError(f"Contribution {contribution_id} not found")
        row = Contribution(id=contribution_id, user_id=USER_ID, **c.model_dump())
        self.contributions[contribution_id] = row
        return row

    def delete_contribution(self, contribution_id):
        self.contributions.pop(contribution_id, None)

    def latest_pick_run(self, include_failed=False):
        runs = [r for r in self.pick_runs if include_failed or r.status == "ok"]
        return runs[-1] if runs else None

    def list_picks(self, run_id):
        return sorted(self.picks.get(str(run_id), []), key=lambda p: p.rank)

    def save_pick_run(self, run, picks):
        run = run.model_copy(update={"id": uuid4(), "created_at": datetime.now(UTC)})
        self.pick_runs.append(run)
        self.picks[str(run.id)] = list(picks)
        if run.trigger == "manual":
            self.manual_runs_today += 1
        return str(run.id)

    def pick_runs_today(self):
        return self.manual_runs_today

    def prune_pick_runs(self, keep=10):
        for run in self.pick_runs[:-keep]:
            self.picks.pop(str(run.id), None)
        self.pick_runs = self.pick_runs[-keep:]

    def list_notifications(self, limit=50):
        return sorted(self.notifications, key=lambda n: n["created_at"], reverse=True)[:limit]

    def unread_count(self):
        return sum(1 for n in self.notifications if not n.get("read_at"))

    def mark_notification_read(self, notification_id):
        for n in self.notifications:
            if n["id"] == notification_id:
                n["read_at"] = "2026-11-01T18:00:00+11:00"

    def mark_all_notifications_read(self):
        for n in self.notifications:
            n["read_at"] = n.get("read_at") or "2026-11-01T18:00:00+11:00"

    def delete_notification(self, notification_id):
        self.notifications = [n for n in self.notifications if n["id"] != notification_id]

    def get_snapshots(self):
        return self.snapshots

    def list_watchlist(self):
        return sorted(self.watchlist.values(), key=lambda w: w.ticker)

    def get_watchlist_item(self, item_id):
        return self.watchlist.get(item_id)

    def add_to_watchlist(self, w: WatchlistItemIn):
        if any(x.key == w.key for x in self.watchlist.values()):
            raise ValueError("Already on your watchlist")
        item = WatchlistItem(id=uuid4(), user_id=USER_ID, **w.model_dump())
        self.watchlist[item.id] = item
        return item

    def update_watchlist_item(self, item_id, w: WatchlistItemIn):
        if item_id not in self.watchlist:
            raise LookupError(item_id)
        self.watchlist[item_id] = WatchlistItem(id=item_id, user_id=USER_ID, **w.model_dump())
        return self.watchlist[item_id]

    def remove_from_watchlist(self, item_id):
        self.watchlist.pop(item_id, None)

    def list_dividends(self):
        return sorted(self.dividends.values(), key=lambda d: d.pay_date, reverse=True)

    def create_dividend(self, d: DividendIn):
        dividend = Dividend(id=uuid4(), user_id=USER_ID, **d.model_dump())
        self.dividends[dividend.id] = dividend
        return dividend

    def delete_dividend(self, dividend_id):
        self.dividends.pop(dividend_id, None)

    def get_preferences(self):
        return self.preferences

    def save_preferences(self, prefs):
        self.preferences = prefs
        return prefs

    def get_category_preferences(self, category_id):
        if category_id not in self.categories:
            raise LookupError(category_id)
        return self.category_preferences.get(category_id)

    def save_category_preferences(self, category_id, prefs):
        if category_id not in self.categories:
            raise LookupError(category_id)
        self.category_preferences[category_id] = prefs
        return prefs

    def get_profile(self):
        return self.profile

    def update_profile(self, update):
        self.profile = Profile(id=USER_ID, **update.model_dump())
        return self.profile

    def rename_platform(self, old, new):
        new = (new or "").strip() or None
        moved = [hid for hid, h in self.holdings.items() if h.platform == old]
        for hid in moved:
            self.holdings[hid] = self.holdings[hid].model_copy(update={"platform": new})
        cash = [cid for cid, c in self.cash.items() if c.institution == old] if new else []
        for cid in cash:
            self.cash[cid] = self.cash[cid].model_copy(update={"institution": new})
        return len(moved) + len(cash)

    def list_cash_accounts(self, category_id=None):
        rows = [c for c in self.cash.values()
                if category_id is None or c.category_id == category_id]
        return sorted(rows, key=lambda c: c.institution)

    def create_cash_account(self, c: CashAccountIn):
        account = CashAccount(id=uuid4(), user_id=USER_ID, **c.model_dump())
        self.cash[account.id] = account
        return account

    def update_cash_account(self, account_id, c: CashAccountIn):
        if account_id not in self.cash:
            raise LookupError(account_id)
        self.cash[account_id] = CashAccount(id=account_id, user_id=USER_ID, **c.model_dump())
        return self.cash[account_id]

    def delete_cash_account(self, account_id):
        self.cash.pop(account_id, None)
        for cid, c in self.contributions.items():  # they become ordinary ones for the goal
            if c.cash_account_id == account_id:
                self.contributions[cid] = c.model_copy(update={"cash_account_id": None})

    def list_categories(self):
        return sorted(self.categories.values(), key=lambda c: c.name)

    def create_category(self, c: CategoryIn):
        cat = Category(id=uuid4(), user_id=USER_ID, **c.model_dump())
        self.categories[cat.id] = cat
        return cat

    def update_category(self, category_id, c: CategoryIn):
        if category_id not in self.categories:
            raise LookupError(category_id)
        cat = Category(id=category_id, user_id=USER_ID, **c.model_dump())
        self.categories[category_id] = cat
        return cat

    def delete_category(self, category_id):
        self.categories.pop(category_id, None)
        for hid, h in self.holdings.items():
            if h.category_id == category_id:
                self.holdings[hid] = h.model_copy(update={"category_id": None})
        for cid, c in self.cash.items():
            if c.category_id == category_id:
                self.cash[cid] = c.model_copy(update={"category_id": None})
        for wid, w in self.watchlist.items():
            if w.category_id == category_id:
                self.watchlist[wid] = w.model_copy(update={"category_id": None})
        for cid, c in self.contributions.items():  # they become whole-portfolio ones
            if c.category_id == category_id:
                self.contributions[cid] = c.model_copy(update={"category_id": None})

    def list_holdings(self, category_id=None):
        rows = [h for h in self.holdings.values()
                if category_id is None or h.category_id == category_id]
        return sorted(rows, key=lambda h: h.buy_date, reverse=True)

    def get_holding(self, holding_id):
        return self.holdings.get(holding_id)

    def create_holding(self, h: HoldingIn):
        holding = Holding(id=uuid4(), user_id=USER_ID, **h.model_dump())
        self.holdings[holding.id] = holding
        return holding

    def update_holding(self, holding_id, h: HoldingIn):
        if holding_id not in self.holdings:
            raise LookupError(holding_id)
        holding = Holding(id=holding_id, user_id=USER_ID, **h.model_dump())
        self.holdings[holding_id] = holding
        return holding

    def delete_holding(self, holding_id):
        self.holdings.pop(holding_id, None)


class FakeProvider(MarketDataProvider):
    """Prices default to 100 for any ticker in ``prices``; FX is 1 AUD = 0.5 of anything else."""

    name = "fake"

    def __init__(self, prices: dict[tuple[str, str], str] | None = None,
                 dividend_yields: dict[tuple[str, str], str] | None = None,
                 price_everything: bool = False,
                 fundamentals: dict[tuple[str, str], Fundamentals] | None = None,
                 discovered: dict[str, list[Candidate]] | None = None):
        self.prices = prices or {("VAS", "ASX"): "100"}
        self.dividend_yields = dividend_yields or {}
        self.price_everything = price_everything
        self.fundamentals = fundamentals or {}
        self.discovered = discovered or {}
        self.fundamentals_requests: list[tuple[str, str]] = []

    def get_quote(self, ticker, exchange):
        price = self.prices.get((ticker, exchange), "100" if self.price_everything else None)
        if price is None:
            return None
        return Quote(ticker, exchange, Decimal(price), EXCHANGES[exchange].currency,
                     datetime(2026, 10, 23, tzinfo=UTC))

    def get_history(self, ticker, exchange, start: date, end: date):
        return [PriceBar(start, Decimal("1"))]

    def get_fx_rate(self, base, quote):
        return Decimal(1) if base == quote else Decimal("0.5")

    def discover(self, exchange, limit=150):
        return self.discovered.get(exchange, [])[:limit]

    def get_fundamentals(self, ticker, exchange):
        self.fundamentals_requests.append((ticker, exchange))
        if (ticker, exchange) in self.fundamentals:
            return self.fundamentals[(ticker, exchange)]
        y = self.dividend_yields.get((ticker, exchange))
        return Fundamentals(dividend_yield=Decimal(y)) if y else None


class FakeAuth:
    def __init__(self, signed_in: bool):
        user = SimpleNamespace(email="ian@example.com", id=str(USER_ID))
        self._session = SimpleNamespace(user=user) if signed_in else None

    def get_session(self):
        return self._session

    def sign_out(self):
        self._session = None


class FakeClient:
    def __init__(self, signed_in: bool = True):
        self.auth = FakeAuth(signed_in)


class FakePriceStore:
    """In-memory PriceStore that records what was saved."""

    def __init__(self):
        self.bars: dict[tuple[str, str], dict[date, PriceBar]] = {}
        self.sync: dict[tuple[str, str], tuple[date, date, datetime]] = {}
        self.saves: list[tuple[str, str, date, date]] = []

    def get_sync(self, exchange, ticker):
        return self.sync.get((exchange, ticker))

    def get_bars(self, exchange, ticker, start, end):
        bars = self.bars.get((exchange, ticker), {})
        return [bars[d] for d in sorted(bars) if start <= d <= end]

    def save(self, exchange, ticker, bars, first_day, last_day):
        self.bars.setdefault((exchange, ticker), {}).update({b.day: b for b in bars})
        self.sync[(exchange, ticker)] = (first_day, last_day, datetime.now(UTC))
        self.saves.append((exchange, ticker, first_day, last_day))


class FakeFundamentalsStore:
    """In-memory fundamentals cache; set ``broken`` to make every call fail."""

    def __init__(self, items=None, broken=False):
        self.items: dict = dict(items or {})  # key -> (Fundamentals, fetched_at)
        self.broken = broken
        self.saved: list = []

    def get_many(self, keys):
        if self.broken:
            raise RuntimeError("relation fundamentals_cache does not exist")
        return {k: self.items[k] for k in keys if k in self.items}

    def save_many(self, items):
        if self.broken:
            raise RuntimeError("relation fundamentals_cache does not exist")
        self.saved.append(dict(items))
        self.items.update({k: (f, datetime.now(UTC)) for k, f in items.items()})


def rising_history(symbols, start, end):
    """A year and a bit of steadily rising daily closes for every symbol (for the picker)."""
    days = (end - start).days
    return {s: [PriceBar(start + timedelta(days=d), Decimal(50) + Decimal(d) / 10 + len(s[0]))
                for d in range(days + 1)] for s in symbols}


class StubRanker:
    """Stands in for Claude: buys the top two of each shortlist, holds or watches the rest."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.payloads: list[dict] = []

    def __call__(self, payload):
        from share_recommender.ai.picker import Ranked
        from share_recommender.core.picker import AIPick, GoalPicks, PickerOutput

        self.payloads.append(payload)
        if self.error:
            raise self.error
        goals = []
        for g in payload["goals"]:
            picks, buys = [], 0
            for n, item in enumerate(g["shortlist"], start=1):
                if item["source"] == "held":
                    action = "hold"
                elif buys < 2 and item["score"] is not None:
                    action, buys = "buy", buys + 1
                elif item["source"] == "watchlist":
                    action = "watch"
                else:
                    continue
                picks.append(AIPick(ticker=item["ticker"], exchange=item["exchange"],
                                    action=action, conviction="high", rank=n,
                                    rationale=f"{item['ticker']} scores well.",
                                    risks="Prices fall."))
            goals.append(GoalPicks(goal=g["goal"], summary=f"Picks for {g['name']}.",
                                   picks=picks))
        return Ranked(PickerOutput(goals=goals), "claude-opus-5-5", 5000, 1500)
