"""Drive the Streamlit app headlessly with fake Supabase and market data."""

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from share_recommender.core.models import CashAccountIn, HoldingIn
from share_recommender.core.preferences import DirectShares, GoalStyle, InvestorPreferences
from share_recommender.core.universe import Theme
from tests.fakes import FakeClient, FakeProvider, FakeRepository

APP = str(Path(__file__).parents[1] / "share_recommender" / "ui" / "app.py")


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def _go(at, page):
    """Open a page, as choosing it from the menu along the top would."""
    at.session_state["nav_page"] = page
    return at


def _type_option(selectbox, text):
    """Type a new value into a selectbox that accepts new options.

    AppTest can only pick existing options, so send the typed text the way the browser does.
    """
    from streamlit.proto.WidgetStates_pb2 import WidgetState

    state = WidgetState(id=selectbox.id, string_value=text)
    selectbox.__class__ = type("TypedSelectbox", (type(selectbox),),
                               {"_widget_state": property(lambda self: state)})
    return selectbox


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
    _go(at, "Holdings").run()

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
    assert at.metric[0].value == "1,000 AUD"  # 10 units at fake price 100

    # Values refresh after a change (row selection itself can't be driven by AppTest).
    repo.update_holding(holding.id, HoldingIn(**holding.model_dump() | {"units": 12}))
    at.run()
    assert at.metric[0].value == "1,200 AUD"


def test_validation_errors_shown(run_app, repo):
    at = run_app()
    _go(at, "Holdings").run()
    at.text_input[0].input("VAS")
    _button(at, "Add holding").click().run()  # units and price left at 0
    assert not repo.holdings
    assert any("greater than 0" in e.value for e in at.error)


def test_categories_page_adds_template(run_app, repo):
    at = run_app()
    _go(at, "Categories").run()
    assert not at.exception
    kids = next(b for b in at.button if "Kids private school" in b.label)
    kids.click().run()
    assert "Kids private school investment fund" in {c.name for c in repo.categories.values()}


def test_recommendations_need_the_interview_first(run_app):
    at = run_app()
    _go(at, "Recommendations").run()
    assert not at.exception
    _button(at, "Go to Preferences").click().run()
    assert at.title[0].value == "Preferences"


def test_interview_then_recommendation(run_app, repo):
    at = run_app()
    _go(at, "Preferences").run()
    assert not at.exception
    at.radio(key="q_direct_shares").set_value(DirectShares.ETF_ONLY)
    _button(at, "Save my answers").click().run()
    assert repo.preferences.direct_shares is DirectShares.ETF_ONLY

    (cat,) = repo.categories.values()
    at.number_input(key=f"q_horizon_years_{cat.id}").set_value(15)
    _button(at, "Save").click().run()
    assert repo.category_preferences[cat.id].horizon_years == 15

    _go(at, "Recommendations").run()
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
    _go(at, "Preferences").run()
    at.radio(key=f"q_style_{cat.id}").set_value(GoalStyle.SPECULATIVE)
    at.multiselect(key=f"q_themes_{cat.id}").set_value([Theme.AI, Theme.BIOTECH])
    _button(at, "Save").click().run()
    saved = repo.category_preferences[cat.id]
    assert saved.style is GoalStyle.SPECULATIVE and saved.themes == [Theme.AI, Theme.BIOTECH]

    _go(at, "Recommendations").run()
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
    _go(at, "Preferences").run()
    with patch("share_recommender.ai.interview.run_turn", return_value=result) as fake:
        at.chat_input(key="chat_input").set_value("I'm new and saving for a boat").run()
    assert not at.exception and fake.called
    assert any("What markets?" in m.value for m in at.markdown)
    assert repo.preferences is None  # nothing saved until confirmed

    _button(at, "Save these answers").click().run()
    assert repo.preferences.experience is Experience.BEGINNER
    assert repo.preferences.notes == "Saving for a boat."
    assert repo.category_preferences[cat.id].horizon_years == 12


def _with_holdings(repo):
    from datetime import date

    (cat,) = repo.categories.values()
    for ticker, units in [("VAS", 10), ("CBA", 5)]:
        repo.create_holding(HoldingIn(ticker=ticker, exchange="ASX", units=units, buy_price=90,
                                      buy_date=date(2026, 1, 5), platform="CMC Markets",
                                      category_id=cat.id))
    return cat


