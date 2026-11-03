"""snapshot / diff / compose, the price-move and deadline extras, and build_notifications."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from share_recommender.core.health import Finding, Severity
from share_recommender.core.models import Category, Holding
from share_recommender.core.notifications import (
    PORTFOLIO,
    Change,
    Message,
    compose_email,
    deadline_reminders,
    diff,
    goal_message,
    months_until,
    price_moves,
    snapshot,
)
from share_recommender.core.recommender import Gaps, Pick, Recommendation, TargetMix
from share_recommender.data.base import PriceBar
from share_recommender.notifier import build_notifications
from share_recommender.recommendations import provider_fns
from tests.fakes import USER_ID, FakeProvider

TODAY = date(2026, 11, 2)  # a Monday
GOAL = Category(id=uuid4(), user_id=USER_ID, name="Kids fund")


def _rec(**weights):
    picks = [Pick(ticker=t, exchange="ASX", name=t, kind="etf", asset_class="au_shares",
                  currency="AUD", weight=Decimal(w), reason="") for t, w in weights.items()]
    return Recommendation(category_id=GOAL.id, category_name=GOAL.name,
                          mix=TargetMix(growth_pct=70, horizon_years=10, slices=[], picks=picks,
                                        reasons=[], warnings=[]),
                          gaps=Gaps(base_currency="AUD", total_value=0, slices=[], notes=[]))


def _f(title, severity=Severity.WARN, area="Kids fund"):
    return Finding(severity, area, title, "detail")


def test_first_run_is_a_baseline():
    assert diff(None, snapshot(_rec(VAS="50"), [])) is None


def test_picks_added_removed_and_reweighted():
    old = snapshot(_rec(VAS="50", VGS="40", AAA="10"), [])

    added = diff(old, snapshot(_rec(VAS="50", VGS="40", AAA="7", VGE="3"), []))
    assert added.due and added.added == ["VGE (ASX) at 3%"] and not added.reweighted

    removed = diff(old, snapshot(_rec(VAS="55", VGS="45"), []))
    assert removed.due and removed.removed == ["AAA (ASX)"]

    small = diff(old, snapshot(_rec(VAS="54", VGS="36", AAA="10"), []))
    assert not small.due  # 4 points: under the threshold

    big = diff(old, snapshot(_rec(VAS="55", VGS="35", AAA="10"), []))
    assert big.due and big.reweighted == ["VAS (ASX) 50% → 55%", "VGS (ASX) 40% → 35%"]


def test_new_findings_trigger_but_cleared_ones_do_not():
    old = snapshot(_rec(VAS="100"), [_f("Off its target mix by 6 points")])
    same = diff(old, snapshot(_rec(VAS="100"), [_f("Off its target mix by 8 points")]))
    assert not same.due  # same issue, different number

    new = diff(old, snapshot(_rec(VAS="100"), [_f("Off its target mix by 6 points"),
                                               _f("Behind its target")]))
    assert new.due and new.new_findings == [{"severity": "warning", "title": "Behind its target"}]

    cleared = diff(old, snapshot(_rec(VAS="100"), []))
    assert not cleared.due and cleared.cleared == ["Off its target mix by 6 points"]

    ok_only = snapshot(_rec(VAS="100"), [_f("On its target mix", Severity.OK)])
    assert ok_only["findings"] == {}


def test_goal_message_and_email():
    change = Change(added=["VGE (ASX) at 7%"], new_findings=[
        {"severity": "alert", "title": "Off its target mix by 12 points"}],
        cleared=["Behind its target"])
    m = goal_message(GOAL, change)
    assert (m.kind, m.title, m.category_id) == ("recommendation", "Recommendations changed",
                                                GOAL.id)
    assert "New suggestions: VGE (ASX) at 7%." in m.body
    assert "Alert: Off its target mix by 12 points." in m.body
    assert "Good news, now resolved: Behind its target." in m.body
    assert goal_message(GOAL, Change(new_findings=[{"severity": "warning", "title": "x"}])
                        ).kind == "health"

    other = Message("health", "1 new portfolio issue", "Warning: <b>.")
    subject, text, html = compose_email([m, other], "Ian", "https://ian-shares.streamlit.app")
    assert subject == "Share Recommender: 2 updates for Kids fund"
    assert text.startswith("Hi Ian,") and "KIDS FUND: RECOMMENDATIONS CHANGED" in text
    assert "not personal financial advice" in text and "Profile page" in text
    assert "&lt;b&gt;" in html and "https://ian-shares.streamlit.app" in html
    assert compose_email([m], None, "x")[0] == \
        "Share Recommender: Kids fund: Recommendations changed"


def _h(ticker, units, exchange="ASX"):
    return Holding(id=uuid4(), user_id=USER_ID, ticker=ticker, exchange=exchange, units=units,
                   buy_price="10", buy_date=date(2026, 1, 1))


def test_price_moves_daily_and_weekly():
    bars = {("LYC", "ASX"): [PriceBar(date(2026, 10, 23), Decimal("13.00")),
                             PriceBar(date(2026, 10, 29), Decimal("12.00")),
                             PriceBar(date(2026, 10, 30), Decimal("11.00"))],
            ("VAS", "ASX"): [PriceBar(date(2026, 10, 29), Decimal("100")),
                             PriceBar(date(2026, 10, 30), Decimal("101"))]}
    holdings = [_h("LYC", "1000"), _h("LYC", "600"), _h("VAS", "10")]
    ((key, line),) = price_moves(holdings, bars, 1, set(), TODAY)
    assert line.startswith("LYC down 8.3% on Fri 30 Oct") and "1600 units" in line
    assert price_moves(holdings, bars, 1, {key}, TODAY) == []
    ((_, weekly),) = price_moves(holdings, bars, 7, set(), TODAY)  # 13 -> 11 is -15.4%
    assert "down 15.4% in the week to" in weekly
    stale = {("LYC", "ASX"): [PriceBar(date(2026, 9, 1), Decimal(1)),
                              PriceBar(date(2026, 9, 2), Decimal(2))]}
    assert price_moves(holdings, stale, 1, set(), TODAY) == []


def test_deadline_reminders():
    soon = Category(id=uuid4(), user_id=USER_ID, name="House", target_date=date(2027, 3, 1))
    far = Category(id=uuid4(), user_id=USER_ID, name="Retire", target_date=date(2040, 1, 1))
    assert months_until(TODAY, soon.target_date) == 3
    ((key, cat, title, body),) = deadline_reminders([soon, far], {}, "AUD", set(), TODAY)
    assert (key, cat, title) == (f"deadline:{soon.id}:6", soon, "Less than 6 months to go")
    assert "01/03/2027" in body
    assert deadline_reminders([soon], {}, "AUD", {key}, TODAY) == []


def _build(holdings, old, categories=(GOAL,)):
    provider = FakeProvider(price_everything=True)

    def history(symbols, currencies, base, start, end):
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        return {s: [PriceBar(d, Decimal(100)) for d in days] for s in symbols}, {}

    return build_notifications(categories=list(categories), goal_prefs={}, holdings=holdings,
                               cash_accounts=[], inv=None, base="AUD", old_snapshots=old,
                               history_fn=history, today=TODAY, weekly=False,
                               **dict(zip(["quotes_fn", "fx_fn", "fundamentals_fn"],
                                          provider_fns(provider), strict=True)))


def test_build_notifications_baseline_then_changes():
    holding = _h("VAS", "50")
    holding = holding.model_copy(update={"category_id": GOAL.id})
    first = _build([holding], {})
    assert first.baseline and first.messages == []
    assert set(first.snapshots) == {str(GOAL.id), PORTFOLIO}

    # Nothing changed: nothing to say.
    again = _build([holding], first.snapshots)
    assert not again.baseline and again.messages == []

    # A goal's last snapshot had different picks: one notification for that goal.
    old = dict(first.snapshots)
    old[str(GOAL.id)] = {"picks": {"VAS:ASX": "100"}, "findings": {}, "sent": []}
    changed = _build([holding], old)
    (m,) = [m for m in changed.messages if m.category_id == GOAL.id]
    assert m.kind == "recommendation" and "No longer suggested" not in m.body
    assert "VAS (ASX)" in m.body


# AI picks and the watchlist -------------------------------------------------------

def _pick(ticker, action, conviction="high"):
    from share_recommender.core.picker import PickRow

    return PickRow(str(GOAL.id), ticker, "ASX", f"{ticker} Ltd", "share", action, conviction, 1,
                   70.0, None, f"{ticker} has grown profits. More detail here.", "Risky.", {},
                   "screen")


def _ai(*picks):
    return snapshot(_rec(VAS="100"), [], picks=list(picks))


def test_ai_baseline_sends_nothing():
    old = snapshot(_rec(VAS="100"), [])  # from before the picker existed: no "ai" key
    change = diff(old, _ai(_pick("CBA", "buy")))
    assert not change.due and change.ai_new == []


def test_new_ai_buy_and_hold_to_review():
    old = _ai(_pick("CBA", "watch"), _pick("BHP", "hold"), _pick("WES", "buy"))
    change = diff(old, _ai(_pick("CBA", "buy"), _pick("BHP", "review"), _pick("WES", "buy"),
                           _pick("LOW", "buy", "low")))
    assert change.due and [p["label"] for p in change.ai_new] == ["CBA (ASX)"]
    assert [p["label"] for p in change.ai_review] == ["BHP (ASX)"]
    m = goal_message(GOAL, change)
    assert m.kind == "ai_pick"
    assert "New AI pick: CBA (ASX), high conviction." in m.body
    assert "The AI picker suggests reviewing BHP (ASX)." in m.body
    subject, text, html = compose_email([m], "Ian", "http://app")
    assert "AI PICKS" in text and "buy idea CBA (ASX) (high conviction). CBA has grown " \
        "profits." in text
    assert text.index("Picks are generated by AI") < text.index("General information")
    assert "<h3 style='margin:20px 0 6px'>AI picks</h3>" in html


def test_a_dropped_ai_pick_sends_nothing():
    change = diff(_ai(_pick("CBA", "buy")), _ai())
    assert not change.due


def test_ai_and_rule_changes_together_are_a_recommendation():
    old = snapshot(_rec(VAS="100"), [], picks=[])
    new = snapshot(_rec(VAS="60", VGS="40"), [], picks=[_pick("CBA", "buy")])
    assert goal_message(GOAL, diff(old, new)).kind == "recommendation"


def _w(ticker="CBA", below=None, above=None, exchange="ASX"):
    from share_recommender.core.models import WatchlistItem

    return WatchlistItem(id=uuid4(), user_id=USER_ID, ticker=ticker, exchange=exchange,
                         alert_below=Decimal(below) if below else None,
                         alert_above=Decimal(above) if above else None)


def test_watchlist_alerts_below_above_once_and_rearm():
    from share_recommender.core.notifications import watchlist_alerts

    friday = date(2026, 10, 30)
    closes = {("CBA", "ASX"): PriceBar(friday, Decimal("118.2")),
              ("NVDA", "NASDAQ"): PriceBar(friday, Decimal("210"))}
    items = [_w("CBA", below="120"), _w("NVDA", above="200", exchange="NASDAQ"),
             _w("WES", below="50")]  # no price: nothing
    alerts = watchlist_alerts(items, closes, set(), TODAY)
    assert alerts == [
        ("watch:CBA:ASX:below:120", "CBA fell to A$118.20, at or below your A$120 alert"),
        ("watch:NVDA:NASDAQ:above:200", "NVDA rose to US$210.00, at or above your US$200 alert")]
    sent = {k for k, _ in alerts}
    assert watchlist_alerts(items, closes, sent, TODAY) == []          # once per level
    moved = [_w("CBA", below="119"), _w("NVDA", above="200", exchange="NASDAQ")]
    assert [k for k, _ in watchlist_alerts(moved, closes, sent, TODAY)] == [
        "watch:CBA:ASX:below:119"]                                     # a new level re-arms
    stale = {("CBA", "ASX"): PriceBar(date(2026, 10, 20), Decimal("100"))}
    assert watchlist_alerts(items, stale, set(), TODAY) == []


def test_watchlist_price_moves_and_message():
    from share_recommender.core.notifications import watchlist_message

    bars = {("NVDA", "NASDAQ"): [PriceBar(date(2026, 10, 23), Decimal("100")),
                                 PriceBar(date(2026, 10, 30), Decimal("111.2"))]}
    ((key, line),) = price_moves([_w("NVDA", exchange="NASDAQ")], bars, 7, set(), TODAY)
    assert line == "NVDA up 11.2% in the week to Fri 30 Oct"  # no units on a watchlist item
    m = watchlist_message([("watch:CBA:ASX:below:120", "CBA fell to A$118.20, at or below "
                            "your A$120 alert")], [(key, line)])
    assert m.kind == "watchlist" and m.body == (
        "CBA fell to A$118.20, at or below your A$120 alert. "
        "NVDA up 11.2% in the week to Fri 30 Oct.")


def test_build_notifications_with_watchlist_and_picks():
    first = _build([], {})
    item = _w("ZZZ", above="50")  # the fake history is 100 every day
    out = build_notifications(
        categories=[GOAL], goal_prefs={}, holdings=[], cash_accounts=[], inv=None, base="AUD",
        old_snapshots=first.snapshots | {str(GOAL.id): first.snapshots[str(GOAL.id)] | {"ai": {}}},
        history_fn=lambda s, c, b, start, end: ({k: [PriceBar(TODAY, Decimal(100))] for k in s},
                                                {}),
        today=TODAY, weekly=False, watchlist=[item], picks=[_pick("CBA", "buy")],
        **dict(zip(["quotes_fn", "fx_fn", "fundamentals_fn"],
                   provider_fns(FakeProvider(price_everything=True)), strict=True)))
    kinds = {m.kind for m in out.messages}
    assert {"watchlist", "ai_pick"} <= kinds
    assert "watch:ZZZ:ASX:above:50" in out.snapshots[PORTFOLIO]["sent"]
    assert out.snapshots[str(GOAL.id)]["ai"] == {"CBA:ASX": "buy"}
