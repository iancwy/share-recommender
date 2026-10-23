"""Drive the Streamlit app headlessly with fake Supabase and market data."""

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from share_recommender.core.models import HoldingIn
from tests.fakes import FakeClient, FakeProvider, FakeRepository

APP = str(Path(__file__).parents[1] / "share_recommender" / "ui" / "app.py")


def _button(at, label):
    return next(b for b in at.button if b.label == label)


@pytest.fixture
def repo():
    return FakeRepository()


def _app(repo, signed_in=True):
    patches = [
        patch("share_recommender.db.supabase_client.anon_client",
              return_value=FakeClient(signed_in)),
        patch("share_recommender.db.repository.PortfolioRepository", return_value=repo),
        patch("share_recommender.data.get_provider", return_value=FakeProvider()),
    ]
    for p in patches:
        p.start()
    at = AppTest.from_file(APP, default_timeout=30)
    return at, patches


@pytest.fixture
def run_app(repo):
    started = []

    def _run(signed_in=True):
        at, patches = _app(repo, signed_in)
        started.extend(patches)
        return at.run()

    yield _run
    for p in started:
        p.stop()


def test_login_screen_when_signed_out(run_app):
    at = run_app(signed_in=False)
    assert not at.exception
    assert [b.label for b in at.button] == ["Sign in", "Create account"]


def test_add_holding_and_value_it(run_app, repo):
    at = run_app()
    assert not at.exception

    at.text_input[0].input("vas")
    at.number_input[0].set_value(10.0)   # units
    at.number_input[1].set_value(95.5)   # buy price
    at.number_input[2].set_value(11.0)   # fees
    _button(at, "Add holding").click().run()
    assert not at.exception

    (holding,) = repo.holdings.values()
    assert holding.ticker == "VAS"
    assert holding.platform == "CMC Markets"
    assert holding.category_id is not None
    assert at.metric[0].value == "1,000.00"  # 10 units at fake price 100

    # Values refresh after a change (row selection itself can't be driven by AppTest).
    repo.update_holding(holding.id, HoldingIn(**holding.model_dump() | {"units": 12}))
    at.run()
    assert at.metric[0].value == "1,200.00"


def test_validation_errors_shown(run_app, repo):
    at = run_app()
    at.text_input[0].input("VAS")
    _button(at, "Add holding").click().run()  # units and price left at 0
    assert not repo.holdings
    assert any("greater than 0" in e.value for e in at.error)


def test_categories_page_adds_template(run_app, repo):
    at = run_app()
    at.sidebar.radio[0].set_value("Categories").run()
    assert not at.exception
    kids = next(b for b in at.button if "Kids private school" in b.label)
    kids.click().run()
    assert "Kids private school investment fund" in {c.name for c in repo.categories.values()}