def test_dashboard(run_app, repo):
    at = run_app()
    assert not at.exception
    assert any("Nothing here yet" in i.value for i in at.info)

    _with_holdings(repo)
    at.run()
    assert not at.exception
    assert at.title[0].value == "Dashboard"
    assert at.metric[0].label == "Portfolio value"
    assert at.metric[1].value == "1,350 AUD"  # 15 units at 90
    at.radio(key="dash_by").set_value(at.radio(key="dash_by").options[1]).run()  # platform
    assert not at.exception
    at.radio(key="dash_period").set_value("All").run()
    assert not at.exception


def test_health_check_page(run_app, repo):
    _with_holdings(repo)
    at = run_app()
    _go(at, "Health check").run()
    assert not at.exception
    labels = [m.label for m in at.metric]
    assert labels == ["⛔ Alert", "⚠️ Warning", "ℹ️ Info", "✅ OK"]
    text = " ".join(m.value for m in at.markdown)
    assert "in one company (CBA)" in text and "Preferences not answered" in text


def test_import_from_pasted_csv(run_app, repo):
    from tests.test_importer import COMMSEC

    at = run_app()
    _go(at, "Import").run()
    assert not at.exception
    at.text_area(key="import_text_0").input(COMMSEC).run()
    assert not at.exception
    assert any("Found **2 purchase(s)**" in m.value for m in at.markdown)
    _button(at, "Import 2 holding(s)").click().run()
    assert not at.exception
    assert sorted(h.ticker for h in repo.holdings.values()) == ["CBA", "VAS"]
    assert {h.platform for h in repo.holdings.values()} == {"CommSec"}
    (cat,) = repo.categories.values()
    assert {h.category_id for h in repo.holdings.values()} == {cat.id}

    # Importing the same file again finds only duplicates.
    at.text_area(key="import_text_1").input(COMMSEC).run()
    assert any("2 are already in your holdings" in m.value for m in at.markdown)
    assert _button(at, "Import 0 holding(s)").disabled


def test_import_snapshot_after_transaction_history(run_app, repo):
    from datetime import date
    from decimal import Decimal

    from tests.test_importer import SNAPSHOT

    history_start = date(2024, 5, 1)
    repo.create_holding(HoldingIn(ticker="VAS", exchange="ASX", units=200, buy_price=95,
                                  fees=10, buy_date=history_start, platform="CMC Markets"))
    at = run_app()
    _go(at, "Import").run()
    at.text_area(key="import_text_0").input(SNAPSHOT).run()
    assert not at.exception
    # The date defaults to the earliest purchase already imported for CMC Markets.
    assert at.date_input(key="import_as_at_0").value == history_start
    assert any("**1** needing a price" in m.value for m in at.markdown)  # WDS
    _button(at, "Import 3 holding(s)").click().run()  # MSFT, the rest of VAS, DHHF
    assert not at.exception

    added = sorted((h for h in repo.holdings.values() if h.buy_date == history_start
                    and h.fees == 0), key=lambda h: h.ticker)
    assert [(h.ticker, h.units) for h in added] == [("DHHF", 100), ("MSFT", 10), ("VAS", 100)]
    vas = next(h for h in added if h.ticker == "VAS")
    assert vas.buy_price == Decimal("79.9")
    assert next(h for h in added if h.ticker == "MSFT").exchange == "NASDAQ"


def test_typing_a_new_platform_on_add_and_edit(run_app, repo):
    at = run_app()
    _go(at, "Holdings").run()
    # No default set: CMC Markets is preselected.
    assert at.selectbox(key="add_holding_platform").value == "CMC Markets"
    at.text_input[0].input("VAS")
    at.number_input[0].set_value(10.0)
    at.number_input[1].set_value(95.0)
    _type_option(at.selectbox(key="add_holding_platform"), "Stake")
    _button(at, "Add holding").click().run()
    assert not at.exception
    (holding,) = repo.holdings.values()
    assert holding.platform == "Stake"

    at = run_app()  # a fresh page, as after reloading
    _go(at, "Holdings").run()
    assert "Stake" in at.selectbox(key="add_holding_platform").options
    at.selectbox(key="edit_holding").set_value(holding).run()
    _type_option(at.selectbox(key=f"edit_{holding.id}_platform"), "Pearler")
    _button(at, "Save changes").click().run()
    assert not at.exception
    assert repo.holdings[holding.id].platform == "Pearler"


