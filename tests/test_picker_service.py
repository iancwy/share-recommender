"""The picker service end to end, with a fake pool, provider, history and Claude."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from share_recommender.ai.client import AIUnavailable
from share_recommender.core.models import Category, Holding, RiskLevel, WatchlistItem
from share_recommender.core.preferences import CategoryPreferences, InvestorPreferences
from share_recommender.data.base import Candidate, Fundamentals
from share_recommender.picker_service import PickerNotReady, run_picker
from share_recommender.recommendations import provider_fns
from tests.fakes import USER_ID, FakeProvider, FakeRepository, StubRanker, rising_history

TODAY = date(2026, 11, 9)
NOW = datetime(2026, 11, 9, 8, tzinfo=UTC)
D = Decimal


def _cand(ticker, cap="20e9"):
    return Candidate(ticker, "ASX", f"{ticker} Ltd", "share", D(30), D(cap), avg_volume=D(1e6))


POOL = [_cand(t) for t in ("ZAA", "ZBB", "ZCC", "ZDD")] + [_cand("ZTINY", "1e7"),
                                                           _cand("ZBOND")]
FUNDAMENTALS = {c.key: Fundamentals(sector=f"Sector {c.ticker}", industry="Banks",
                                    name=c.name, quote_type="EQUITY",
                                    dividend_yield=D("0.04"), return_on_equity=D("0.1"))
                for c in POOL}
FUNDAMENTALS[("ZBOND", "ASX")] = Fundamentals(quote_type="ETF", name="Z Bond Fund",
                                              fund_category="Bond")


@pytest.fixture
def world():
    house = Category(id=uuid4(), user_id=USER_ID, name="House", risk_level=RiskLevel.MEDIUM)
    later = Category(id=uuid4(), user_id=USER_ID, name="Later")  # no answers yet
    held = Holding(id=uuid4(), user_id=USER_ID, ticker="ZHELD", exchange="ASX", units=D(10),
                   buy_price=D(20), buy_date=date(2025, 1, 1), category_id=house.id)
    watch = WatchlistItem(id=uuid4(), user_id=USER_ID, ticker="ZWAT", exchange="ASX")
    provider = FakeProvider(price_everything=True, fundamentals=FUNDAMENTALS)
    return {"house": house, "later": later, "held": held, "watch": watch, "provider": provider}


ASX_ONLY = InvestorPreferences(markets=["ASX"])


def _run(world, rank=None, inv=ASX_ONLY, **kw):
    quotes_fn, fx_fn, fundamentals_fn = provider_fns(world["provider"])
    args = {
        "categories": [world["house"], world["later"]],
        "goal_prefs": {world["house"].id: CategoryPreferences(horizon_years=15)},
        "holdings": [world["held"]], "cash_accounts": [], "watchlist": [world["watch"]],
        "inv": inv, "base": "AUD", "quotes_fn": quotes_fn, "fx_fn": fx_fn,
        "fundamentals_fn": fundamentals_fn, "history_fn": rising_history,
        "pool_fn": lambda markets: [c for c in POOL if c.exchange in markets],
        "rank_fn": rank or StubRanker(), "today": TODAY, "trigger": "manual", "now": NOW,
    } | kw
    return run_picker(**args)


def test_a_run_end_to_end(world):
    rank = StubRanker()
    result = _run(world, rank)
    run, picks = result.run, result.picks
    assert run.status == "ok" and run.error is None
    assert run.summary["skipped"] == ["Later"]
    goal = run.summary["goals"][str(world["house"].id)]
    assert goal["summary"] == "Picks for House."
    assert (run.input_tokens, run.output_tokens, run.model) == (5000, 1500, "claude-opus-5-5")
    assert run.pool_size >= 6 and run.shortlisted > 0

    actions = {p.ticker: p.action for p in picks}
    assert actions["ZHELD"] == "hold" and actions["ZWAT"] == "watch"
    assert sum(1 for a in actions.values() if a == "buy") == 2
    assert all(p.goal_id == str(world["house"].id) for p in picks)

    shortlist = result.shortlists[str(world["house"].id)]
    keys = {i.key[0] for i in shortlist.items}
    assert "ZTINY" not in keys and "ZBOND" not in keys  # too small, a bond fund
    assert len(rank.payloads) == 1 and [g["name"] for g in rank.payloads[0]["goals"]] == ["House"]


def test_unchanged_inputs_reuse_the_last_run(world):
    first = _run(world)
    saved = first.run.model_copy(update={"id": uuid4(), "created_at": NOW - timedelta(hours=2)})
    rank = StubRanker()
    again = _run(world, rank, latest=saved)
    assert again.reused and again.run is saved
    assert rank.payloads == []  # Claude wasn't asked again


def test_reuse_only_within_a_day(world):
    first = _run(world)
    old = first.run.model_copy(update={"id": uuid4(), "created_at": NOW - timedelta(hours=30)})
    assert not _run(world, latest=old).reused


def test_a_claude_failure_is_a_failed_run_with_the_message(world):
    result = _run(world, StubRanker(AIUnavailable("Claude is busy (rate limited).")))
    assert result.run.status == "failed" and result.picks == []
    assert result.run.error == "Claude is busy (rate limited)."
    assert result.shortlists  # the screen still ran, so the page can show it unranked


def test_a_data_failure_is_a_failed_run(world):
    def broken_pool(markets):
        raise ConnectionError("Yahoo is down")
    result = _run(world, pool_fn=broken_pool)
    assert result.run.status == "failed" and "Yahoo is down" in result.run.error


def test_no_interview_answers(world):
    with pytest.raises(PickerNotReady):
        _run(world, inv=None)
    result = _run(world, goal_prefs={})
    assert result.run.status == "failed" and "goal questions" in result.run.error


def test_saving_and_the_daily_count():
    from share_recommender.core.picker import PickRun

    repo = FakeRepository()
    for _ in range(12):
        repo.save_pick_run(PickRun(trigger="manual"), [])
    repo.prune_pick_runs(keep=10)
    assert len(repo.pick_runs) == 10 and repo.pick_runs_today() == 12
