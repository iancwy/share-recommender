import pytest
from fastapi.testclient import TestClient

from share_recommender.api.deps import get_market_data, get_repository
from share_recommender.api.main import app
from tests.fakes import FakeProvider, FakeRepository


@pytest.fixture
def client():
    repo = FakeRepository()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_market_data] = lambda: FakeProvider()
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
