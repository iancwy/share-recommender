"""In-memory stand-ins for Supabase and the market data provider."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.models import (
    CashAccount,
    CashAccountIn,
    Category,
    CategoryIn,
    Holding,
    HoldingIn,
    Profile,
)
from share_recommender.core.preferences import CategoryPreferences, InvestorPreferences
from share_recommender.data.base import Fundamentals, MarketDataProvider, PriceBar, Quote

USER_ID = UUID("11111111-1111-1111-1111-111111111111")


class FakeRepository:
    def __init__(self):
        self.profile = Profile(id=USER_ID, display_name="Ian", base_currency="AUD")
        general = Category(id=uuid4(), user_id=USER_ID, name="General investing")
        self.categories: dict[UUID, Category] = {general.id: general}
        self.holdings: dict[UUID, Holding] = {}
        self.cash: dict[UUID, CashAccount] = {}
        self.preferences: InvestorPreferences | None = None
        self.category_preferences: dict[UUID, CategoryPreferences] = {}

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
                 price_everything: bool = False):
        self.prices = prices or {("VAS", "ASX"): "100"}
        self.dividend_yields = dividend_yields or {}
        self.price_everything = price_everything

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

    def get_fundamentals(self, ticker, exchange):
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
