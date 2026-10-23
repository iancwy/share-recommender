"""In-memory stand-ins for Supabase and the market data provider."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

from share_recommender.core.models import Category, CategoryIn, Holding, HoldingIn, Profile
from share_recommender.data.base import MarketDataProvider, PriceBar, Quote

USER_ID = UUID("11111111-1111-1111-1111-111111111111")


class FakeRepository:
    def __init__(self):
        self.profile = Profile(id=USER_ID, display_name="Ian", base_currency="AUD")
        general = Category(id=uuid4(), user_id=USER_ID, name="General investing")
        self.categories: dict[UUID, Category] = {general.id: general}
        self.holdings: dict[UUID, Holding] = {}

    def get_profile(self):
        return self.profile

    def update_profile(self, display_name, base_currency):
        self.profile = Profile(id=USER_ID, display_name=display_name, base_currency=base_currency)
        return self.profile

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
    name = "fake"

    def __init__(self, prices: dict[tuple[str, str], str] | None = None):
        self.prices = prices or {("VAS", "ASX"): "100"}

    def get_quote(self, ticker, exchange):
        price = self.prices.get((ticker, exchange))
        if price is None:
            return None
        return Quote(ticker, exchange, Decimal(price), "AUD", datetime(2026, 10, 23, tzinfo=UTC))

    def get_history(self, ticker, exchange, start: date, end: date):
        return [PriceBar(start, Decimal("1"))]


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