def test_default_platform_is_preselected(run_app, repo):
    from share_recommender.core.models import ProfileIn

    repo.update_profile(ProfileIn(display_name="Ian", default_platform="CommSec"))
    at = run_app()
    _go(at, "Holdings").run()
    assert at.selectbox(key="add_holding_platform").value == "CommSec"
    _go(at, "Import").run()
    at.text_area(key="import_text_0").input(
        "Date,Code,Quantity,Price\n01/02/2026,VGS,5,130\n").run()
    assert at.selectbox(key="import_platform_0").value == "CommSec"


def test_rename_a_platform(run_app, repo):
    from datetime import date

    for ticker, platform in [("VAS", "CMC Markets"), ("CBA", "CMC Markets"), ("NAB", "Stake")]:
        repo.create_holding(HoldingIn(ticker=ticker, exchange="ASX", units=1, buy_price=100,
                                      buy_date=date(2026, 1, 1), platform=platform))
    at = run_app()
    _go(at, "Holdings").run()
    at.selectbox(key="rename_old").set_value("CMC Markets")
    at.text_input(key="rename_new").input("CMC Invest")
    at.checkbox(key="rename_confirm").check().run()
    _button(at, "Rename platform").click().run()
    assert not at.exception
    assert sorted(h.platform for h in repo.holdings.values()) == [
        "CMC Invest", "CMC Invest", "Stake"]


def test_default_platform_on_profile_page(run_app, repo):
    at = run_app()
    _go(at, "Profile").run()
    at.text_input(key="profile_default_platform").input("CommSec")
    _button(at, "Save").click().run()
    assert not at.exception
    assert repo.profile.default_platform == "CommSec"
    assert repo.profile.platform_for_new == "CommSec"


def test_cash_page_add_edit_delete(run_app, repo):
    (cat,) = repo.categories.values()
    at = run_app()
    _go(at, "Cash").run()
    assert not at.exception
    assert any("No cash accounts yet" in i.value for i in at.info)

    _type_option(at.selectbox(key="add_cash_institution"), "ING")
    at.text_input(key="add_cash_name").input("Savings Maximiser")
    at.number_input(key="add_cash_balance").set_value(5000.0)
    at.number_input(key="add_cash_rate").set_value(5.5)
    _button(at, "Add cash account").click().run()
    assert not at.exception
    (account,) = repo.cash.values()
    assert (account.institution, account.balance, float(account.interest_rate),
            account.category_id, account.currency) == ("ING", 5000, 5.5, cat.id, "AUD")

    at = run_app()
    _go(at, "Cash").run()
    assert at.metric[0].value == "5,000 AUD"
    at.selectbox(key="edit_cash").set_value(account).run()
    at.number_input(key=f"edit_cash_{account.id}_balance").set_value(6500.0)
    _button(at, "Save changes").click().run()
    assert not at.exception
    assert repo.cash[account.id].balance == 6500

    at = run_app()
    _go(at, "Cash").run()
    at.selectbox(key="edit_cash").set_value(repo.cash[account.id]).run()
    at.checkbox(key=f"confirm_cash_{account.id}").check().run()
    _button(at, "Delete cash account").click().run()
    assert not repo.cash

    # Deleting a goal keeps its cash accounts, uncategorised.
    kept = repo.create_cash_account(CashAccountIn(institution="Ubank", balance=1,
                                                  category_id=cat.id))
    repo.delete_category(cat.id)
    assert repo.cash[kept.id].category_id is None


def test_dashboard_counts_cash(run_app, repo):
    from datetime import date

    (cat,) = repo.categories.values()
    repo.create_holding(HoldingIn(ticker="VAS", exchange="ASX", units=100, buy_price=100,
                                  buy_date=date(2026, 1, 5), category_id=cat.id))
    repo.create_cash_account(CashAccountIn(institution="ING", balance=5000, interest_rate=5,
                                           category_id=cat.id))
    at = run_app()
    assert not at.exception
    assert at.metric[0].value == "15,000 AUD"  # 100 units at the fake price of 100, plus cash


