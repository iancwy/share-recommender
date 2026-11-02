import pytest
from fastapi.testclient import TestClient

from share_recommender.api.deps import get_market_data, get_price_store, get_repository
from share_recommender.api.main import app
from tests.fakes import FakePriceStore, FakeProvider, FakeRepository


@pytest.fixture
def client():
    repo = FakeRepository()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_market_data] = lambda: FakeProvider()
    app.dependency_overrides[get_price_store] = lambda: FakePriceStore()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_requires_token():
    assert TestClient(app).get("/holdings").status_code == 401


def test_holding_crud(client):
    cat_id = client.get("/categories").json()[0]["id"]
    body = {"ticker": "vas", "exchange": "ASX", "units": "10", "buy_price": "95.5",
            "buy_date": "2026-01-02", "fees": "11", "platform": "CMC Markets",
            "category_id": cat_id}

    created = client.post("/holdings", json=body)
    assert created.status_code == 201
    hid = created.json()["id"]
    assert created.json()["ticker"] == "VAS"

    updated = client.put(f"/holdings/{hid}", json=body | {"units": "12"})
    assert updated.json()["units"] == "12"

    assert len(client.get("/holdings", params={"category_id": cat_id}).json()) == 1
    assert client.delete(f"/holdings/{hid}").status_code == 204
    assert client.get(f"/holdings/{hid}").status_code == 404


def test_rejects_invalid_holding(client):
    r = client.post("/holdings", json={"ticker": "VAS", "exchange": "ASX", "units": "0",
                                       "buy_price": "1", "buy_date": "2026-01-01"})
    assert r.status_code == 422


def test_category_with_goal(client):
    r = client.post("/categories", json={"name": "Kids private school investment fund",
                                         "target_amount": "120000", "target_date": "2032-01-01",
                                         "risk_level": "low"})
    assert r.status_code == 201
    assert r.json()["risk_level"] == "low"


def test_quote(client):
    assert client.get("/quotes/ASX/VAS").json()["price"] == "100"
    assert client.get("/quotes/ASX/XYZ").status_code == 404
    assert client.get("/quotes/MOON/VAS").status_code == 404


def test_preferences_and_recommendation(client):
    cat_id = client.get("/categories").json()[0]["id"]
    assert client.get("/preferences").status_code == 404
    assert client.get(f"/categories/{cat_id}/recommendation").status_code == 409

    prefs = {"markets": ["asx"], "direct_shares": "etf_only", "brokerage_per_trade": "5"}
    saved = client.put("/preferences", json=prefs)
    assert saved.status_code == 200 and saved.json()["markets"] == ["ASX"]
    assert client.put(f"/categories/{cat_id}/preferences",
                      json={"horizon_years": 15, "flexibility": "must_meet"}).status_code == 200

    rec = client.get(f"/categories/{cat_id}/recommendation", params={"amount": "5000"}).json()
    assert rec["mix"]["growth_pct"] == 60
    assert rec["mix"]["picks"][0]["ticker"] == "VAS"
    assert rec["buy_plan"]["amount"] == "5000"


def test_rejects_bad_preferences(client):
    assert client.put("/preferences", json={"markets": ["MOON"]}).status_code == 422
    assert client.put("/preferences", json={"markets": []}).status_code == 422


def test_dashboard_health_and_import_endpoints(client):
    from tests.test_importer import COMMSEC

    preview = client.post("/holdings/import/preview", json={"csv": COMMSEC,
                                                            "platform": "CommSec"}).json()
    assert preview["layout"] == "commsec"
    buys = [r["holding"] for r in preview["rows"] if r["importable"]]
    assert [h["ticker"] for h in buys] == ["VAS", "CBA"]
    for h in buys:
        assert client.post("/holdings", json=h).status_code == 201

    history = client.get("/portfolio/history", params={"days": 30}).json()
    assert history and set(history[0]) == {"day", "value", "invested"}

    health = client.get("/health-check").json()
    assert any("CBA" in f["title"] for f in health["findings"])
    assert health["platforms"][0]["platform"] == "CommSec"

    bad = client.post("/holdings/import/preview", json={"csv": "Date,Code\n01/01/2026,VAS\n"})
    assert bad.status_code == 422


def test_update_me_with_default_platform(client):
    r = client.put("/me", json={"display_name": "Ian", "base_currency": "AUD",
                                "default_platform": " Stake "})
    assert r.status_code == 200 and r.json()["default_platform"] == "Stake"
    assert client.put("/me", json={"base_currency": "aud"}).status_code == 422


def test_cash_accounts(client):
    body = {"institution": "ING", "account_name": "Savings Maximiser", "balance": "5000",
            "interest_rate": "5.5"}
    created = client.post("/cash-accounts", json=body)
    assert created.status_code == 201
    account_id = created.json()["id"]
    updated = client.put(f"/cash-accounts/{account_id}", json=body | {"balance": "6000"})
    assert updated.json()["balance"] == "6000"
    assert len(client.get("/cash-accounts").json()) == 1
    assert client.post("/cash-accounts", json=body | {"balance": "-1"}).status_code == 422
    assert client.delete(f"/cash-accounts/{account_id}").status_code == 204
    assert client.get("/cash-accounts").json() == []


def test_dividends(client):
    body = {"ticker": "vas", "exchange": "ASX", "pay_date": "2026-07-15", "amount": "120.50",
            "franking_credits": "30"}
    created = client.post("/dividends", json=body)
    assert created.status_code == 201 and created.json()["ticker"] == "VAS"
    assert len(client.get("/dividends").json()) == 1
    assert client.delete(f"/dividends/{created.json()['id']}").status_code == 204
    assert client.post("/dividends", json=body | {"amount": "0"}).status_code == 422


def test_notifications_endpoints(client):
    from uuid import uuid4

    repo = app.dependency_overrides[get_repository]()
    note_id = str(uuid4())
    repo.notifications = [{"id": note_id, "title": "t", "body": "b", "read_at": None,
                           "created_at": "2026-11-02T18:00:00+11:00"}]
    assert client.get("/notifications").json()[0]["id"] == note_id
    assert client.post(f"/notifications/{note_id}/read").status_code == 204
    assert repo.unread_count() == 0
    assert client.delete(f"/notifications/{note_id}").status_code == 204
    assert client.get("/notifications").json() == []

    me = client.put("/me", json={"notify_frequency": "daily", "notify_email": False}).json()
    assert (me["notify_frequency"], me["notify_email"]) == ("daily", False)
    assert client.put("/me", json={"notify_frequency": "hourly"}).status_code == 422
