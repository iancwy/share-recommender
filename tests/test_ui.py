"""Drive the Streamlit app headlessly with fake Supabase and market data."""

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from share_recommender.core.models import HoldingIn
from share_recommender.core.preferences import DirectShares, GoalStyle, InvestorPreferences
from share_recommender.core.universe import Theme
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


def test_recommendations_need_the_interview_first(run_app):
    at = run_app()
    at.sidebar.radio[0].set_value("Recommendations").run()
    assert not at.exception
    _button(at, "Go to Preferences").click().run()
    assert at.title[0].value == "Preferences"


def test_interview_then_recommendation(run_app, repo):
    at = run_app()
    at.sidebar.radio[0].set_value("Preferences").run()
    assert not at.exception
    at.radio(key="q_direct_shares").set_value(DirectShares.ETF_ONLY)
    _button(at, "Save my answers").click().run()
    assert repo.preferences.direct_shares is DirectShares.ETF_ONLY

    (cat,) = repo.categories.values()
    at.number_input(key=f"q_horizon_years_{cat.id}").set_value(15)
    _button(at, "Save").click().run()
    assert repo.category_preferences[cat.id].horizon_years == 15

    at.sidebar.radio[0].set_value("Recommendations").run()
    assert not at.exception
    assert at.metric[0].value == "70%"
    at.number_input(key="rec_amount").set_value(5000.0).run()
    assert not at.exception
    assert any("What to buy" in s.value for s in at.subheader)

    with patch("share_recommender.ai.explain.explain", return_value="Plain words.") as fake:
        _button(at, "Explain it in plain English").click().run()
    assert fake.called
    assert any("Plain words." in m.value for m in at.markdown)


def test_speculative_goal_from_the_interview(run_app, repo):
    repo.preferences = InvestorPreferences()
    (cat,) = repo.categories.values()
    at = run_app()
    at.sidebar.radio[0].set_value("Preferences").run()
    at.radio(key=f"q_style_{cat.id}").set_value(GoalStyle.SPECULATIVE)
    at.multiselect(key=f"q_themes_{cat.id}").set_value([Theme.AI, Theme.BIOTECH])
    _button(at, "Save").click().run()
    saved = repo.category_preferences[cat.id]
    assert saved.style is GoalStyle.SPECULATIVE and saved.themes == [Theme.AI, Theme.BIOTECH]

    at.sidebar.radio[0].set_value("Recommendations").run()
    assert not at.exception
    assert at.metric[0].value == "Speculative"


def test_chat_fills_the_questionnaire_for_review(run_app, repo):
    from share_recommender.ai.interview import Draft, TurnResult
    from share_recommender.core.preferences import CategoryPreferences, Experience

    (cat,) = repo.categories.values()
    draft = Draft(InvestorPreferences(experience=Experience.BEGINNER, notes="Saving for a boat."),
                  {cat.id: CategoryPreferences(horizon_years=12)},
                  answered={"experience", "notes"}, goal_answered={cat.id: {"horizon_years"}})
    result = TurnResult("Got it. What markets?", draft, [])

    at = run_app()
    at.sidebar.radio[0].set_value("Preferences").run()
    with patch("share_recommender.ai.interview.run_turn", return_value=result) as fake:
        at.chat_input(key="chat_input").set_value("I'm new and saving for a boat").run()
    assert not at.exception and fake.called
    assert any("What markets?" in m.value for m in at.markdown)
    assert repo.preferences is None  # nothing saved until confirmed

    _button(at, "Save these answers").click().run()
    assert repo.preferences.experience is Experience.BEGINNER
    assert repo.preferences.notes == "Saving for a boat."
    assert repo.category_preferences[cat.id].horizon_years == 12