def test_dividends_page(run_app, repo):
    _with_holdings(repo)
    at = run_app()
    _go(at, "Dividends").run()
    assert not at.exception
    assert any("No dividends recorded yet" in i.value for i in at.info)

    next(s for s in at.selectbox if s.label == "Holding").set_value("VAS (ASX)")
    next(n for n in at.number_input if n.label == "Amount paid").set_value(120.5)
    next(n for n in at.number_input if n.label == "Franking credits").set_value(30.0)
    _button(at, "Add dividend").click().run()
    assert not at.exception
    (d,) = repo.dividends.values()
    assert (d.ticker, d.exchange, d.amount, d.franking_credits, d.platform) == \
        ("VAS", "ASX", Decimal("120.5"), 30, "CMC Markets")

    _go(at, "Dashboard").run()
    assert not at.exception
    assert at.metric[3].label == "Dividends, last 12 months"
    assert at.metric[3].value == "120 AUD"  # whole dollars


def _note(title, read=False, minutes=0):
    from uuid import uuid4

    return {"id": str(uuid4()), "category_id": None, "kind": "health", "title": title,
            "body": f"{title} body", "payload": {},
            "created_at": f"2026-11-02T18:{minutes:02d}:00+11:00",
            "read_at": "2026-11-02T19:00:00+11:00" if read else None, "emailed_at": None}


def test_notifications_page(run_app, repo):
    repo.notifications = [_note("Old news", read=True, minutes=1), _note("First", minutes=2),
                          _note("Second", minutes=3)]
    at = run_app()
    assert at.session_state["unread"] == 2  # shown in the menu as "Notifications (2)"
    _go(at, "Notifications").run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Second" in text and text.index("Second") < text.index("First")  # newest first
    assert text.count("**New**") == 2

    first = next(n for n in repo.notifications if n["title"] == "First")
    _button_by_key(at, f"read_{first['id']}").click().run()
    assert first["read_at"] and repo.unread_count() == 1

    _button(at, "Mark all 1 as read").click().run()
    assert repo.unread_count() == 0

    old = next(n for n in repo.notifications if n["title"] == "Old news")
    _button_by_key(at, f"delete_note_{old['id']}").click().run()
    assert [n["title"] for n in repo.notifications] == ["First", "Second"]

    _button(at, "Check for changes now").click().run()
    assert not at.exception
    assert any("no earlier check" in i.value for i in at.info)


def _button_by_key(at, key):
    return next(b for b in at.button if b.key == key)


def test_notification_settings_on_profile(run_app, repo):
    at = run_app()
    _go(at, "Profile").run()
    at.selectbox(key="profile_frequency").set_value("daily")
    at.checkbox(key="profile_email").uncheck()
    _button(at, "Save").click().run()
    assert not at.exception
    assert (repo.profile.notify_frequency, repo.profile.notify_email) == ("daily", False)
    assert repo.profile.display_name == "Ian"  # saving settings keeps the rest


def test_missing_migration_shows_a_friendly_message(run_app, repo):
    from postgrest.exceptions import APIError

    def missing_table(*_, **__):
        raise APIError({"code": "PGRST205", "message": "Could not find the table "
                        "'public.cash_accounts' in the schema cache"})

    repo.list_cash_accounts = missing_table
    repo.unread_count = missing_table
    at = run_app()  # the Dashboard reads cash accounts
    assert not at.exception
    assert any("missing an update" in e.value for e in at.error)
    assert at.session_state["unread"] == 0  # no unread count, and no crash


def test_cloud_entry_point_runs_the_app(repo):
    """streamlit_app.py (what Community Cloud runs) runs the real app on every rerun."""
    root = Path(__file__).parents[1]
    patches = [
        patch("share_recommender.db.supabase_client.anon_client", return_value=FakeClient()),
        patch("share_recommender.db.repository.PortfolioRepository", return_value=repo),
        patch("share_recommender.data.get_provider", return_value=FakeProvider()),
    ]
    for p in patches:
        p.start()
    try:
        at = AppTest.from_file(str(root / "streamlit_app.py"), default_timeout=30).run()
        assert not at.exception and at.title[0].value == "Dashboard"
        at.run()  # a rerun renders the page again, not a blank screen
        assert not at.exception and at.title[0].value == "Dashboard"
    finally:
        for p in patches:
            p.stop()
